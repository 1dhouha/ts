from dataclasses import replace
import json
import unittest

from city_live import LiveCity, LiveError
from city_live.cli import execute
from city_live.registry import proposed_registry, validate_registry
from city_live.faults import create_fault, reported
from city_live.records import utc_epoch
from city_live.demand import TrafficDemand


class AdversarialTests(unittest.TestCase):
    def test_same_id_user_schedule_cannot_hijack_immediate_operator_incident(self):
        city = LiveCity('collision')
        self.addCleanup(city.close)
        city.schedule('operator-00000002', 5, 'power_restore', run_id='collision')
        result = city.incident('power_outage')
        self.assertEqual(result['kind'], 'power_outage')
        self.assertFalse(city.diagnostics()['power']['hospital_powered'])
        self.assertEqual(city.actions()[0]['status'], 'PENDING')

    def test_scheduled_fault_observation_changes_without_changing_physics(self):
        city = LiveCity('scheduled-fault')
        self.addCleanup(city.close)
        city.schedule('spoof', 5, 'sensor_fault', {'sensor_id': 'PRT-WLV-01',
                      'fault_type': 'FALSE_READING', 'parameters': {'value': 180}}, run_id='scheduled-fault')
        for _ in range(10):
            city.step()
        self.assertEqual(city.sensor_compare('PRT-WLV-01')['reported_value'], 180)
        self.assertEqual(city.sensor_compare('PRT-WLV-01')['actual_value'], 50)
        city.schedule('clear', 10, 'sensor_clear', {'sensor_id': 'PRT-WLV-01'}, run_id='scheduled-fault')
        for _ in range(5):
            city.step()
        self.assertEqual(city.sensor_compare('PRT-WLV-01')['delta'], 0)

    def test_stale_command_and_revision_rejected_before_effect(self):
        city = LiveCity('stale')
        self.addCleanup(city.close)
        city.reset('fresh')
        before = city.diagnostics()
        with self.assertRaisesRegex(LiveError, 'OLD_RUN'):
            city.command('A-VALVE-01', .9, command_id='old', run_id='stale')
        with self.assertRaisesRegex(LiveError, 'STATE_CONFLICT'):
            city.command('A-VALVE-01', .9, command_id='stale', run_id='fresh', expected_revision=10)
        self.assertEqual(city.diagnostics(), before)

    def test_old_id_registration_history_survives_reset(self):
        city = LiveCity('registration')
        self.addCleanup(city.close)
        city.sensor_replace('DTN-TRF-01', 'DTN-TRF-03')
        city.reset('registration2')
        with self.assertRaises(LiveError):
            city.sensor_replace('DTN-TRF-03', 'DTN-TRF-01')
        with self.assertRaises(LiveError):
            city.sensor_replace('DTN-TRF-03', 'RES-TRF-04')
        with self.assertRaises(LiveError):
            city.sensor_replace('missing', 'DTN-TRF-04')

    def test_registry_rejects_duplicate_ids_units_ranges_and_bad_backups(self):
        rows = proposed_registry()
        cases = [(*rows, rows[0]), (replace(rows[0], unit='m'), *rows[1:]),
                 (replace(rows[0], normal_limits=(-1, 5)), *rows[1:]),
                 (replace(rows[0], backup_ids=('missing',)), *rows[1:]),
                 (replace(rows[0], sensor_id='port-wlv-01'), *rows[1:])]
        for entries in cases:
            with self.assertRaises(LiveError):
                validate_registry(entries)

    def test_invalid_constructor_epoch_and_closed_calls(self):
        for epoch in ('2026-10-18', 'not-dateZ', '9999-12-31T23:59:59Z', '2026-10-18T00:00:00.5Z'):
            with self.assertRaises(LiveError):
                utc_epoch(epoch)
        for kwargs in ({'seed': True}, {'tick_budget': 0}, {'history_limit': -1}, {'action_limit': 100000}):
            with self.assertRaises(LiveError):
                LiveCity('invalid', **kwargs)
        city = LiveCity('closed')
        city.close()
        with self.assertRaisesRegex(LiveError, 'CLOSED'):
            city.start()
        with self.assertRaisesRegex(LiveError, 'CLOSED'):
            city.diagnostics()

    def test_delayed_warmup_is_null_not_fake_zero_and_no_global_randomness(self):
        city = LiveCity('warmup')
        self.addCleanup(city.close)
        city.sensor_fault('PRT-WLV-01', 'DELAYED', {'lag_ticks': 600})
        for _ in range(5):
            city.step()
        reading = city.sensor_compare('PRT-WLV-01')
        self.assertIsNone(reading['reported_value'])
        self.assertFalse(reading['available'])
        fault = create_fault('n', 'NOISE', 0, {'amplitude': 5}, 0)
        a = reported(fault, 40, 10, 'one', 8)
        b = reported(fault, 40, 10, 'one', 8)
        self.assertEqual(a, b)
        self.assertNotEqual(a, reported(fault, 40, 10, 'two', 8))
        self.assertEqual(reported(create_fault('o', 'FALSE_READING', 0, {'offset': 5}, None),
                                  40, 1, 'one', 1)[0], 45)

    def test_failed_dispatch_does_not_claim_completion_or_block_future_steps(self):
        city = LiveCity('dispatch')
        self.addCleanup(city.close)
        bad = city.incident('dispatch', {'target': 'AMB-01', 'mission': 'm',
            'destination': 'J-D1', 'route': ['R-DOWN']})
        self.assertEqual(bad['status'], 'FAILED')
        good = city.incident('dispatch', {'target': 'AMB-01', 'mission': 'm',
            'destination': 'J-D1', 'route': ['R-LOW']})
        self.assertEqual(good['status'], 'COMPLETED')
        self.assertEqual(city.incident('dispatch', {'target': 'AMB-01', 'mission': 'second',
            'destination': 'J-D1', 'route': ['R-LOW']})['status'], 'FAILED')
        for _ in range(60):
            city.step()
        self.assertEqual(city.incident('release', {'target': 'AMB-01', 'mission': 'm'})['status'], 'COMPLETED')

    def test_core_effect_boundary_retains_source_run_revision_and_dedup_checks(self):
        # Original suite remains canonical; composition must not bypass it.
        city = LiveCity('core')
        self.addCleanup(city.close)
        command = city.command('A-SIG-01', 'stop', command_id='sig')
        self.assertEqual(command['status'], 'COMPLETED')
        with self.assertRaisesRegex(LiveError, 'IDEMPOTENCY_CONFLICT'):
            city.command('A-SIG-01', 'normal', command_id='sig')
        with self.assertRaises(LiveError):
            city.command('A-VALVE-01', float('inf'), command_id='bad')
        with self.assertRaises(LiveError):
            city.command('A-PUMP-01', 1, command_id='bad')
        with self.assertRaises(LiveError):
            city.command('A-SIG-02', 'red', command_id='bad')
        with self.assertRaises(LiveError):
            city.command('A-PUMP-01', True, available='yes', command_id='bad')

    def test_cli_command_families_and_fault_parameters(self):
        city = LiveCity('grammar')
        self.addCleanup(city.close)
        for line in ('help', 'speed .5', 'traffic time_of_day', 'sensors list', 'scenarios list',
                     'scenarios active', 'incident partial_block R-LOW', 'recover power',
                     'recover rain', 'recover pollution', 'incident valve .3', 'command valve .8 .42',
                     'command pump on', 'inspect', 'events', 'observations',
                     'schedule pending 20 power_outage', 'cancel pending', 'step 5',
                     'sensor fault PRT-WLV-01 OFFSET 3', 'sensor clear PRT-WLV-01',
                     'sensor fault PRT-WLV-01 DROPOUT 10', 'sensor repair PRT-WLV-01',
                     'sensor fault PRT-WLV-01 DELAYED 2', 'sensor repair PRT-WLV-01',
                     'sensor fault PRT-WLV-01 DEAD', 'sensor replace PRT-WLV-01 PRT-WLV-04',
                     'scenarios run normal', 'stop', 'step', 'watch 0'):
            with self.subTest(line=line):
                execute(city, line)
        for line in ('recover unknown', 'sensors no', 'sensor nope a', 'sensor fault a BOGUS 3',
                     'scenarios nope', 'scenarios run', 'command pump maybe', 'command nope 1'):
            with self.subTest(line=line), self.assertRaises(LiveError):
                execute(city, line)

    def test_preset_budget_atomicity_cursors_and_profile_validation(self):
        city = LiveCity('preset', action_limit=1)
        self.addCleanup(city.close)
        with self.assertRaises(LiveError):
            city.run_scenario('combined_crisis')
        self.assertEqual(city.actions(), [])
        for name in ('evening_rush', 'surge', 'congestion', 'low', 'normal'):
            city.traffic(name)
        city.traffic('custom', {'base': .3, 'amplitude': .01, 'transition_ticks': 2})
        with self.assertRaises(LiveError):
            city.traffic('unknown')
        with self.assertRaises(LiveError):
            city.traffic('custom', {'base': True, 'amplitude': 0, 'transition_ticks': 1})
        with self.assertRaises(LiveError):
            city.public.events_since('preset', 1)
        self.assertEqual(city.public.events_since('preset', 0)['records'], [])
        for _ in range(5):
            city.step()
        packet = city.public.events_since('preset', 0, 1)
        self.assertEqual(packet['next_after_seq'], 1)
        self.assertFalse(packet['at_end'])

    def test_active_disturbances_follow_effective_truth_and_clear_on_recovery(self):
        city = LiveCity('active')
        self.addCleanup(city.close)
        city.incident('heavy_rain')
        city.incident('power_outage')
        city.incident('accident', {'target': 'R-LOW'})
        active = city.diagnostics()['active_disturbances']
        self.assertEqual({r['kind'] for r in active}, {'rain', 'power_outage', 'road_obstruction'})
        city.incident('rain_clear')
        city.incident('power_restore')
        city.incident('road_clear', {'target': 'R-LOW'})
        self.assertEqual(city.diagnostics()['active_disturbances'], [])

    def test_replacement_is_uppercase_permanent_and_has_registration_run(self):
        city = LiveCity('reg')
        self.addCleanup(city.close)
        with self.assertRaises(LiveError):
            city.sensor_replace('PRT-WLV-01', 'prt-wlv-04')
        city.sensor_replace('PRT-WLV-01', 'PRT-WLV-04')
        row = next(r for r in city.sensors() if r['sensor_id'] == 'PRT-WLV-04')
        self.assertEqual(row['registered_run_id'], 'reg')

    def test_scheduled_final_budget_tick_rejected_instead_of_stranded(self):
        city = LiveCity('last', tick_budget=5)
        self.addCleanup(city.close)
        with self.assertRaises(LiveError):
            city.schedule('never', 5, 'power_outage', run_id='last')
