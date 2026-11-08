import http.client
import json
import unittest

from city_live.runtime import LiveCity
from city_dashboard.server import DashboardBridge


class DashboardHealthTests(unittest.TestCase):
    def setUp(self):
        self.city = LiveCity("dashboard-health-test")
        self.bridge = DashboardBridge(
            self.city, "health-test-passphrase!", port=0
        )
        self.bridge.start()
        self.addCleanup(self.bridge.close)

    def request(self, path):
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.bridge.port, timeout=3
        )
        try:
            connection.request(
                "GET",
                path,
                headers={"Origin": self.bridge.url},
            )
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def test_liveness_and_readiness_reflect_bridge_and_runtime(self):
        status, live = self.request("/health/live")
        self.assertEqual(status, 200)
        self.assertEqual(live["status"], "UP")

        status, ready = self.request("/health/ready")
        self.assertEqual(status, 200)
        self.assertEqual(ready["status"], "READY")
        self.assertEqual(ready["checks"]["dashboard_bridge"]["status"], "UP")

        self.city.close()
        status, down = self.request("/health/ready")
        self.assertEqual(status, 503)
        self.assertEqual(down["status"], "DOWN")
        self.assertEqual(self.request("/health/live")[0], 200)

    def test_not_started_or_shutting_down_bridge_is_not_ready(self):
        other = LiveCity("dashboard-not-started")
        bridge = DashboardBridge(
            other, "health-test-passphrase!", port=0
        )
        self.addCleanup(bridge.close)
        report = bridge.health_report()
        self.assertEqual(report["status"], "DOWN")
        self.assertEqual(report["checks"]["dashboard_bridge"]["status"], "DOWN")

        bridge.close()
        self.assertEqual(bridge.health_report()["status"], "DOWN")

    def test_health_does_not_mutate_simulation(self):
        before_status = self.city.status()
        before_diagnostics = self.city.diagnostics()
        before_effects = self.city._effect_counter
        before_requests = self.city._actuator_requests

        self.request("/health/live")
        self.request("/health/ready")
        self.request("/health")

        self.assertEqual(self.city.status(), before_status)
        self.assertEqual(self.city.diagnostics(), before_diagnostics)
        self.assertEqual(self.city._effect_counter, before_effects)
        self.assertIs(self.city._actuator_requests, before_requests)


if __name__ == "__main__":
    unittest.main()
