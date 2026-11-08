import unittest

from city_live import LiveCity, LiveError


class ReviewRegressions(unittest.TestCase):
    def test_maximum_delay_preserves_full_traffic_window(self):
        for lag in (592, 600):
            with self.subTest(lag=lag):
                city = LiveCity('delay', seed=1)
                self.addCleanup(city.close)
                city.sensor_fault('DTN-TRF-01', 'DELAYED', {'lag_ticks': lag})
                for _ in range(630):
                    city.step()
                row = city.sensor_compare('DTN-TRF-01')
                self.assertIsNotNone(row['reported_value'])
                self.assertEqual(row['status'], 'DEGRADED')
                self.assertEqual(row['sim_tick'], 630 - lag)
                if lag == 600:
                    self.assertEqual(row['reported_value'], 30)

    def test_stuck_keeps_last_available_degraded_report(self):
        city = LiveCity('stuck')
        self.addCleanup(city.close)
        for _ in range(5):
            city.step()
        city.sensor_fault('PRT-WLV-01', 'DELAYED', {'lag_ticks': 1})
        city.incident('heavy_rain')
        for _ in range(10):
            city.step()
        previous = city.sensor_compare('PRT-WLV-01')['reported_value']
        self.assertEqual(previous, 50.45)
        city.sensor_clear('PRT-WLV-01')
        city.sensor_fault('PRT-WLV-01', 'STUCK', {})
        for _ in range(5):
            city.step()
        self.assertEqual(city.sensor_compare('PRT-WLV-01')['reported_value'], previous)

    def test_stuck_without_previous_available_report_rejects(self):
        city = LiveCity('never-sampled')
        self.addCleanup(city.close)
        with self.assertRaisesRegex(LiveError, 'NO_VALID_SAMPLE'):
            city.sensor_fault('PRT-WLV-01', 'STUCK', {})
