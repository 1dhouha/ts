import concurrent.futures
import unittest

from city_live.runtime import LiveCity
from city_live.records import LiveError


class FrameTests(unittest.TestCase):
    def setUp(self):
        self.city = LiveCity('frames', history_limit=128)
        self.addCleanup(self.city.close)

    def test_owner_has_atomic_capture_boundary(self):
        self.assertTrue(callable(getattr(self.city, 'capture_dashboard_frame', None)),
                        'LiveCity must supply a coherent owner capture')

    def test_operator_frame_includes_simulated_actuator_inventory_and_quarantine(self):
        frame = self.city.capture_dashboard_frame()
        pump = next(row for row in frame['actuators'] if row['device_id'] == 'PRT-PMP-01')
        unsupported = next(row for row in frame['actuators'] if row['device_id'] == 'RES-TLT-01')
        self.assertEqual(pump['target_id'], 'P-03')
        self.assertTrue(pump['supported'])
        self.assertEqual(pump['simulated_state']['effective_enabled'], False)
        self.assertFalse(unsupported['supported'])
        self.assertIsNone(unsupported['simulated_state'])
        self.assertEqual(frame['security']['quarantined_devices'], [])

    def test_quarantine_metadata_is_only_in_private_operator_frame(self):
        from city_dashboard.frames import read_reported_frame
        self.city.quarantine_device('PRT-PMP-01', 'test isolation')
        private = self.city.capture_dashboard_frame()
        reported = read_reported_frame(self.city.public)
        self.assertEqual(private['security']['quarantined_devices'][0]['device_id'], 'PRT-PMP-01')
        self.assertNotIn('security', reported)
        self.assertNotIn('PRT-PMP-01', str(reported))

    def test_scenario_catalog_is_visible_without_exposing_private_run_state(self):
        from city_dashboard.frames import read_reported_frame
        private = self.city.capture_dashboard_frame()
        reported = read_reported_frame(self.city.public)
        expected = {'SCN-02', 'SCN-03', 'SCN-04', 'SCN-05', 'SCN-06', 'T5'}
        self.assertEqual({row['scenario_id'] for row in reported['scenario_catalog']}, expected)
        self.assertEqual(private['scenario_catalog'], reported['scenario_catalog'])
        self.assertTrue(all(row['name'] and row['description'] for row in reported['scenario_catalog']))
        for key in ('scenario', 'diagnostics', 'actuators', 'comparisons'):
            self.assertNotIn(key, reported)

    def test_detached_frame_and_monotone_identity_across_reset(self):
        from city_dashboard.frames import capture_operator_frame
        a = capture_operator_frame(self.city)
        self.city.step()
        b = capture_operator_frame(self.city)
        self.assertEqual(a['sim_tick'], 0)
        self.assertEqual(b['sim_tick'], 1)
        self.assertEqual(b['revision'], b['diagnostics']['context']['revision'])
        self.assertEqual(b['sim_tick'], b['diagnostics']['context']['tick'])
        b['diagnostics']['physical_state'].clear()
        self.assertTrue(capture_operator_frame(self.city)['diagnostics']['physical_state'])
        self.city.reset('fresh')
        c = capture_operator_frame(self.city)
        self.assertEqual((c['run_id'], c['sim_tick']), ('fresh', 0))
        self.assertGreater(c['frame_seq'], b['frame_seq'])
        self.assertEqual(c['readings'], [])
        self.assertEqual(c['events'], [])
        self.assertEqual(c['visibility'], 'PRIVATE_OPERATOR_ONLY')

    def test_capture_remains_coherent_during_concurrent_step_reset(self):
        from city_dashboard.frames import capture_operator_frame
        def drive():
            for i in range(12):
                self.city.step()
                self.city.reset(f'run-{i}')
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            future = pool.submit(drive)
            frames = [capture_operator_frame(self.city) for _ in range(50)]
            future.result()
        for frame in frames:
            context = frame['diagnostics']['context']
            self.assertEqual((frame['run_id'], frame['sim_tick'], frame['revision']),
                             (context['run_id'], context['tick'], context['revision']))
            self.assertTrue(all(r['run_id'] == frame['run_id'] and
                                r['delivery_tick'] <= frame['sim_tick'] for r in frame['readings']))

    def test_report_capability_has_no_private_values_and_preserves_delayed_identity(self):
        from city_dashboard.frames import read_reported_frame
        self.city.sensor_fault('DTN-WLV-01', 'DELAYED', {'lag_ticks': 5})
        for _ in range(10):
            self.city.step()
        frame = read_reported_frame(self.city.public)
        self.assertEqual(frame['visibility'], 'REPORTED_ONLY')
        forbidden = {'actual_value', 'delta', 'fault', 'active_faults', 'physical_state',
                     'seed', 'revision', 'diagnostics', 'scenario', 'source'}
        def check(value):
            if isinstance(value, dict):
                self.assertFalse(forbidden.intersection(value))
                for item in value.values(): check(item)
            elif isinstance(value, list):
                for item in value: check(item)
        check(frame)
        r = next(r for r in reversed(frame['records']) if r['sensor_id'] == 'DTN-WLV-01')
        self.assertEqual((r['sim_tick'], r['delivery_tick']), (5, 10))
        self.assertEqual(r['timestamp'], '2026-10-18T00:00:05Z')
        self.assertEqual(r, next(x for x in self.city.public.observations() if x['seq'] == r['seq']))

    def test_report_bootstrap_cursor_gap_old_run_and_nulls(self):
        from city_dashboard.frames import read_reported_frame
        empty = read_reported_frame(self.city.public)
        self.assertEqual((empty['run_id'], empty['records'], empty['frame_seq']), ('frames', [], 0))
        self.city.sensor_fault('PRT-WLV-01', 'DEAD', {})
        for _ in range(10): self.city.step()
        page = read_reported_frame(self.city.public, {'run_id': 'frames', 'after_seq': 0, 'limit': 1})
        self.assertEqual(len(page['records']), 1)
        self.assertFalse(page['at_end'])
        rows = read_reported_frame(self.city.public)['records']
        self.assertTrue(any(r['reported_value'] is None and not r['available'] for r in rows))
        with self.assertRaisesRegex(LiveError, 'OLD_RUN'):
            read_reported_frame(self.city.public, {'run_id': 'old', 'after_seq': 0})
        with self.assertRaisesRegex(LiveError, 'CURSOR_INVALID'):
            read_reported_frame(self.city.public, {'run_id': 'frames', 'after_seq': 999999})
        with self.assertRaisesRegex(LiveError, 'INVALID_ARGUMENT'):
            read_reported_frame(self.city.public, {'role': 'operator'})
        small = LiveCity('small', history_limit=1)
        self.addCleanup(small.close)
        for _ in range(10): small.step()
        with self.assertRaisesRegex(LiveError, 'HISTORY_GAP'):
            read_reported_frame(small.public, {'run_id': 'small', 'after_seq': 0})

    def test_closed_owner_fails_instead_of_stale_success(self):
        from city_dashboard.frames import capture_operator_frame
        self.city.close()
        with self.assertRaisesRegex(LiveError, 'CLOSED'):
            capture_operator_frame(self.city)
