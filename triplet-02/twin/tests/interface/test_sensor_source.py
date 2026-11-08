"""Sensor source unification across LiveCity, Dashboard, and MCP."""
import asyncio
import http.client
import json
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("TWIN_ALLOW_INSECURE_DEV_TOKENS", "1")

from mcp.shared.context import RequestContext
from mcp.server.lowlevel.server import request_ctx
from starlette.requests import Request

from city_dashboard.frames import capture_operator_frame
from city_dashboard.server import DashboardBridge
from city_live.device_catalog import load_catalog
from city_live.registry import proposed_registry
from city_live.runtime import LiveCity
from twin_interface.auth import DEFAULT_TOKENS, Role
from twin_interface.facade import TwinFacade
from twin_interface.mcp_server.server import create_mcp_server
from twin_interface.rest.router import handle_rest_request


class SensorSourceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.city = LiveCity("test-sensor-source")
        self.facade = TwinFacade(self.city)
        self.mcp = create_mcp_server(self.facade)
        self.token = DEFAULT_TOKENS[Role.BRAIN]

    async def asyncTearDown(self):
        self.city.close()

    def _frame(self):
        return capture_operator_frame(self.city)

    def _dashboard_http_frame(self):
        secret = "unit-consistency-test-passphrase!"
        bridge = DashboardBridge(self.city, secret, port=0)
        bridge.start()
        connection = http.client.HTTPConnection(
            "127.0.0.1", bridge.port, timeout=3
        )
        try:
            connection.request(
                "POST",
                "/api/session",
                json.dumps({"secret": secret}),
                {"Origin": bridge.url, "Content-Type": "application/json"},
            )
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            cookie = response.getheader("Set-Cookie").split(";", 1)[0]
            response.read()

            connection.request(
                "GET",
                "/api/operator/frame",
                headers={"Origin": bridge.url, "Cookie": cookie},
            )
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            return json.loads(response.read())["data"]
        finally:
            connection.close()
            bridge.close()

    async def _mcp_readings(self):
        request = Request(
            {
                "type": "http",
                "method": "POST",
                "scheme": "http",
                "path": "/mcp/",
                "raw_path": b"/mcp/",
                "query_string": b"",
                "headers": [(b"authorization", f"Bearer {self.token}".encode())],
                "server": ("localhost", 8000),
                "client": ("127.0.0.1", 12345),
            }
        )
        context_token = request_ctx.set(
            RequestContext(
                request_id="sensor-source",
                meta=None,
                session=None,
                lifespan_context=None,
                request=request,
            )
        )
        try:
            _, meta = await self.mcp.call_tool("get_sensor_readings", {})
        finally:
            request_ctx.reset(context_token)
        response = meta.get("result", meta)
        self.assertTrue(response["ok"])
        return {row["sensor_id"]: row for row in response["data"]["readings"]}

    def test_catalog_matches_livecity_sensor_definitions(self):
        registry = {row.sensor_id: row for row in proposed_registry()}
        catalog = {row.sensor_id: row for row in load_catalog().sensors}
        self.assertEqual(set(registry), set(catalog))
        for sensor_id, row in registry.items():
            other = catalog[sensor_id]
            self.assertEqual(row.type, other.type)
            self.assertEqual(row.unit, other.unit)
            self.assertEqual(row.description, other.description)
            self.assertEqual(row.source, other.source)
            self.assertEqual(row.interval, other.interval)
            self.assertEqual(row.normal_limits, other.normal_limits)
            self.assertEqual(row.hard_limits, other.hard_limits)
            self.assertEqual(row.linked_device_ids, other.linked_device_ids)
            self.assertEqual(row.backup_ids, other.backup_ids)
            self.assertTrue(load_catalog().supported_sensor(sensor_id))

    async def test_all_sensor_units_and_values_match_dashboard_mcp_and_rest(self):
        for _ in range(60):
            self.city.step()

        definitions = {row.sensor_id: row for row in proposed_registry()}
        livecity = {
            sensor_id: self.city.sensor_compare(sensor_id)
            for sensor_id in definitions
        }
        mcp = await self._mcp_readings()
        rest_status, rest_response = handle_rest_request(
            self.facade,
            "GET",
            "/api/v1/sensors",
            headers={"Authorization": f"Bearer {self.token}"},
        )
        self.assertEqual(rest_status, 200)
        self.assertTrue(rest_response["ok"])
        rest = {
            row["sensor_id"]: row for row in rest_response["data"]["readings"]
        }
        dashboard_frame = await asyncio.to_thread(self._dashboard_http_frame)
        dashboard = {
            row["sensor_id"]: row for row in dashboard_frame["comparisons"]
        }

        self.assertEqual(set(definitions), set(dashboard))
        self.assertEqual(set(definitions), set(mcp))
        self.assertEqual(set(definitions), set(rest))
        for sensor_id, definition in definitions.items():
            reading = livecity[sensor_id]
            self.assertEqual(reading["unit"], definition.unit)
            for interface in (dashboard, mcp, rest):
                self.assertEqual(interface[sensor_id]["unit"], definition.unit)
                self.assertEqual(
                    interface[sensor_id]["reported_value"],
                    reading["reported_value"],
                )

        self.assertEqual(livecity["PRT-WLV-01"]["reported_value"], 50.0)
        self.assertEqual(livecity["PRT-FLW-01"]["reported_value"], 60.0)

    async def test_dashboard_and_mcp_share_livecity_readings(self):
        for _ in range(5):
            self.city.step()
        frame = self._frame()
        mcp = await self._mcp_readings()
        latest = {row["sensor_id"]: row for row in frame["comparisons"]}
        self.assertEqual(set(latest), set(mcp))
        for sensor_id, reading in latest.items():
            self.assertEqual(reading["reported_value"], mcp[sensor_id]["reported_value"])
            self.assertEqual(reading["unit"], mcp[sensor_id]["unit"])
            self.assertEqual(reading["status"], mcp[sensor_id]["status"])

        self.city.sensor_fault("PRT-WLV-01", "FALSE_READING", {"offset": 100.0})
        for _ in range(5):
            self.city.step()
        frame = self._frame()
        mcp = await self._mcp_readings()
        latest = {row["sensor_id"]: row for row in frame["comparisons"]}
        self.assertEqual(latest["PRT-WLV-01"]["reported_value"],
                         mcp["PRT-WLV-01"]["reported_value"])
        self.assertGreater(mcp["PRT-WLV-01"]["reported_value"], 50)

    async def test_dashboard_and_mcp_preserve_livecity_sensor_ids(self):
        for _ in range(60):
            self.city.step()
        frame = self._frame()
        registry_ids = {row.sensor_id for row in proposed_registry()}
        frame_ids = {row["sensor_id"] for row in frame["sensors"]}
        mcp_ids = set(await self._mcp_readings())
        self.assertEqual(frame["sensors"], self.city.sensors())
        self.assertEqual(frame_ids, registry_ids)
        self.assertEqual(mcp_ids, registry_ids)
        self.assertEqual(frame_ids, mcp_ids)

    def test_dashboard_bridge_reuses_livecity_owner(self):
        bridge = DashboardBridge(self.city, "test-only-passphrase-983!", port=0)
        self.addCleanup(bridge.close)
        self.assertIs(bridge._owner, self.city)
        self.assertIs(bridge._reports._reader, self.city.public)

    async def test_mcp_reuses_supplied_facade(self):
        with patch("twin_interface.mcp_server.server.TwinFacade") as factory:
            create_mcp_server(self.facade)
            factory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
