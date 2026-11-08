"""Standalone partner mock tests [Requirement R1].

Verifies that the Brain and Guardian mocks only use public Façade tools and
react correctly to telemetry faults.
"""
import os
from pathlib import Path
import sys
import unittest

os.environ.setdefault("TWIN_ALLOW_INSECURE_DEV_TOKENS", "1")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from city_live.runtime import LiveCity
from mocks.brain_mock.mock_brain import BrainMock
from mocks.guardian_mock.mock_guardian import GuardianMock
from twin_interface.facade import TwinFacade


class GuardianMockTests(unittest.TestCase):
    def setUp(self):
        self.city = LiveCity("test-guardian-mock")
        self.facade = TwinFacade(self.city)
        for _ in range(5):
            self.city.step()
        self.guardian = GuardianMock(self.facade)

    def tearDown(self):
        self.city.close()

    def test_isolates_sensor_that_jumps_away_from_backup(self):
        self.assertEqual(self.guardian.scan(), [])

        self.city.sensor_fault("PRT-WLV-01", "FALSE_READING", {"offset": 100.0})
        for _ in range(5):
            self.city.step()

        actions = self.guardian.scan()

        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].device_id, "PRT-WLV-01")
        self.assertEqual(actions[0].backup_id, "PRT-WLV-02")
        self.assertGreater(actions[0].discrepancy_cm, 50.0)
        self.assertTrue(self.facade.is_device_isolated("PRT-WLV-01"))
        self.assertFalse(self.facade.is_device_isolated("PRT-WLV-02"))

    def test_does_not_reisolate_or_touch_healthy_pair(self):
        self.city.sensor_fault("PRT-WLV-01", "FALSE_READING", {"offset": 100.0})
        for _ in range(5):
            self.city.step()
        self.guardian.scan()
        for _ in range(5):
            self.city.step()

        self.assertEqual(self.guardian.scan(), [])

    def test_restore_lifts_quarantine(self):
        self.city.sensor_fault("PRT-WLV-01", "FALSE_READING", {"offset": 100.0})
        for _ in range(5):
            self.city.step()
        self.guardian.scan()

        response = self.guardian.restore("PRT-WLV-01")

        self.assertTrue(response["ok"])
        self.assertFalse(self.facade.is_device_isolated("PRT-WLV-01"))


class BrainMockTests(unittest.TestCase):
    def setUp(self):
        self.city = LiveCity("test-brain-mock")
        self.facade = TwinFacade(self.city)
        for _ in range(5):
            self.city.step()
        self.brain = BrainMock(self.facade)

    def tearDown(self):
        self.city.close()

    def test_dispatches_linked_pump_above_flood_threshold(self):
        self.city.sensor_fault("PRT-WLV-01", "FALSE_READING", {"offset": 100.0})
        for _ in range(5):
            self.city.step()

        actions = self.brain.tick()

        dispatched = {action.device_id for action in actions}
        self.assertIn("PRT-PMP-01", dispatched)
        self.assertTrue(all(action.response["ok"] for action in actions))

    def test_dispatch_is_idempotent_per_episode(self):
        self.city.sensor_fault("PRT-WLV-01", "FALSE_READING", {"offset": 100.0})
        for _ in range(5):
            self.city.step()
        self.brain.tick()

        self.assertEqual(self.brain.tick(), [])

    def test_ignores_offline_readings(self):
        self.city.sensor_fault("PRT-WLV-01", "FALSE_READING", {"offset": 100.0})
        for _ in range(5):
            self.city.step()
        guardian = GuardianMock(self.facade)
        guardian.scan()

        actions = self.brain.tick()

        self.assertNotIn("PRT-PMP-01", {action.device_id for action in actions})


if __name__ == "__main__":
    unittest.main()
