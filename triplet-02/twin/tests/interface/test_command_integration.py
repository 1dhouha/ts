import http.client
import json
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("TWIN_ALLOW_INSECURE_DEV_TOKENS", "1")

from mcp.shared.context import RequestContext
from mcp.server.lowlevel.server import request_ctx
from starlette.requests import Request

from city_live.runtime import LiveCity
from city_dashboard.server import DashboardBridge
from twin_interface.auth import DEFAULT_TOKENS, Role
from twin_interface.facade import TwinFacade
from twin_interface.mcp_server.server import create_mcp_server
from twin_interface.rest.router import handle_rest_request


class SharedCommandIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.city = LiveCity("shared-command-test")
        self.addCleanup(self.city.close)
        with self.city._condition:
            self.city._state = "RUNNING"
        self.facade = TwinFacade(self.city)
        self.bridge = DashboardBridge(
            self.city, "integration-test-passphrase!", port=0
        )
        self.bridge.start()
        self.addCleanup(self.bridge.close)
        self.mcp = create_mcp_server(self.facade)
        self.brain_token = DEFAULT_TOKENS[Role.BRAIN]
        self.guardian_token = DEFAULT_TOKENS[Role.GUARDIAN]
        self.cookie = self._dashboard_login()

    def _dashboard_request(self, method, path, body=None, cookie=None):
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.bridge.port, timeout=3
        )
        headers = {"Origin": self.bridge.url}
        if cookie:
            headers["Cookie"] = cookie
        if body is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(body)
        try:
            connection.request(method, path, body, headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def _dashboard_login(self):
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.bridge.port, timeout=3
        )
        try:
            connection.request(
                "POST",
                "/api/session",
                json.dumps({"secret": "integration-test-passphrase!"}),
                {
                    "Origin": self.bridge.url,
                    "Content-Type": "application/json",
                },
            )
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            response.read()
            return response.getheader("Set-Cookie").split(";", 1)[0]
        finally:
            connection.close()

    def _dashboard_command(self, command_id, device_id, requested):
        status = self.city.status()
        return self._dashboard_request(
            "POST",
            "/api/operator/command",
            {
                "command_id": command_id,
                "run_id": status["run_id"],
                "expected_revision": status["revision"],
                "kind": "device",
                "args": {"device_id": device_id, "requested": requested},
            },
            cookie=self.cookie,
        )

    def _dashboard_canonical_command(
        self, command_id, device_id, command, params, *, context=None
    ):
        status = self.city.status()
        context = context or {}
        return self._dashboard_request(
            "POST",
            "/api/operator/command",
            {
                "command_id": command_id,
                "run_id": context.get("run_id", status["run_id"]),
                "expected_revision": context.get(
                    "expected_revision", status["revision"]
                ),
                "kind": "device",
                "args": {
                    "device_id": device_id,
                    "command": command,
                    "params": params,
                },
            },
            cookie=self.cookie,
        )

    def _rest_command(
        self, request_id, device_id, command, params, *, context=None
    ):
        return handle_rest_request(
            self.facade,
            "POST",
            "/api/v1/commands",
            headers={"Authorization": f"Bearer {self.brain_token}"},
            body={
                "request_id": request_id,
                "device_id": device_id,
                "command": command,
                "params": params,
                "context": context,
            },
        )

    async def _mcp_command(
        self, request_id, device_id, command, params, *, context=None
    ):
        request = Request(
            {
                "type": "http",
                "method": "POST",
                "scheme": "http",
                "path": "/mcp/",
                "raw_path": b"/mcp/",
                "query_string": b"",
                "headers": [
                    (b"authorization", f"Bearer {self.brain_token}".encode())
                ],
                "server": ("localhost", 8000),
                "client": ("127.0.0.1", 12345),
            }
        )
        context_token = request_ctx.set(
            RequestContext(
                request_id="command-integration",
                meta=None,
                session=None,
                lifespan_context=None,
                request=request,
            )
        )
        try:
            _, metadata = await self.mcp.call_tool(
                "send_actuator_command",
                {
                    "device_id": device_id,
                    "command": command,
                    "params": params,
                    "request_id": request_id,
                    "context": context,
                },
            )
        finally:
            request_ctx.reset(context_token)
        return metadata.get("result", metadata)

    def _valve_opening(self):
        return self.city.diagnostics()["physical_state"]["valve"][
            "effective_opening_fraction"
        ]

    async def test_real_interfaces_share_livecity_execution_and_effect(self):
        self.assertIs(self.bridge._owner, self.city)
        self.assertIs(self.facade.city, self.city)

        with patch.object(
            self.city,
            "execute_actuator_command",
            wraps=self.city.execute_actuator_command,
        ) as execute:
            dashboard_status, dashboard = self._dashboard_command(
                "dashboard-valve-command", "A-VALVE-01", 0.65
            )
            rest_status, rest = self._rest_command(
                "rest-valve-command",
                "A-VALVE-01",
                "set_opening",
                {"open_pct": 65},
            )
            mcp = await self._mcp_command(
                "mcp-valve-command",
                "A-VALVE-01",
                "set_opening",
                {"open_pct": 65},
            )

        self.assertEqual(dashboard_status, 200)
        self.assertEqual(rest_status, 200)
        self.assertTrue(rest["ok"])
        self.assertTrue(mcp["ok"])
        dashboard_result = dashboard["data"]["result"]
        self.assertEqual(dashboard_result["status"], "COMPLETED")
        self.assertEqual(rest["data"]["status"], "APPLIED")
        self.assertEqual(mcp["data"]["status"], "APPLIED")
        self.assertEqual(execute.call_count, 3)
        self.assertEqual(self._valve_opening(), 0.65)

        first_effect = self.city._effect_counter - 2
        self.assertEqual(
            dashboard_result["effect_id"], f"live-{first_effect:010d}"
        )
        self.assertEqual(
            rest["data"]["effect_id"], f"live-{first_effect + 1:010d}"
        )
        self.assertEqual(
            mcp["data"]["effect_id"], f"live-{first_effect + 2:010d}"
        )

        dashboard_frame_status, dashboard_frame = self._dashboard_request(
            "GET", "/api/operator/frame", cookie=self.cookie
        )
        self.assertEqual(dashboard_frame_status, 200)
        self.assertEqual(
            dashboard_frame["data"]["diagnostics"]["physical_state"]["valve"][
                "effective_opening_fraction"
            ],
            0.65,
        )

    async def test_cross_interface_idempotency_uses_livecity_receipt(self):
        context = self.city.status()
        execution_context = {
            "run_id": context["run_id"],
            "expected_revision": context["revision"],
        }
        with patch.object(
            self.city,
            "execute_actuator_command",
            wraps=self.city.execute_actuator_command,
        ) as execute:
            dashboard_status, dashboard = self._dashboard_canonical_command(
                "cross-interface-idempotency",
                "PRT-PMP-01",
                "set_speed",
                {"speed_pct": 30},
                context=execution_context,
            )
            rest_status, rest = self._rest_command(
                "cross-interface-idempotency",
                "PRT-PMP-01",
                "set_speed",
                {"speed_pct": 30},
                context=execution_context,
            )
            mcp = await self._mcp_command(
                "cross-interface-idempotency",
                "PRT-PMP-01",
                "set_speed",
                {"speed_pct": 30},
                context=execution_context,
            )

        self.assertEqual(dashboard_status, 200)
        self.assertEqual(rest_status, 200)
        self.assertTrue(rest["ok"])
        self.assertTrue(mcp["ok"])
        dashboard_receipt = dashboard["data"]["result"]
        self.assertEqual(dashboard_receipt["effect_id"], rest["data"]["effect_id"])
        self.assertEqual(dashboard_receipt["effect_id"], mcp["data"]["effect_id"])
        self.assertEqual(
            dashboard_receipt["effective_revision"],
            rest["data"]["effective_revision"],
        )
        self.assertEqual(
            dashboard_receipt["effective_revision"],
            mcp["data"]["effective_revision"],
        )
        self.assertEqual(execute.call_count, 3)
        self.assertEqual(self.city.status()["revision"], context["revision"] + 1)
        self.assertEqual(self.city._effect_counter, 1)

        for interface in ("rest", "mcp", "dashboard"):
            if interface == "rest":
                _, conflict = self._rest_command(
                    "cross-interface-idempotency",
                    "PRT-PMP-01",
                    "set_speed",
                    {"speed_pct": 70},
                    context=execution_context,
                )
                self.assertEqual(
                    conflict["error"]["code"], "IDEMPOTENCY_CONFLICT"
                )
            elif interface == "mcp":
                conflict = await self._mcp_command(
                    "cross-interface-idempotency",
                    "PRT-PMP-01",
                    "set_speed",
                    {"speed_pct": 70},
                    context=execution_context,
                )
                self.assertEqual(
                    conflict["error"]["code"], "IDEMPOTENCY_CONFLICT"
                )
            else:
                status, conflict = self._dashboard_canonical_command(
                    "cross-interface-idempotency",
                    "PRT-PMP-01",
                    "set_speed",
                    {"speed_pct": 70},
                    context=execution_context,
                )
                self.assertEqual(status, 409)
                self.assertEqual(
                    conflict["error"]["code"], "IDEMPOTENCY_CONFLICT"
                )

        self.assertEqual(self.city._effect_counter, 1)
        self.assertEqual(self.city.status()["revision"], context["revision"] + 1)

    async def test_invalid_commands_and_unknown_devices_match_across_interfaces(self):
        dashboard_status, dashboard_invalid = self._dashboard_command(
            "dashboard-invalid-command", "A-VALVE-01", 1.5
        )
        rest_status, rest_invalid = self._rest_command(
            "rest-invalid-command",
            "A-VALVE-01",
            "set_opening",
            {"open_pct": 150},
        )
        mcp_invalid = await self._mcp_command(
            "mcp-invalid-command",
            "A-VALVE-01",
            "set_opening",
            {"open_pct": 150},
        )

        self.assertEqual(dashboard_status, 400)
        self.assertEqual(rest_status, 400)
        self.assertEqual(
            dashboard_invalid["error"]["code"], "INVALID_PARAM"
        )
        self.assertEqual(rest_invalid["error"]["code"], "INVALID_PARAM")
        self.assertEqual(mcp_invalid["error"]["code"], "INVALID_PARAM")

        dashboard_status, dashboard_unknown = self._dashboard_command(
            "dashboard-unknown-device", "UNKNOWN-ACTUATOR", 0.5
        )
        rest_status, rest_unknown = self._rest_command(
            "rest-unknown-device",
            "UNKNOWN-ACTUATOR",
            "set_opening",
            {"open_pct": 50},
        )
        mcp_unknown = await self._mcp_command(
            "mcp-unknown-device",
            "UNKNOWN-ACTUATOR",
            "set_opening",
            {"open_pct": 50},
        )

        self.assertEqual(dashboard_status, 404)
        self.assertEqual(rest_status, 404)
        self.assertEqual(
            dashboard_unknown["error"]["code"], "DEVICE_NOT_FOUND"
        )
        self.assertEqual(rest_unknown["error"]["code"], "DEVICE_NOT_FOUND")
        self.assertEqual(mcp_unknown["error"]["code"], "DEVICE_NOT_FOUND")

    async def test_invalid_state_and_quarantine_apply_to_all_interfaces(self):
        with self.city._condition:
            self.city._state = "ERROR"

        dashboard_status, dashboard_error = self._dashboard_command(
            "dashboard-invalid-state", "A-VALVE-01", 0.5
        )
        rest_status, rest_error = self._rest_command(
            "rest-invalid-state",
            "A-VALVE-01",
            "set_opening",
            {"open_pct": 50},
        )
        mcp_error = await self._mcp_command(
            "mcp-invalid-state",
            "A-VALVE-01",
            "set_opening",
            {"open_pct": 50},
        )

        self.assertEqual(dashboard_status, 409)
        self.assertEqual(rest_status, 409)
        self.assertEqual(dashboard_error["error"]["code"], "INVALID_STATE")
        self.assertEqual(rest_error["error"]["code"], "INVALID_STATE")
        self.assertEqual(mcp_error["error"]["code"], "INVALID_STATE")

        with self.city._condition:
            self.city._state = "RUNNING"
        isolated = self.facade.isolate_device(
            self.guardian_token, "V-12", "integration quarantine"
        )
        self.assertTrue(isolated["ok"])

        dashboard_status, dashboard_error = self._dashboard_command(
            "dashboard-quarantined-command", "A-VALVE-01", 0.5
        )
        rest_status, rest_error = self._rest_command(
            "rest-quarantined-command",
            "A-VALVE-01",
            "set_opening",
            {"open_pct": 50},
        )
        mcp_error = await self._mcp_command(
            "mcp-quarantined-command",
            "A-VALVE-01",
            "set_opening",
            {"open_pct": 50},
        )

        self.assertEqual(dashboard_status, 403)
        self.assertEqual(rest_status, 403)
        self.assertEqual(
            dashboard_error["error"]["code"], "GUARDIAN_RESTRICTED"
        )
        self.assertEqual(rest_error["error"]["code"], "GUARDIAN_RESTRICTED")
        self.assertEqual(mcp_error["error"]["code"], "GUARDIAN_RESTRICTED")

    async def test_pump_speed_and_signal_modes_are_real_shared_simulation_state(self):
        observed = []
        commands = (
            ("dashboard", 30),
            ("rest", 70),
            ("mcp", 30),
            ("dashboard", 0),
            ("rest", 100),
            ("dashboard", 50),
            ("mcp", 80),
        )
        for index, (interface, speed_pct) in enumerate(commands):
            params = {"speed_pct": speed_pct}
            if interface == "dashboard":
                status, response = self._dashboard_canonical_command(
                    f"pump-{interface}-{index}", "PRT-PMP-01", "set_speed", params
                )
                self.assertEqual(status, 200)
                self.assertEqual(response["data"]["result"]["status"], "APPLIED")
            elif interface == "rest":
                status, response = self._rest_command(
                    f"pump-{interface}-{index}", "PRT-PMP-01", "set_speed", params
                )
                self.assertEqual(status, 200)
                self.assertTrue(response["ok"])
            else:
                response = await self._mcp_command(
                    f"pump-{interface}-{index}", "PRT-PMP-01", "set_speed", params
                )
                self.assertTrue(response["ok"])

            snapshot = self.city._snapshot()
            fraction = speed_pct / 100
            observed.append(snapshot.physical_state.pump.effective_speed_fraction)
            self.assertEqual(
                snapshot.physical_state.pump.effective_enabled, speed_pct > 0
            )
            self.assertEqual(
                snapshot.physical_state.pump.effective_speed_fraction, fraction
            )
            self.assertAlmostEqual(
                snapshot.water.pump_capacity_m3_s,
                1.2 * fraction if speed_pct else 0,
            )
            self.assertAlmostEqual(
                snapshot.power.assets["P-03"].requested_demand_kw,
                10 * fraction,
            )

        self.assertEqual(observed, [0.3, 0.7, 0.3, 0.0, 1.0, 0.5, 0.8])

        for mode, expected_capacity in (
            ("normal", 0.5),
            ("priority", 0.75),
            ("stop", 0.0),
        ):
            status, response = self._rest_command(
                f"signal-mode-{mode}", "DTN-TLT-01", "set_mode", {"mode": mode}
            )
            self.assertEqual(status, 200)
            self.assertTrue(response["ok"])
            state = self.city._snapshot()
            self.assertEqual(state.physical_state.signals["J-D1"].effective_mode, mode)
            self.assertEqual(state.traffic["R-LOW"].capacity_veh_s, expected_capacity)

        for phase in ("green_ns", "green_ew"):
            status, response = self._rest_command(
                f"signal-phase-{phase}",
                "DTN-TLT-01",
                "set_phase",
                {"phase": phase},
            )
            self.assertEqual(status, 400)
            self.assertFalse(response["ok"])
            self.assertEqual(response["error"]["code"], "INVALID_PARAM")


if __name__ == "__main__":
    unittest.main()
