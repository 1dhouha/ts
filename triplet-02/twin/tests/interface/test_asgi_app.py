"""Network-facing tests for the unified REST and MCP ASGI application."""
from __future__ import annotations

import asyncio
import os
import secrets
import tempfile
import unittest
from unittest.mock import patch

import httpx
import uvicorn
from fastapi.testclient import TestClient
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from city_live.runtime import LiveCity
from twin_interface.auth import Role
from twin_interface.mcp_server.server import create_mcp_server
import twin_app
from twin_app import MCP_MOUNT_PATH, create_app, main


class UnifiedAsgiAppTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="twin-asgi-")
        self.tokens = {
            role: secrets.token_urlsafe(36)
            for role in ("BRAIN", "GUARDIAN", "OPERATOR", "EVALUATOR")
        }
        self.env = patch.dict(os.environ, {
            "TWIN_ALLOW_INSECURE_DEV_TOKENS": "0",
            **{f"TWIN_TOKEN_{role}": token for role, token in self.tokens.items()},
            "TWIN_ALLOWED_HOSTS": "testserver,localhost,127.0.0.1",
            "TWIN_DASHBOARD_SECRET": "test-dashboard-passphrase-for-asgi!",
            "TWIN_ALLOWED_ORIGINS": "",
            "TWIN_LOG_DIR": self.temp_dir.name,
            "TWIN_RUN_ID": "asgi-integration-test",
            "PORT": "8765",
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.app = create_app()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_dashboard_operator_and_brain_rest_share_the_deployed_city_owner(self):
        with TestClient(self.app) as client:
            origin = "http://testserver"
            self.assertEqual(client.get("/").status_code, 200)
            for asset in ("/styles.css", "/app.js", "/api.js", "/map-layout.json", "/city-map.svg"):
                with self.subTest(asset=asset):
                    self.assertEqual(client.get(asset).status_code, 200)
            self.assertEqual(client.get("/api/operator/frame").status_code, 401)
            report = client.get("/api/report/frame")
            self.assertEqual(report.status_code, 200)
            self.assertEqual(report.json()["data"]["visibility"], "REPORTED_ONLY")

            login = client.post(
                "/api/session",
                json={"secret": os.environ["TWIN_DASHBOARD_SECRET"]},
                headers={"Origin": origin},
            )
            self.assertEqual(login.status_code, 200, login.text)
            self.assertIn("HttpOnly", login.headers["set-cookie"])
            self.assertEqual(client.get("/api/report/frame?unexpected=1").status_code, 400)
            rejected_origin = client.post(
                "/api/logout", json={}, headers={"Origin": "https://attacker.example"}
            )
            self.assertEqual(rejected_origin.status_code, 403)

            status = client.get("/api/operator/frame")
            self.assertEqual(status.status_code, 200, status.text)
            frame = status.json()["data"]
            paused = client.post(
                "/api/operator/command",
                json={"command_id": "asgi-dashboard-pause", "run_id": frame["run_id"],
                      "expected_revision": None, "kind": "pause", "args": {}},
                headers={"Origin": origin},
            )
            self.assertEqual(paused.status_code, 200, paused.text)
            frame = client.get("/api/operator/frame").json()["data"]

            dashboard_write = client.post(
                "/api/operator/command",
                json={"command_id": "asgi-dashboard-valve", "run_id": frame["run_id"],
                      "expected_revision": frame["revision"], "kind": "device",
                      "args": {"device_id": "A-VALVE-01", "requested": 0.65}},
                headers={"Origin": origin},
            )
            self.assertEqual(dashboard_write.status_code, 200, dashboard_write.text)
            brain_token = os.environ["TWIN_TOKEN_BRAIN"]
            rest_state = client.get("/api/v1/state", headers={"Authorization": f"Bearer {brain_token}"})
            self.assertEqual(rest_state.status_code, 200)
            self.assertEqual(rest_state.json()["data"]["zones"]["PRT"]["valves"]["V-12"], "OPEN_65%")

            brain_write = client.post(
                "/api/v1/commands",
                json={"request_id": "asgi-brain-valve", "device_id": "V-12",
                      "command": "set_opening", "params": {"open_pct": 22},
                      "context": {"run_id": frame["run_id"]}},
                headers={"Authorization": f"Bearer {brain_token}"},
            )
            self.assertEqual(brain_write.status_code, 200, brain_write.text)
            operator_frame = client.get("/api/operator/frame").json()["data"]
            self.assertAlmostEqual(
                operator_frame["diagnostics"]["physical_state"]["valve"]["effective_opening_fraction"],
                0.22,
            )

            logged_out = client.post("/api/logout", json={}, headers={"Origin": origin})
            self.assertEqual(logged_out.status_code, 200)
            self.assertEqual(client.get("/api/operator/frame").status_code, 401)

    def test_dashboard_cookie_can_be_forced_secure_behind_tls_proxy(self):
        with patch.dict(os.environ, {"TWIN_DASHBOARD_COOKIE_SECURE": "true"}):
            with TestClient(self.app, base_url="https://testserver") as client:
                response = client.post(
                    "/api/session",
                    json={"secret": os.environ["TWIN_DASHBOARD_SECRET"]},
                    headers={"Origin": "https://testserver"},
                )
                self.assertEqual(response.status_code, 200, response.text)
                self.assertIn("secure", response.headers["set-cookie"].casefold())

    def test_openapi_describes_rest_query_and_json_body_parameters(self):
        schema = self.app.openapi()
        sensors = schema["paths"]["/api/v1/sensors"]["get"]
        self.assertEqual(
            {parameter["name"] for parameter in sensors["parameters"]},
            {"zone_id", "sensor_id", "sensor_type"},
        )
        events = schema["paths"]["/api/v1/events"]["get"]
        self.assertEqual(events["parameters"][0]["name"], "seq")
        command = schema["paths"]["/api/v1/commands"]["post"]
        command_schema = command["requestBody"]["content"]["application/json"]["schema"]
        self.assertEqual(
            set(command_schema["required"]),
            {"device_id", "command", "request_id"},
        )
        self.assertEqual(command_schema["properties"]["params"]["type"], "object")
        response_schema = command["responses"]["200"]["content"]["application/json"]["schema"]
        self.assertEqual(response_schema["properties"]["ok"]["type"], "boolean")
        self.assertNotIn("/api/operator/command", schema["paths"])

    def test_real_mcp_client_and_rest_share_one_runtime_and_facade(self):
        created_cities = []
        created_facades = []
        created_mcps = []
        starts = []
        closes = []
        logger_closes = []

        class TrackedScenarioLogger(twin_app.ScenarioLogger):
            def close(self):
                logger_closes.append(self)
                super().close()

        logger_patcher = patch("twin_app.ScenarioLogger", TrackedScenarioLogger)
        logger_patcher.start()
        self.addCleanup(logger_patcher.stop)

        def city_factory(run_id):
            city = LiveCity(run_id)
            original_start = city.start
            original_close = city.close

            def start():
                starts.append(city)
                return original_start()

            def close():
                closes.append(city)
                return original_close()

            city.start = start
            city.close = close
            created_cities.append(city)
            return city

        def facade_factory(city, **kwargs):
            from twin_interface.facade import TwinFacade
            facade = TwinFacade(city, **kwargs)
            created_facades.append(facade)
            return facade

        def mcp_factory(facade, **kwargs):
            server = create_mcp_server(facade, **kwargs)
            created_mcps.append((server, facade))
            return server

        app = create_app(
            city_factory=city_factory,
            facade_factory=facade_factory,
            mcp_factory=mcp_factory,
        )
        async def exercise_remote_clients():
            server = uvicorn.Server(uvicorn.Config(
                app,
                host="127.0.0.1",
                port=0,
                log_level="critical",
                access_log=False,
            ))
            server_task = asyncio.create_task(server.serve())
            try:
                for _ in range(1000):
                    if server.started:
                        break
                    if server_task.done():
                        await server_task
                        self.fail("Uvicorn exited before becoming ready.")
                    await asyncio.sleep(0.01)
                else:
                    self.fail("Uvicorn did not become ready.")

                self.assertEqual(len(created_cities), 1)
                self.assertEqual(len(created_facades), 1)
                self.assertEqual(len(created_mcps), 1)
                city = created_cities[0]
                facade = created_facades[0]
                mcp_server, mcp_facade = created_mcps[0]
                self.assertIs(facade.city, city)
                self.assertIs(mcp_facade, facade)
                self.assertIs(app.state.city, city)
                self.assertIs(app.state.facade, facade)
                self.assertEqual(starts, [city])
                self.assertIsNotNone(mcp_server.session_manager._task_group)
                self.assertTrue(city.status()["state"] in {"RUNNING", "READY"})
                self.assertEqual(mcp_server.settings.streamable_http_path, "/")
                self.assertEqual(mcp_server.settings.host, "0.0.0.0")
                self.assertEqual(mcp_server.settings.port, 8765)

                bound_port = server.servers[0].sockets[0].getsockname()[1]
                base_url = f"http://127.0.0.1:{bound_port}"
                brain_token = os.environ[f"TWIN_TOKEN_{Role.BRAIN.upper()}"]
                command = {
                    "request_id": "shared-rest-mcp-command",
                    "device_id": "A-VALVE-01",
                    "command": "set_opening",
                    "params": {"open_pct": 65},
                    "context": {
                        "run_id": city.status()["run_id"],
                    },
                }
                async with httpx.AsyncClient(base_url=base_url) as rest_client:
                    rest_response = await rest_client.post(
                        "/api/v1/commands",
                        json=command,
                        headers={"Authorization": f"Bearer {brain_token}"},
                    )
                self.assertEqual(rest_response.status_code, 200, rest_response.text)
                rest_data = rest_response.json()["data"]

                dashboard_client = httpx.AsyncClient(base_url=base_url)
                dashboard_login = await dashboard_client.post(
                    "/api/session",
                    json={"secret": os.environ["TWIN_DASHBOARD_SECRET"]},
                    headers={"Origin": base_url},
                )
                self.assertEqual(dashboard_login.status_code, 200, dashboard_login.text)
                operator_frame = await dashboard_client.get("/api/operator/frame")
                self.assertEqual(operator_frame.status_code, 200, operator_frame.text)
                operator_data = operator_frame.json()["data"]
                paused = await dashboard_client.post(
                    "/api/operator/command",
                    json={"command_id": "network-dashboard-pause",
                          "run_id": operator_data["run_id"], "expected_revision": None,
                          "kind": "pause", "args": {}},
                    headers={"Origin": base_url},
                )
                self.assertEqual(paused.status_code, 200, paused.text)
                operator_data = (await dashboard_client.get("/api/operator/frame")).json()["data"]
                dashboard_command = await dashboard_client.post(
                    "/api/operator/command",
                    json={"command_id": "network-dashboard-valve",
                          "run_id": operator_data["run_id"],
                          "expected_revision": operator_data["revision"],
                          "kind": "device",
                          "args": {"device_id": "A-VALVE-01", "requested": 0.44}},
                    headers={"Origin": base_url},
                )
                self.assertEqual(dashboard_command.status_code, 200, dashboard_command.text)

                async with streamablehttp_client(
                    f"{base_url}{MCP_MOUNT_PATH}",
                    headers={"Authorization": f"Bearer {brain_token}"},
                ) as (read_stream, write_stream, _):
                    try:
                        async with ClientSession(read_stream, write_stream) as session:
                            await session.initialize()
                            tools = await session.list_tools()
                            names = {tool.name for tool in tools.tools}
                            self.assertIn("get_sensor_readings", names)
                            self.assertIn("send_actuator_command", names)
                            command_tool = next(
                                tool for tool in tools.tools
                                if tool.name == "send_actuator_command"
                            )
                            self.assertNotIn("token", command_tool.inputSchema["properties"])
                            result = await session.call_tool(
                                "send_actuator_command",
                                command,
                            )
                            self.assertFalse(result.isError)
                            mcp_data = result.structuredContent
                            self.assertIsInstance(mcp_data, dict)
                            self.assertTrue(mcp_data["ok"])
                            self.assertEqual(mcp_data["data"]["effect_id"], rest_data["effect_id"])
                            mcp_state = await session.call_tool("get_city_state", {})
                            self.assertEqual(
                                mcp_state.structuredContent["data"]["zones"]["PRT"]["valves"]["V-12"],
                                "OPEN_44%",
                            )

                            mcp_command = {
                                **command,
                                "request_id": "shared-mcp-rest-command",
                                "params": {"open_pct": 22},
                                "context": {
                                    "run_id": city.status()["run_id"],
                                },
                            }
                            mcp_write = await session.call_tool(
                                "send_actuator_command",
                                mcp_command,
                            )
                            self.assertTrue(mcp_write.structuredContent["ok"])
                            async with httpx.AsyncClient(base_url=base_url) as rest_client:
                                rest_state = await rest_client.get(
                                    "/api/v1/state",
                                    headers={"Authorization": f"Bearer {brain_token}"},
                                )
                            self.assertEqual(rest_state.status_code, 200)
                            self.assertEqual(
                                rest_state.json()["data"]["zones"]["PRT"]["valves"]["V-12"],
                                "OPEN_22%",
                            )
                            operator_after_brain = await dashboard_client.get("/api/operator/frame")
                            self.assertEqual(operator_after_brain.status_code, 200)
                            self.assertAlmostEqual(
                                operator_after_brain.json()["data"]["diagnostics"][
                                    "physical_state"]["valve"]["effective_opening_fraction"],
                                0.22,
                            )
                            readings = await session.call_tool(
                                "get_sensor_readings",
                                {"sensor_type": "water_level"},
                            )
                            self.assertFalse(readings.isError)
                        self.assertIs(app.state.mcp_server, mcp_server)
                        self.assertIs(app.state.facade, facade)
                    finally:
                        await dashboard_client.aclose()
                        await read_stream.aclose()

                guardian_token = os.environ["TWIN_TOKEN_GUARDIAN"]
                async with streamablehttp_client(
                    f"{base_url}{MCP_MOUNT_PATH}",
                    headers={"Authorization": f"Bearer {guardian_token}"},
                ) as (read_stream, write_stream, _):
                    try:
                        async with ClientSession(read_stream, write_stream) as session:
                            await session.initialize()
                            denied = await session.call_tool(
                                "send_actuator_command",
                                command,
                            )
                            self.assertFalse(denied.isError)
                            self.assertFalse(denied.structuredContent["ok"])
                            self.assertEqual(
                                denied.structuredContent["error"]["code"],
                                "NOT_AUTHORIZED",
                            )
                            isolated = await session.call_tool(
                                "isolate_device",
                                {
                                    "device_id": "PRT-WLV-01",
                                    "reason": "integration test",
                                },
                            )
                            self.assertTrue(isolated.structuredContent["ok"])
                    finally:
                        await read_stream.aclose()

                evaluator_token = os.environ["TWIN_TOKEN_EVALUATOR"]
                async with streamablehttp_client(
                    f"{base_url}{MCP_MOUNT_PATH}",
                    headers={"Authorization": f"Bearer {evaluator_token}"},
                ) as (read_stream, write_stream, _):
                    try:
                        async with ClientSession(read_stream, write_stream) as session:
                            await session.initialize()
                            allowed = await session.call_tool("get_city_state", {})
                            self.assertTrue(allowed.structuredContent["ok"])
                            denied = await session.call_tool(
                                "send_actuator_command",
                                {
                                    **command,
                                    "request_id": "evaluator-mcp-command-denied",
                                },
                            )
                            self.assertEqual(
                                denied.structuredContent["error"]["code"],
                                "NOT_AUTHORIZED",
                            )
                    finally:
                        await read_stream.aclose()

                async with httpx.AsyncClient(base_url=base_url) as unauthenticated:
                    initialize = {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {
                            "protocolVersion": "2025-06-18",
                            "capabilities": {},
                            "clientInfo": {"name": "auth-check", "version": "1"},
                        },
                    }
                    missing = await unauthenticated.post("/mcp/", json=initialize)
                    missing_get = await unauthenticated.get("/mcp/")
                    invalid = await unauthenticated.post(
                        "/mcp/",
                        json=initialize,
                        headers={"Authorization": "Bearer invalid-credential"},
                    )
                    query_credential = await unauthenticated.post(
                        "/mcp/?access_token=not-accepted",
                        json=initialize,
                        headers={"Authorization": f"Bearer {brain_token}"},
                    )
                    self.assertEqual(missing.status_code, 401)
                    self.assertEqual(missing_get.status_code, 401)
                    self.assertEqual(invalid.status_code, 401)
                    self.assertNotIn("invalid-credential", invalid.text)
                    self.assertEqual(query_credential.status_code, 400)
            finally:
                server.should_exit = True
                await server_task

        asyncio.run(exercise_remote_clients())
        self.assertEqual(closes, [created_cities[0]])
        self.assertIsNone(created_mcps[0][0].session_manager._task_group)
        self.assertEqual(len(logger_closes), 1)

    def test_rest_header_auth_permissions_and_credential_rejection(self):
        with TestClient(self.app) as client:
            brain_token = os.environ["TWIN_TOKEN_BRAIN"]
            guardian_token = os.environ["TWIN_TOKEN_GUARDIAN"]
            evaluator_token = os.environ["TWIN_TOKEN_EVALUATOR"]

            missing = client.get("/api/v1/sensors")
            invalid = client.get(
                "/api/v1/sensors",
                headers={"Authorization": "Bearer invalid-credential"},
            )
            self.assertEqual(missing.status_code, 401)
            self.assertEqual(invalid.status_code, 401)
            self.assertNotIn("invalid-credential", invalid.text)

            denied = client.post(
                "/api/v1/isolate",
                json={"device_id": "PRT-WLV-01"},
                headers={"Authorization": f"Bearer {brain_token}"},
            )
            self.assertEqual(denied.status_code, 403)
            self.assertEqual(denied.json()["error"]["code"], "NOT_AUTHORIZED")

            denied = client.post(
                "/api/v1/commands",
                json={
                    "device_id": "A-VALVE-01",
                    "command": "set_opening",
                    "params": {"open_pct": 5},
                    "request_id": "guardian-command-denied",
                },
                headers={"Authorization": f"Bearer {guardian_token}"},
            )
            self.assertEqual(denied.status_code, 403)
            self.assertEqual(denied.json()["error"]["code"], "NOT_AUTHORIZED")

            query_token = client.get("/api/v1/sensors?token=not-accepted")
            self.assertEqual(query_token.status_code, 400)
            mixed_case_query_token = client.get("/api/v1/sensors?Access_Token=not-accepted")
            self.assertEqual(mixed_case_query_token.status_code, 400)
            body_token = client.post(
                "/api/v1/isolate",
                json={"device_id": "PRT-WLV-01", "token": guardian_token},
            )
            self.assertEqual(body_token.status_code, 400)
            nested_body_token = client.post(
                "/api/v1/isolate",
                json={
                    "device_id": "PRT-WLV-01",
                    "metadata": {"Authorization": f"Bearer {guardian_token}"},
                },
            )
            self.assertEqual(nested_body_token.status_code, 400)

            guardian_isolate = client.post(
                "/api/v1/isolate",
                json={"device_id": "PRT-WLV-01"},
                headers={"Authorization": f"Bearer {guardian_token}"},
            )
            self.assertEqual(guardian_isolate.status_code, 200, guardian_isolate.text)
            self.assertTrue(guardian_isolate.json()["ok"])

            evaluator_read = client.get(
                "/api/v1/sensors",
                headers={"Authorization": f"Bearer {evaluator_token}"},
            )
            evaluator_command = client.post(
                "/api/v1/commands",
                json={
                    "device_id": "A-VALVE-01",
                    "command": "set_opening",
                    "params": {"open_pct": 5},
                    "request_id": "evaluator-command-denied",
                },
                headers={"Authorization": f"Bearer {evaluator_token}"},
            )
            self.assertEqual(evaluator_read.status_code, 200)
            self.assertEqual(evaluator_command.status_code, 403)
            self.assertEqual(
                evaluator_command.json()["error"]["code"],
                "NOT_AUTHORIZED",
            )

            malformed = client.post("/api/v1/isolate", content=b"{")
            self.assertEqual(malformed.status_code, 400)
            non_object = client.post("/api/v1/isolate", json=["not", "an", "object"])
            self.assertEqual(non_object.status_code, 400)

            self.assertEqual(client.get("/health/live").status_code, 200)
            self.assertEqual(client.get("/health/ready").status_code, 200)
            openapi = client.get("/openapi.json").json()
            self.assertEqual(
                openapi["components"]["securitySchemes"]["BearerAuth"]["scheme"],
                "bearer",
            )
            self.assertEqual(
                openapi["paths"]["/api/v1/sensors"]["get"]["security"],
                [{"BearerAuth": []}],
            )

    def test_host_allowlist_is_applied_to_rest_and_mcp(self):
        brain_token = os.environ["TWIN_TOKEN_BRAIN"]
        with TestClient(self.app) as client:
            response = client.get(
                "/api/v1/sensors",
                headers={"Host": "attacker.example", "Authorization": f"Bearer {brain_token}"},
            )
            self.assertEqual(response.status_code, 400)
            forwarded_host = client.get(
                "/api/v1/sensors",
                headers={
                    "Host": "testserver",
                    "X-Forwarded-Host": "attacker.example",
                    "Authorization": f"Bearer {brain_token}",
                },
            )
            self.assertEqual(forwarded_host.status_code, 200)

            response = client.post(
                "/mcp/",
                headers={
                    "Host": "attacker.example",
                    "Authorization": f"Bearer {brain_token}",
                },
                json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
            )
            self.assertEqual(response.status_code, 400)

    def test_mcp_rejects_unapproved_origin(self):
        with patch.dict(os.environ, {"TWIN_ALLOWED_ORIGINS": "https://brain.example"}):
            app = create_app()
        brain_token = os.environ["TWIN_TOKEN_BRAIN"]
        with TestClient(app) as client:
            response = client.post(
                "/mcp/",
                headers={
                    "Authorization": f"Bearer {brain_token}",
                    "Origin": "https://attacker.example",
                },
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "clientInfo": {"name": "origin-check", "version": "1"},
                    },
                },
            )
            self.assertEqual(response.status_code, 403)

    def test_main_binds_to_public_host_and_configured_port(self):
        with patch.dict(os.environ, {
            "PORT": "8765",
            "TWIN_TRUSTED_PROXY_IPS": "10.0.0.0/8",
        }):
            with patch("uvicorn.run") as run:
                main()
        run.assert_called_once()
        self.assertEqual(run.call_args.kwargs["host"], "0.0.0.0")
        self.assertEqual(run.call_args.kwargs["port"], 8765)
        self.assertEqual(run.call_args.kwargs["workers"], 1)
        self.assertFalse(run.call_args.kwargs["access_log"])
        self.assertEqual(run.call_args.kwargs["forwarded_allow_ips"], "10.0.0.0/8")

    def test_main_rejects_invalid_public_port(self):
        for port in ("not-a-port", "0", "65536"):
            with self.subTest(port=port):
                with patch.dict(os.environ, {"PORT": port}):
                    with patch("uvicorn.run") as run:
                        with self.assertRaisesRegex(SystemExit, "PORT"):
                            main()
                run.assert_not_called()

    def test_missing_production_configuration_fails_startup(self):
        with patch.dict(os.environ, {
            "TWIN_ALLOW_INSECURE_DEV_TOKENS": "0",
            "TWIN_TOKEN_BRAIN": "",
            "TWIN_ALLOWED_HOSTS": "testserver",
        }):
            app = create_app()
            with self.assertRaisesRegex(RuntimeError, "TWIN_TOKEN_BRAIN"):
                with TestClient(app):
                    pass

    def test_low_diversity_role_token_fails_startup(self):
        with patch.dict(os.environ, {
            "TWIN_TOKEN_BRAIN": "a" * 64,
        }):
            app = create_app()
            with self.assertRaisesRegex(RuntimeError, "distinct characters"):
                with TestClient(app):
                    pass


if __name__ == "__main__":
    unittest.main()
