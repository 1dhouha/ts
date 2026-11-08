import unittest
from unittest.mock import patch

from city_live.runtime import LiveCity


class LiveCityHealthTests(unittest.TestCase):
    def setUp(self):
        self.city = LiveCity("health-test")
        self.addCleanup(self.city.close)

    def test_initialized_runtime_and_city_core_are_ready(self):
        report = self.city.health()

        self.assertEqual(report["status"], "UP")
        self.assertEqual(
            {name: check["status"] for name, check in report["checks"].items()},
            {
                "initialized": "UP",
                "lifecycle": "UP",
                "city_core": "UP",
            },
        )

    def test_uninitialized_runtime_is_not_ready(self):
        self.city._initialized = False

        report = self.city.health()

        self.assertEqual(report["status"], "DOWN")
        self.assertEqual(report["checks"]["initialized"]["status"], "DOWN")
        self.assertEqual(report["checks"]["city_core"]["status"], "DOWN")

    def test_required_core_failure_is_safe_and_readiness_recovers(self):
        with patch.object(
            self.city, "_snapshot", side_effect=RuntimeError("private diagnostic")
        ):
            report = self.city.health()

        self.assertEqual(report["status"], "DOWN")
        self.assertEqual(report["checks"]["city_core"]["status"], "DOWN")
        self.assertNotIn("private diagnostic", repr(report))
        self.assertEqual(self.city.health()["status"], "UP")

    def test_error_and_shutdown_states_are_not_ready(self):
        with self.city._condition:
            self.city._state = "ERROR"
        self.assertEqual(self.city.health()["status"], "DOWN")

        self.city.close()
        report = self.city.health()
        self.assertEqual(report["status"], "DOWN")
        self.assertEqual(report["checks"]["lifecycle"]["status"], "DOWN")

    def test_running_state_requires_live_owner_worker(self):
        with self.city._condition:
            self.city._state = "RUNNING"
            self.city._thread = None

        report = self.city.health()

        self.assertEqual(report["status"], "DOWN")
        self.assertEqual(report["checks"]["lifecycle"]["status"], "DOWN")

    def test_health_does_not_mutate_simulation(self):
        before_status = self.city.status()
        before_diagnostics = self.city.diagnostics()
        before_effects = self.city._effect_counter
        before_requests = self.city._actuator_requests

        for _ in range(3):
            self.city.health()

        self.assertEqual(self.city.status(), before_status)
        self.assertEqual(self.city.diagnostics(), before_diagnostics)
        self.assertEqual(self.city._effect_counter, before_effects)
        self.assertIs(self.city._actuator_requests, before_requests)


if __name__ == "__main__":
    unittest.main()
