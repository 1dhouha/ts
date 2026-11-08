import json
import unittest

from city_live.runtime import LiveCity
from city_live.registry import proposed_registry, load_registry
from city_live.records import LiveError


class SensorTests(unittest.TestCase):
    def setUp(self):
        self.city = LiveCity('sensors', seed=17)
        self.addCleanup(self.city.close)

    def sample(self, sensor='PRT-WLV-01', steps=5):
        for _ in range(steps):
            self.city.step()
        return self.city.sensor_compare(sensor)

    def test_pdf_registry_exact_ids_ranges_intervals_links_backups(self):
        entries = proposed_registry()
        self.assertEqual(len(entries), 16)
        self.assertEqual({r.sensor_id for r in entries}, {
            'PRT-WLV-01', 'PRT-WLV-02', 'PRT-FLW-01', 'PRT-PRS-01',
            'IND-TMP-01', 'IND-TMP-02', 'IND-PRS-01', 'IND-FLW-01',
            'DTN-TRF-01', 'DTN-TRF-02', 'DTN-WLV-01', 'DTN-TMP-01',
            'RES-WLV-01', 'RES-PRS-01', 'RES-FLW-01', 'RES-TRF-01'})
        records = {r.sensor_id: r for r in entries}
        self.assertEqual(records['PRT-WLV-01'].backup_ids, ('PRT-WLV-02',))
        self.assertEqual(records['PRT-WLV-02'].linked_device_ids, ('PRT-PMP-02',))
        self.assertEqual(records['IND-TMP-02'].backup_ids, ('IND-TMP-01',))
        self.assertEqual(records['DTN-TMP-01'].interval, 60)
        self.assertEqual(records['DTN-TMP-01'].hard_limits, (-10, 50))
        self.assertEqual(records['RES-PRS-01'].normal_limits, (3, 5))
        self.assertEqual(records['RES-TRF-01'].unit, 'veh/min')
        self.assertEqual(records['PRT-WLV-01'].source, 'model:harbour_level')

    def test_truth_conversion_and_traffic_count_is_departures_not_queue(self):
        level = self.sample()
        self.assertEqual(level['actual_value'], 50)
        self.assertEqual(level['reported_value'], 50)
        count = self.sample('DTN-TRF-01')
        self.assertEqual(count['actual_value'], 30)
        self.assertNotEqual(count['actual_value'],
                            self.city.diagnostics()['physical_state']['roads']['R-LOW']['queue_vehicles'])
        self.assertEqual(self.city.sensor_compare('PRT-WLV-01')['actual_value'], 50)
        self.city.sensor_fault('PRT-WLV-01', 'FALSE_READING', {'value': 180})

    def test_false_reading_clamps_output_but_never_truth_or_public_fault_label(self):
        self.sample()
        before = self.city.diagnostics()['physical_state']['water']
        self.city.sensor_fault('PRT-WLV-01', 'FALSE_READING', {'value': 999})
        reading = self.sample()
        self.assertEqual(reading['actual_value'], 50)
        self.assertEqual(reading['reported_value'], 300)
        self.assertEqual(reading['delta'], 250)
        self.assertEqual(reading['status'], 'OK')
        self.assertEqual(before['basin_volume_m3'],
                         self.city.diagnostics()['physical_state']['water']['basin_volume_m3'])
        for field in ('actual_value', 'delta', 'fault', 'FALSE_READING', 'fault_id'):
            self.assertNotIn(field, json.dumps(self.city.public.observations()))

    def test_stuck_drift_noise_and_clear(self):
        self.sample()
        self.city.sensor_fault('PRT-WLV-01', 'STUCK', {})
        self.city.incident('heavy_rain')
        stuck = self.sample()
        self.assertEqual(stuck['reported_value'], 50)
        self.assertGreater(stuck['actual_value'], 50)
        self.city.sensor_clear('PRT-WLV-01')
        self.city.sensor_fault('PRT-WLV-01', 'DRIFT', {'rate': -1})
        drift = self.sample()
        self.assertAlmostEqual(drift['delta'], -5, places=2)
        self.city.sensor_clear('PRT-WLV-01')
        self.city.sensor_fault('PRT-WLV-01', 'NOISE', {'amplitude': 2})
        noisy = self.sample()
        self.assertLessEqual(abs(noisy['delta']), 2)
        self.assertEqual(noisy['status'], 'OK')

    def test_delayed_timestamp_and_delivery_are_distinct(self):
        self.city.incident('heavy_rain')
        self.sample()
        self.city.sensor_fault('PRT-WLV-01', 'DELAYED', {'lag_ticks': 3})
        delayed = self.sample()
        self.assertEqual(delayed['sim_tick'], 7)
        self.assertEqual(delayed['delivery_tick'], 10)
        self.assertEqual(delayed['timestamp'], '2026-10-18T00:00:07Z')
        self.assertEqual(delayed['status'], 'DEGRADED')
        self.assertLess(delayed['actual_value'], 51)

    def test_dropout_dead_repair_and_reset(self):
        self.sample()
        self.city.sensor_fault('PRT-WLV-01', 'DROPOUT', {'duration_ticks': 6})
        reading = self.sample()
        self.assertIsNone(reading['reported_value'])
        self.assertEqual(reading['status'], 'OFFLINE')
        self.assertIsNone(reading['delta'])
        self.assertEqual(self.sample()['status'], 'OK')
        self.city.sensor_fault('PRT-WLV-01', 'DEAD', {})
        self.assertEqual(self.sample()['status'], 'FAILED')
        with self.assertRaisesRegex(LiveError, 'REPAIR_REQUIRED'):
            self.city.sensor_clear('PRT-WLV-01')
        self.city.sensor_repair('PRT-WLV-01')
        self.assertEqual(self.sample()['status'], 'OK')
        self.city.reset('sensors-new')
        self.assertEqual(self.city.public.observations(), [])
        self.assertEqual(self.sample()['fault'], None)

    def test_sampling_counts_sequences_and_independent_devices(self):
        self.city.sensor_fault('PRT-WLV-01', 'DEAD', {})
        for _ in range(60):
            self.city.step()
        values = self.city.public.observations()
        self.assertEqual([r['seq'] for r in values], list(range(1, len(values) + 1)))
        for sensor, count in [('PRT-WLV-01', 12), ('DTN-TRF-01', 6), ('DTN-TMP-01', 1)]:
            selected = [r for r in values if r['sensor_id'] == sensor]
            self.assertEqual(len(selected), count)
        self.assertEqual(self.city.sensor_compare('PRT-WLV-01')['status'], 'FAILED')
        self.assertEqual(self.city.sensor_compare('PRT-WLV-02')['status'], 'OK')

    def test_scenario_input_cleanup_removes_only_its_forced_sample(self):
        bank = self.city._sensors
        bank.set_scenario_input('scenario-a', 'PRT-WLV-01', 40)
        bank.force_sample('PRT-WLV-01', 4)
        bank.set_scenario_input('scenario-b', 'DTN-TRF-01', 30)
        bank.force_sample('DTN-TRF-01', 5)

        bank.clear_scenario_inputs('scenario-a')

        self.assertNotIn('PRT-WLV-01', bank.scenario_inputs)
        self.assertNotIn('PRT-WLV-01', bank.forced_samples)
        self.assertIn('DTN-TRF-01', bank.scenario_inputs)
        self.assertEqual(bank.forced_samples['DTN-TRF-01'], 5)

    def test_invalid_faults_duplicate_faults_and_replacement_ids(self):
        for kind, params in [('BOGUS', {}), ('NOISE', {'amplitude': -1}),
                             ('DELAYED', {'lag_ticks': True}), ('FALSE_READING', {}),
                             ('FALSE_READING', {'value': 4, 'offset': 2})]:
            with self.assertRaises(LiveError):
                self.city.sensor_fault('PRT-WLV-01', kind, params)
        self.city.sensor_fault('PRT-WLV-01', 'DEAD', {})
        with self.assertRaisesRegex(LiveError, 'FAULT_ALREADY_ACTIVE'):
            self.city.sensor_fault('PRT-WLV-01', 'DEAD', {})
        self.city.sensor_replace('PRT-WLV-01', 'PRT-WLV-04')
        new = self.sample('PRT-WLV-04')
        self.assertEqual(new['reported_value'], 50)
        self.assertEqual(new['fault'], None)
        with self.assertRaises(LiveError):
            self.city.sensor_replace('PRT-WLV-04', 'PRT-WLV-01')
        self.city.reset('replacement-reset')
        self.assertIn('PRT-WLV-04', [r['sensor_id'] for r in self.city.sensors()])
        self.assertFalse(next(r for r in self.city.sensors() if r['sensor_id'] == 'PRT-WLV-01')['active'])

    def test_backup_fault_state_independent_on_explicit_test_only_source_fixture(self):
        # This proves adapter independence, NOT that harbour physics exists.
        from dataclasses import replace
        from city_core import build_city
        from city_live.sensors import SensorBank
        from city_live.history import PublicStore
        from city_live.records import utc_epoch
        pair = tuple(replace(row, source='basin') for row in proposed_registry()
                     if row.sensor_id in ('PRT-WLV-01', 'PRT-WLV-02'))
        bank = SensorBank(1, utc_epoch('2026-10-18T00:00:00Z'), registry=pair)
        bank.inject('PRT-WLV-01', 'DEAD', {}, 0)
        core = build_city('fixture').value
        store = PublicStore(run_id='fixture')
        for tick in range(5):
            snap = core.lifecycle.advance_one('fixture', tick).value
            bank.capture(snap, {})
        bank.sample(5, store.append)
        self.assertEqual(bank.compare('PRT-WLV-01')['status'], 'FAILED')
        backup = bank.compare('PRT-WLV-02')
        self.assertEqual(backup['status'], 'OK')
        self.assertEqual(backup['sensor_id'], 'PRT-WLV-02')
        self.assertEqual(backup['reported_value'], 290)
