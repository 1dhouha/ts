"""Person B scenario data is adapted into the Person C LiveCity boundary."""
from types import SimpleNamespace
import unittest
from datetime import datetime, timezone

from city_live.b_scenarios import (
    Scenario,
    ScenarioRunner,
    Trigger,
    _fault_params,
    load_scenarios,
    scenario,
)
from city_live.records import LiveError


class FakeSensors:
    def __init__(self):
        self.present = {'PRT-WLV-01', 'DTN-TRF-01', 'IND-TMP-01', 'IND-TMP-02'}
        self.inputs = {}
        self.calls = []
        self.inject_error = None
        self.faults = {}
        self.latest = {}

    def set_scenario_input(self, scenario, sensor, value):
        self.calls.append(('set_scenario_input', scenario, sensor, value))
        self.inputs[sensor] = value

    def clear_scenario_inputs(self, scenario):
        self.calls.append(('clear_scenario_inputs', scenario))
        self.inputs.clear()
        self.forced = {}

    def force_sample(self, sensor, tick):
        self.calls.append(('force_sample', sensor, tick))

    def sensor(self, sensor):
        if sensor not in self.present:
            raise LiveError('SENSOR_NOT_FOUND')
        return SimpleNamespace(
            unit='cm',
            hard_limits=(0, 300),
            linked_device_ids=('PRT-PMP-01',),
        )

    def sensor_row(self, sensor):
        return sensor if sensor in self.present else None

    def add_scenario_sensor(self, sensor, **values):
        self.calls.append(('add_scenario_sensor', sensor, values))
        self.present.add(sensor)

    def inject(self, sensor, kind, params, tick):
        self.calls.append(('inject', sensor, kind, params, tick))
        if self.inject_error:
            raise self.inject_error
        self.faults[sensor] = kind

    def clear(self, sensor, *, repair):
        self.calls.append(('clear', sensor, repair))
        self.faults.pop(sensor, None)


class FakeCity:
    def __init__(self, tick=10):
        self._sensors = FakeSensors()
        self.tick = tick
        self.quarantines = []
        self.commands = []
        self.fail_commands = False
        self._run_id = 'fake-run'
        self._epoch = datetime(2026, 10, 18, tzinfo=timezone.utc)
        self.events = []

    def _snapshot(self):
        return SimpleNamespace(context=SimpleNamespace(tick=self.tick))

    def _event(self, kind, details):
        self.events.append((kind, details))

    def _scenario_event(self, details):
        self._event('SCENARIO_LIFECYCLE', details)

    def quarantine_device(self, device, reason):
        self.quarantines.append((device, reason))

    def command(self, device, requested, *, command_id):
        self.commands.append((device, requested, command_id))
        if self.fail_commands:
            raise LiveError('DEVICE_UNAVAILABLE')


class ScenarioDataTests(unittest.TestCase):
    def setUp(self):
        load_scenarios.cache_clear()

    def test_person_b_ids_and_documented_aliases_resolve_to_same_scenario(self):
        self.assertIs(scenario('T5'), scenario('t5'))
        self.assertIs(scenario('SCN_02'), scenario('scn-2'))
        self.assertIs(scenario('scn_03'), scenario('SCN-03'))
        self.assertIs(scenario('scn-04'), scenario('scn_4'))
        self.assertIs(scenario('SCN_05'), scenario('scn-5'))
        self.assertGreaterEqual(len(load_scenarios()), 15)
        with self.assertRaisesRegex(LiveError, 'UNSUPPORTED_SCENARIO'):
            scenario('unknown')

    def test_fault_parameter_names_are_adapted_without_changing_fault_types(self):
        cases = (
            ('FALSE_READING', {'fixed_value': 180}, {'value': 180}),
            ('FALSE_READING', {'offset': 4}, {'offset': 4}),
            ('FALSE_READING', {'actual_value': 18}, {'actual_value': 18}),
            ('DRIFT', {'rate_per_tick': -2}, {'rate': -2}),
            ('NOISE', {'amplitude': 3}, {'amplitude': 3}),
            ('DELAYED', {'lag_ticks': 4}, {'lag_ticks': 4}),
            ('DROPOUT', {'duration_ticks': 5}, {'duration_ticks': 5}),
        )
        for kind, params, expected in cases:
            with self.subTest(kind=kind, params=params):
                self.assertEqual(_fault_params(kind, params), expected)
        self.assertEqual(_fault_params('STUCK', {'x': 1}), {'x': 1})


class ScenarioRunnerTests(unittest.TestCase):
    def setUp(self):
        self.city = FakeCity()
        self.runner = ScenarioRunner(self.city)

    def test_start_sets_scenario_specific_truth_sampling_and_fixture_data(self):
        t5 = self.runner.start('t5')
        self.assertEqual(t5.scenario_id, 'T5')
        self.assertEqual(self.city._sensors.inputs['PRT-WLV-01'], 40.0)
        self.assertIn(('force_sample', 'PRT-WLV-01', 11), self.city._sensors.calls)

        self.runner.done = True
        scn2 = self.runner.start('scn-02')
        self.assertEqual(scn2.scenario_id, 'SCN-02')
        self.runner.done = True

        scn3 = self.runner.start('scn-03')
        self.assertEqual(scn3.scenario_id, 'SCN-03')
        self.assertEqual(self.city._sensors.inputs['IND-TMP-01'], 71.0)
        self.runner.done = True

        self.runner.start('scn-04')
        self.runner.done = True
        scn5 = self.runner.start('scn-05')
        self.assertEqual(scn5.scenario_id, 'SCN-05')
        self.assertNotIn('RES-AIR-TEST-01', self.city._sensors.present)

    def test_real_scenario_triggers_are_events_not_synthetic_partner_decisions(self):
        self.runner.start('scn-02')
        self.runner.advance(11)
        self.assertIn(('set_scenario_input', 'SCN-02', 'PRT-WLV-01', 110.0),
                      self.city._sensors.calls)
        self.assertEqual(self.city.commands, [])
        self.assertIn('RECEIVED', [event[1]['event_type'] for event in self.city.events])

        self.runner.done = True
        self.runner.start('scn-03')
        self.runner.advance(12)
        injections = [call for call in self.city._sensors.calls if call[0] == 'inject']
        self.assertIn(('inject', 'DTN-TRF-01', 'STUCK', {}, 12), injections)
        self.assertIn(('inject', 'IND-TMP-01', 'DEAD', {}, 12), injections)

        self.runner.done = True
        self.runner.start('scn-04')
        self.runner.advance(11)
        self.assertEqual(self.city.quarantines, [])
        self.assertEqual(self.city.commands, [])

        self.runner.done = True
        self.runner.start('scn-05')
        self.runner.advance(15)
        self.assertEqual(self.city.commands, [])
        self.assertEqual(self.city.quarantines, [])
        self.assertTrue(any(event[1]['source'] == 'scenario_fixture'
                            for event in self.city.events if event[0] == 'SCENARIO_LIFECYCLE'))

    def test_t5_after_scenario_clear_and_finish_boundary(self):
        self.runner.start('t5')
        self.runner.advance(15)
        self.assertTrue(any(call[:3] == ('inject', 'PRT-WLV-01', 'FALSE_READING')
                            for call in self.city._sensors.calls))
        self.runner.advance(15)
        self.assertEqual(len([call for call in self.city._sensors.calls
                              if call[0] == 'inject' and call[1] == 'PRT-WLV-01']), 1)

        self.runner.finish(54)
        self.assertFalse(self.runner.done)
        self.runner.finish(55)
        self.assertTrue(self.runner.done)
        self.assertIn(('clear', 'PRT-WLV-01', True), self.city._sensors.calls)
        self.assertIn(('clear_scenario_inputs', 'T5'), self.city._sensors.calls)
        count = len(self.city._sensors.calls)
        self.runner.finish(100)
        self.assertEqual(len(self.city._sensors.calls), count)

    def test_custom_supported_actions_and_invalid_action(self):
        self.runner.scenario = Scenario('CUSTOM', 'test', 1, (
            Trigger(1, 'SET_GROUND_TRUTH', 'PRT-WLV-01', None, {'to_cm': 88}, '', False, False),
            Trigger(2, 'INJECT_FAULT', 'LOCAL-01', 'FALSE_READING', {'fixed_value': 4}, '', False, True),
            Trigger(3, 'CLEAR_FAULT', 'PRT-WLV-01', None, {}, '', False, False),
            Trigger(4, 'QUARANTINE', 'PRT-PMP-01', None, {}, 'bad sensor', False, False),
        ), {})
        self.runner.start_tick = 10
        self.runner.advance(11)
        self.assertIn(('set_scenario_input', 'CUSTOM', 'PRT-WLV-01', 88),
                      self.city._sensors.calls)
        self.runner.advance(12)
        self.assertNotIn('LOCAL-01', self.city._sensors.present)
        self.runner.advance(13)
        self.assertIn(('clear', 'PRT-WLV-01', True), self.city._sensors.calls)
        self.runner.advance(14)
        self.assertTrue(any(
            event[1]['event_type'] == 'GUARDIAN_PENDING'
            and event[1]['device_id'] == 'PRT-PMP-01'
            for event in self.city.events
        ))

        self.runner.scenario = Scenario('BAD', 'bad action', 1, (
            Trigger(1, 'INVENTED_ACTION', 'PRT-WLV-01', None, {}, '', False, False),
        ), {})
        self.runner.processed = set()
        self.runner.done = False
        with self.assertRaisesRegex(LiveError, 'UNSUPPORTED_OPERATION'):
            self.runner.advance(11)

    def test_local_fault_duplicate_is_idempotent_but_other_errors_propagate(self):
        trigger = Trigger(1, 'INJECT_FAULT', 'PRT-WLV-01', 'DEAD', {}, '', False, False)
        self.runner.scenario = Scenario('CUSTOM', 'duplicate', 1, (trigger,), {})
        self.runner.start_tick = 0
        self.city._sensors.inject_error = LiveError('FAULT_ALREADY_ACTIVE')
        self.runner.advance(1)

        self.runner.scenario = Scenario('CUSTOM', 'broken', 1, (trigger,), {})
        self.runner.processed = set()
        self.city._sensors.inject_error = LiveError('INVALID_ARGUMENT')
        with self.assertRaisesRegex(LiveError, 'INVALID_ARGUMENT'):
            self.runner.advance(1)

    def test_reset_and_empty_or_completed_runner_are_noops(self):
        self.runner.advance(0)
        self.runner.finish(0)
        self.runner.start('scn-04')
        self.runner.done = True
        self.runner.advance(11)
        self.runner.finish(11)
        self.runner.reset()
        self.assertIsNone(self.runner.scenario)
        self.assertIsNone(self.runner.start_tick)
        self.assertEqual(self.runner.processed, set())
        self.assertFalse(self.runner.done)


if __name__ == '__main__':
    unittest.main()
