import unittest

from city_live.demo import demonstrate


class DemonstrationTests(unittest.TestCase):
    def test_real_core_demonstration_asserts_consequences_and_seeded_repeat(self):
        result = demonstrate()
        self.assertTrue(result['passed'])
        self.assertTrue(result['seeded_repeat_equal'])
        self.assertTrue(result['core_replay_equal'])
        self.assertGreater(result['checkpoints']['flood']['surface_m3'], 200)
        self.assertLess(result['checkpoints']['recovery']['surface_m3'],
                        result['checkpoints']['flood']['surface_m3'])
        self.assertEqual(result['spoof']['reported_value'], 180)
        self.assertNotEqual(result['spoof']['actual_value'], 180)
        self.assertEqual(result['proposed_sensor_count'], 16)
        self.assertEqual(result['grounded_proposed_sensors'], 16)
