"""Supervisor-selected inventory: real sources, isolation and lifecycle."""
import json
import unittest

from city_live.runtime import LiveCity
from city_live.registry import proposed_registry
from city_live.records import LiveError


class PdfInventoryTests(unittest.TestCase):
    def city(self, run='pdf'):
        city = LiveCity(run, seed=27, history_limit=1000)
        self.addCleanup(city.close)
        return city

    def advance(self, city, count):
        for _ in range(count):
            city.step()

    def test_only_pdf_sensors_work_and_all_have_due_numeric_readings(self):
        city = self.city()
        expected = {r.sensor_id: r for r in proposed_registry()}
        self.assertEqual({r['sensor_id'] for r in city.sensors() if r['active']}, set(expected))
        self.assertTrue(all(r['source'] for r in city.sensors()))
        self.advance(city, 60)
        rows = city.public.observations()
        self.assertEqual({r['sensor_id'] for r in rows}, set(expected))
        for sensor_id, sensor in expected.items():
            selected = [r for r in rows if r['sensor_id'] == sensor_id]
            self.assertEqual(len(selected), 60 // sensor.interval)
            self.assertEqual(selected[0]['sim_tick'], sensor.interval)
            self.assertTrue(all(r['available'] and r['status'] == 'OK' for r in selected))
            self.assertTrue(all(isinstance(r['reported_value'], (int, float)) for r in selected))
        for old in ('S-WATER-01', 'S-CAM-01', 'S-POWER-ESS', 'S-AIR-IND', 'S-CALL-01'):
            with self.assertRaisesRegex(LiveError, 'SENSOR_NOT_FOUND'):
                city.sensor_fault(old, 'DEAD', {})

    def test_rain_and_power_affect_real_sources_and_backups_are_independent(self):
        normal, incident = self.city('normal'), self.city('incident')
        incident.incident('heavy_rain')
        incident.incident('power_outage')
        self.advance(normal, 60)
        self.advance(incident, 60)
        for sensor in ('PRT-WLV-01', 'DTN-WLV-01'):
            self.assertGreater(incident.sensor_compare(sensor)['actual_value'],
                               normal.sensor_compare(sensor)['actual_value'])
        for sensor in ('PRT-FLW-01', 'PRT-PRS-01', 'IND-FLW-01', 'IND-PRS-01', 'RES-FLW-01', 'RES-PRS-01'):
            self.assertEqual(incident.sensor_compare(sensor)['actual_value'], 0)
            self.assertGreater(normal.sensor_compare(sensor)['actual_value'], 0)
        for a, b in (('PRT-WLV-01', 'PRT-WLV-02'), ('IND-TMP-01', 'IND-TMP-02')):
            self.assertEqual(normal.sensor_compare(a)['actual_value'], normal.sensor_compare(b)['actual_value'])
            normal.sensor_fault(a, 'DEAD', {})
        self.advance(normal, 10)
        self.assertEqual(normal.sensor_compare('PRT-WLV-01')['status'], 'FAILED')
        self.assertEqual(normal.sensor_compare('PRT-WLV-02')['status'], 'OK')
        self.assertEqual(normal.sensor_compare('IND-TMP-01')['status'], 'FAILED')
        self.assertEqual(normal.sensor_compare('IND-TMP-02')['status'], 'OK')

    def test_faults_do_not_change_extension_and_reset_repeats(self):
        city, control = self.city(), self.city('control')
        city.sensor_fault('PRT-WLV-01', 'FALSE_READING', {'value': 180})
        self.advance(city, 60)
        self.advance(control, 60)
        self.assertEqual(city.diagnostics()['sensor_model'], control.diagnostics()['sensor_model'])
        self.assertEqual(city.sensor_compare('PRT-WLV-01')['reported_value'], 180)
        self.assertNotEqual(city.sensor_compare('PRT-WLV-01')['actual_value'], 180)
        for forbidden in ('actual_value', 'delta', 'fault', 'sensor_model', 'harbour_volume'):
            self.assertNotIn(forbidden, json.dumps(city.public.capture_reported()))
        before = control.diagnostics()['sensor_model']
        city.reset('reset')
        self.assertEqual(city.public.observations(), [])
        self.advance(city, 60)
        self.assertEqual(city.diagnostics()['sensor_model'], before)
        self.assertEqual(city.sensor_compare('PRT-WLV-01')['delta'], 0)

    def test_replacements_preserve_sixteen_slots_and_reject_legacy_families(self):
        city = self.city()
        with self.assertRaises(LiveError):
            city.sensor_replace('PRT-WLV-01', 'S-WATER-04')
        city.sensor_replace('PRT-WLV-01', 'PRT-WLV-03')
        self.assertEqual(len([r for r in city.sensors() if r['active']]), 16)
        backup = next(r for r in city.sensors() if r['sensor_id'] == 'PRT-WLV-02')
        self.assertEqual(backup['backup_ids'], ['PRT-WLV-03'])
        self.advance(city, 5)
        self.assertTrue(city.sensor_compare('PRT-WLV-03')['available'])
        with self.assertRaisesRegex(LiveError, 'SENSOR_NOT_FOUND'):
            city.sensor_compare('PRT-WLV-01')

    def test_extension_volume_balances_tick_guard_and_detached_diagnostics(self):
        from dataclasses import replace
        from city_live.sensor_physics import SensorPhysics, advance
        from city_live.records import utc_epoch
        city = self.city()
        city.incident('heavy_rain')
        before = city.diagnostics()['sensor_model']
        city.step()
        after = city.diagnostics()['sensor_model']
        for volume, inlet, outlet in (
            ('harbour_volume_m3', 'harbour_inflow_m3_s', 'harbour_outflow_m3_s'),
            ('drain_volume_m3', 'drain_inflow_m3_s', 'drain_outflow_m3_s'),
            ('reservoir_volume_m3', 'reservoir_supply_m3_s', 'network_flow_m3_s')):
            self.assertAlmostEqual(after[volume], before[volume] + after[inlet] - after[outlet])
        after['harbour_volume_m3'] = -1
        self.assertGreater(city.diagnostics()['sensor_model']['harbour_volume_m3'], 0)
        snapshot = city._snapshot()  # Test-only privileged core truth.
        with self.assertRaisesRegex(LiveError, 'SENSOR_MODEL_TICK_MISMATCH'):
            advance(replace(SensorPhysics(), tick=5), snapshot, utc_epoch('2026-10-18T00:00:00Z'))
