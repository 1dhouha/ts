import tempfile
import unittest
from unittest.mock import patch

from starlette.testclient import TestClient

from city_live.runtime import LiveCity
from twin_interface.facade import TwinFacade
from twin_interface.health import readiness_report
from twin_interface.mcp_server.server import create_mcp_server
from twin_interface.rest.router import handle_rest_request
from twin_observability.event_store import EventStore
from twin_observability.scenario_log import ScenarioLogger


class ServiceHealthTests(unittest.TestCase):
    def setUp(self):
        self.city = LiveCity("service-health-test")
        self.facade = TwinFacade(self.city)
        self.addCleanup(self.city.close)

    def test_rest_liveness_readiness_and_shutdown(self):
        status, live = handle_rest_request(self.facade, "GET", "/health/live")
        self.assertEqual(status, 200)
        self.assertEqual(live["data"]["status"], "UP")

        status, ready = handle_rest_request(self.facade, "GET", "/health/ready")
        self.assertEqual(status, 200)
        self.assertEqual(ready["data"]["status"], "DEGRADED")
        self.assertEqual(ready["data"]["checks"]["rest_gateway"]["status"], "UP")
        self.assertEqual(ready["data"]["checks"]["event_store"]["status"], "DEGRADED")

        self.city.close()
        status, down = handle_rest_request(self.facade, "GET", "/health")
        self.assertEqual(status, 503)
        self.assertFalse(down["ok"])
        self.assertEqual(down["data"]["status"], "DOWN")

    def test_rest_is_ready_when_optional_sinks_are_healthy(self):
        with tempfile.TemporaryDirectory() as log_dir:
            logger = ScenarioLogger(
                "health-test",
                self.city.status()["run_id"],
                log_dir=log_dir,
            )
            facade = TwinFacade(self.city, EventStore(), logger)
            try:
                status, response = handle_rest_request(
                    facade, "GET", "/health/ready"
                )
            finally:
                logger.close()

        self.assertEqual(status, 200)
        self.assertEqual(response["data"]["status"], "READY")
        self.assertEqual(response["data"]["checks"]["event_store"]["status"], "UP")
        self.assertEqual(response["data"]["checks"]["scenario_logger"]["status"], "UP")

    def test_rest_reports_uninitialized_and_core_failure(self):
        self.city._initialized = False
        status, not_initialized = handle_rest_request(
            self.facade, "GET", "/health/ready"
        )
        self.assertEqual(status, 503)
        self.assertEqual(not_initialized["data"]["checks"]["livecity"]["status"], "DOWN")

        self.city._initialized = True
        with patch.object(
            self.city, "_snapshot", side_effect=RuntimeError("private diagnostic")
        ):
            status, failed = handle_rest_request(
                self.facade, "GET", "/health/ready"
            )
        self.assertEqual(status, 503)
        self.assertNotIn("private diagnostic", repr(failed))

    def test_unconfigured_optional_observability_is_degraded_not_down(self):
        status, report = handle_rest_request(
            self.facade, "GET", "/health/ready"
        )

        self.assertEqual(status, 200)
        self.assertEqual(report["data"]["status"], "DEGRADED")
        self.assertEqual(
            report["data"]["checks"]["scenario_logger"]["status"], "DEGRADED"
        )

    def test_configured_but_unavailable_observability_is_down(self):
        store = EventStore()
        self.facade._event_store = store
        with patch.object(store, "health", return_value={"status": "DOWN"}):
            status, report = handle_rest_request(
                self.facade, "GET", "/health/ready"
            )

        self.assertEqual(status, 503)
        self.assertEqual(report["data"]["status"], "DOWN")
        self.assertEqual(
            report["data"]["checks"]["event_store"]["status"], "DOWN"
        )
        self.assertEqual(report["data"]["checks"]["livecity"]["status"], "UP")

    def test_rest_health_probe_does_not_mutate_simulation(self):
        before_status = self.city.status()
        before_diagnostics = self.city.diagnostics()
        before_effects = self.city._effect_counter
        before_requests = self.city._actuator_requests

        handle_rest_request(self.facade, "GET", "/health/live")
        handle_rest_request(self.facade, "GET", "/health/ready")
        handle_rest_request(self.facade, "GET", "/health")

        self.assertEqual(self.city.status(), before_status)
        self.assertEqual(self.city.diagnostics(), before_diagnostics)
        self.assertEqual(self.city._effect_counter, before_effects)
        self.assertIs(self.city._actuator_requests, before_requests)

    def test_mcp_health_routes_check_only_the_shared_runtime(self):
        server = create_mcp_server(self.facade)
        client = TestClient(server.streamable_http_app())
        before_status = self.city.status()
        before_diagnostics = self.city.diagnostics()
        before_effects = self.city._effect_counter
        before_requests = self.city._actuator_requests

        live = client.get("/health/live")
        self.assertEqual(live.status_code, 200)
        self.assertEqual(live.json()["status"], "UP")

        ready = client.get("/health/ready")
        self.assertEqual(ready.status_code, 200)
        self.assertEqual(ready.json()["status"], "DEGRADED")
        self.assertEqual(ready.json()["checks"]["mcp_service"]["status"], "UP")
        self.assertEqual(ready.json()["checks"]["scenario_logger"]["status"], "DEGRADED")
        self.assertNotIn("rest", ready.json()["checks"])

        with patch.object(
            self.city, "_snapshot", side_effect=RuntimeError("private diagnostic")
        ):
            unavailable = client.get("/health/ready")
        self.assertEqual(unavailable.status_code, 503)
        self.assertEqual(unavailable.json()["status"], "DOWN")
        self.assertNotIn("private diagnostic", unavailable.text)
        self.assertEqual(client.get("/health/live").status_code, 200)
        self.assertEqual(self.city.status(), before_status)
        self.assertEqual(self.city.diagnostics(), before_diagnostics)
        self.assertEqual(self.city._effect_counter, before_effects)
        self.assertIs(self.city._actuator_requests, before_requests)

    def test_dashboard_and_mcp_readiness_do_not_depend_on_rest(self):
        dashboard = readiness_report(
            "dashboard",
            self.city,
            required_checks={"dashboard_bridge": True},
        )
        mcp = self.facade.health_report(
            "mcp",
            service_checks={"mcp_service": True},
        )

        self.assertEqual(dashboard["status"], "READY")
        self.assertEqual(mcp["status"], "DEGRADED")
        self.assertNotIn("rest", dashboard["checks"])
        self.assertNotIn("rest", mcp["checks"])


if __name__ == "__main__":
    unittest.main()
