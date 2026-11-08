import unittest
from unittest.mock import patch

from city_live.runtime import LiveCity
from twin_interface.facade import TwinFacade
from twin_observability.event_store import EventStore


class ScenarioOrchestrationTests(unittest.TestCase):
    def setUp(self):
        self.tokens = patch.dict(
            'os.environ',
            {
                'TWIN_TOKEN_BRAIN': 'scenario-test-brain',
                'TWIN_TOKEN_GUARDIAN': 'scenario-test-guardian',
                'TWIN_TOKEN_OPERATOR': 'scenario-test-operator',
            },
        )
        self.tokens.start()
        self.addCleanup(self.tokens.stop)
        self.city = LiveCity('scenario-orchestration-test')
        self.addCleanup(self.city.close)
        self.events = EventStore()
        self.facade = TwinFacade(self.city, event_store=self.events)

    def start(self, scenario_id, ticks=0):
        status = self.city.start_b_scenario(scenario_id)
        for _ in range(ticks):
            self.city.step()
        return status

    def command(self, device_id, command, params, request_id, scenario, **context):
        return self.facade.send_actuator_command(
            'scenario-test-brain',
            device_id,
            command,
            params,
            request_id,
            {
                'scenario_id': scenario['scenario_id'],
                'correlation_id': scenario['correlation_id'],
                **context,
            },
        )

    def timeline(self):
        return self.city.events()['events']

    def test_scn01_spoof_is_detected_and_blocks_unsafe_effect(self):
        scenario = self.start('t5', ticks=5)

        reading = self.city._sensors.latest['PRT-WLV-01']
        self.assertEqual(reading['actual_value'], 40.0)
        self.assertEqual(reading['reported_value'], 180.0)
        revision = self.city.status()['revision']

        response = self.command(
            'PRT-PMP-01',
            'set_speed',
            {'speed_pct': 80},
            'scenario-fake-flood-command',
            scenario,
        )

        self.assertFalse(response['ok'])
        self.assertEqual(self.city.status()['revision'], revision)
        self.assertTrue(any(
            row['details']['event_type'] == 'GUARDIAN_PENDING'
            for row in self.timeline()
            if row['type'] == 'SCENARIO_LIFECYCLE'
        ))

    def test_scn02_real_seeded_event_reaches_core_and_replays_idempotently(self):
        scenario = self.start('scn-02', ticks=1)
        reading = self.city._sensors.latest['PRT-WLV-01']
        self.assertEqual(reading['actual_value'], 110.0)
        self.assertEqual(reading['reported_value'], 110.0)

        frame = self.city.capture_dashboard_frame()
        self.assertEqual(frame['scenario']['scenario_id'], 'SCN-02')
        self.assertTrue(any(
            row['sensor_id'] == 'PRT-WLV-01'
            for row in frame['readings']
        ))

        context = {
            'scenario_id': scenario['scenario_id'],
            'correlation_id': scenario['correlation_id'],
        }
        first = self.facade.send_actuator_command(
            'scenario-test-brain', 'PRT-PMP-01', 'set_speed',
            {'speed_pct': 30}, 'scenario-flood-command', context,
        )
        duplicate = self.facade.send_actuator_command(
            'scenario-test-brain', 'PRT-PMP-01', 'set_speed',
            {'speed_pct': 30}, 'scenario-flood-command', context,
        )
        self.assertTrue(first['ok'])
        self.assertEqual(first, duplicate)
        self.assertEqual(self.city.status()['revision'], first['data']['effective_revision'])
        self.assertEqual(
            self.city._snapshot().physical_state.pump.effective_speed_fraction,
            0.3,
        )

        events = [
            row for row in self.timeline()
            if row['type'] == 'SCENARIO_LIFECYCLE'
        ]
        command = next(
            row['details'] for row in events
            if row['details']['event_type'] == 'COMMAND_EXECUTED'
        )
        sensor_event = next(
            row['details'] for row in events
            if row['details']['event_type'] == 'TWIN_UPDATED'
            and row['details']['device_id'] == 'PRT-WLV-01'
        )
        self.assertTrue(command['payload']['city_core_updated'])
        self.assertIn(sensor_event['event_id'], command['payload']['origin_event_ids'])
        self.assertEqual(command['correlation_id'], scenario['correlation_id'])
        self.assertTrue(any(
            event['event_type'] == 'COMMAND_EXECUTED'
            for event in self.events.get_latest(limit=100, role='operator')
        ))

    def test_scn03_requires_validated_backup_sensor_for_command(self):
        scenario = self.start('scn-03', ticks=2)
        traffic = self.city._sensors.latest['DTN-TRF-01']
        dead = self.city._sensors.latest['IND-TMP-01']
        backup = self.city._sensors.latest['IND-TMP-02']

        self.assertEqual((traffic['actual_value'], traffic['reported_value']), (55.0, 48.0))
        self.assertIsNone(dead['reported_value'])
        self.assertEqual(dead['status'], 'FAILED')
        self.assertEqual(backup['reported_value'], 70.0)
        revision = self.city.status()['revision']

        rejected = self.command(
            'IND-VLV-01', 'set_opening', {'open_pct': 25},
            'scenario-no-backup-command', scenario,
        )
        self.assertFalse(rejected['ok'])
        self.assertEqual(self.city.status()['revision'], revision)

        accepted = self.command(
            'IND-VLV-01', 'set_opening', {'open_pct': 25},
            'scenario-valid-backup-command', scenario,
            source_sensor_id='IND-TMP-02',
        )
        self.assertTrue(accepted['ok'])
        self.assertEqual(self.city.status()['revision'], revision + 1)

    def test_scn04_guardian_isolation_vetoes_primary_and_allows_existing_backup(self):
        scenario = self.start('scn-04', ticks=1)
        blocked = self.command(
            'PRT-PMP-01', 'set_speed', {'speed_pct': 25},
            'scenario-before-guardian', scenario,
        )
        self.assertFalse(blocked['ok'])

        isolated = self.facade.isolate_device(
            'scenario-test-guardian', 'PRT-PMP-01', 'integrity_check_failed'
        )
        self.assertTrue(isolated['ok'])
        rejected = self.command(
            'PRT-PMP-01', 'set_speed', {'speed_pct': 25},
            'scenario-quarantined-primary', scenario,
        )
        self.assertFalse(rejected['ok'])

        alias_backup = self.command(
            'PRT-PMP-02', 'set_speed', {'speed_pct': 25},
            'scenario-eligible-backup', scenario,
        )
        self.assertFalse(alias_backup['ok'])
        self.assertTrue(self.city.is_device_isolated('PRT-PMP-01'))
        self.assertEqual(
            self.city._snapshot().physical_state.pump.effective_speed_fraction,
            0.0,
        )

    def test_scn05_keeps_fixture_local_and_executes_traffic_response(self):
        scenario = self.start('scn-05', ticks=5)
        self.assertNotIn(
            'RES-AIR-TEST-01',
            {sensor.sensor_id for sensor in self.city._sensors.registry},
        )
        fixture_events = [
            row['details'] for row in self.timeline()
            if row['type'] == 'SCENARIO_LIFECYCLE'
            and row['details']['source'] == 'scenario_fixture'
        ]
        self.assertTrue(fixture_events)
        self.assertTrue(all(
            event['payload']['scenario_local_fixture']
            for event in fixture_events
        ))
        self.assertEqual(
            self.city._sensors.latest['DTN-TRF-01']['reported_value'],
            105.0,
        )

        result = self.command(
            'DTN-TLT-01', 'set_priority_corridor', {},
            'scenario-traffic-response', scenario,
        )
        self.assertTrue(result['ok'])
        self.assertEqual(
            self.city._snapshot().physical_state.signals['J-D1'].effective_mode,
            'priority',
        )

    def test_delayed_scenario_command_is_rejected_after_city_reset(self):
        scenario = self.start('scn-02', ticks=1)
        self.city.reset('scenario-orchestration-reset')
        new_scenario = self.start('scn-05', ticks=5)
        before = self.city._snapshot().physical_state.signals['J-D1'].effective_mode

        stale = self.facade.send_actuator_command(
            'scenario-test-brain',
            'DTN-TLT-01',
            'set_priority_corridor',
            {},
            'delayed-old-scenario-command',
            {
                'scenario_id': scenario['scenario_id'],
                'correlation_id': scenario['correlation_id'],
            },
        )

        self.assertFalse(stale['ok'])
        self.assertEqual(stale['error']['code'], 'INVALID_STATE')
        self.assertEqual(
            self.city._snapshot().physical_state.signals['J-D1'].effective_mode,
            before,
        )
        self.assertNotEqual(
            scenario['correlation_id'], new_scenario['correlation_id']
        )

    def test_scn06_brain_outage_blocks_command_without_decision_or_effect(self):
        scenario = self.start('scn-06', ticks=1)
        revision = self.city.status()['revision']

        response = self.command(
            'PRT-PMP-01', 'set_speed', {'speed_pct': 30},
            'scn06-brain-outage-command', scenario,
        )

        self.assertFalse(response['ok'])
        self.assertEqual(response['error']['code'], 'INVALID_STATE')
        self.assertEqual(self.city.status()['revision'], revision)
        events = [
            row['details'] for row in self.timeline()
            if row['type'] == 'SCENARIO_LIFECYCLE'
        ]
        self.assertTrue(any(
            event['event_type'] == 'PARTNER_CHANNEL_UNAVAILABLE'
            and event['payload']['partner'] == 'brain'
            for event in events
        ))
        self.assertTrue(any(
            event['event_type'] == 'PARTNER_REQUEST_BLOCKED'
            and event['payload']['request_id'] == 'scn06-brain-outage-command'
            and event['payload']['queued'] is False
            for event in events
        ))
        self.assertFalse(any(
            event['event_type'] == 'BRAIN_DECIDED'
            and event['payload'].get('request_id') == 'scn06-brain-outage-command'
            for event in events
        ))

    def test_scn06_guardian_outage_blocks_isolation_and_restore_calls(self):
        self.start('scn-06')
        initial_isolation = self.facade.isolate_device(
            'scenario-test-guardian', 'PRT-PMP-01', 'retain through outage'
        )
        self.assertTrue(initial_isolation['ok'])
        self.city.step()
        self.city.step()

        isolated = self.facade.isolate_device(
            'scenario-test-guardian', 'PRT-PMP-01', 'outage probe'
        )
        restored = self.facade.restore_device(
            'scenario-test-guardian', 'PRT-PMP-01'
        )
        operator_restore = self.facade.restore_device(
            'scenario-test-operator', 'PRT-PMP-01'
        )

        self.assertFalse(isolated['ok'])
        self.assertEqual(isolated['error']['code'], 'INVALID_STATE')
        self.assertFalse(restored['ok'])
        self.assertEqual(restored['error']['code'], 'INVALID_STATE')
        self.assertFalse(operator_restore['ok'])
        self.assertEqual(operator_restore['error']['code'], 'INVALID_STATE')
        self.assertTrue(self.city.is_device_isolated('PRT-PMP-01'))
        events = [
            row['details'] for row in self.timeline()
            if row['type'] == 'SCENARIO_LIFECYCLE'
        ]
        blocked = [
            event for event in events
            if event['event_type'] == 'PARTNER_REQUEST_BLOCKED'
            and event['payload']['partner'] == 'guardian'
        ]
        self.assertEqual(len(blocked), 3)

    def test_scn06_guardian_outage_fails_closed_for_actuator_commands(self):
        scenario = self.start('scn-06', ticks=3)
        revision = self.city.status()['revision']

        response = self.command(
            'PRT-PMP-01', 'set_speed', {'speed_pct': 30},
            'scn06-guardian-outage-command', scenario,
        )
        operator_response = self.facade.send_actuator_command(
            'scenario-test-operator',
            'PRT-PMP-01',
            'set_speed',
            {'speed_pct': 30},
            'scn06-guardian-outage-operator-command',
            {
                'scenario_id': scenario['scenario_id'],
                'correlation_id': scenario['correlation_id'],
            },
        )

        self.assertFalse(response['ok'])
        self.assertEqual(response['error']['code'], 'INVALID_STATE')
        self.assertFalse(operator_response['ok'])
        self.assertEqual(operator_response['error']['code'], 'INVALID_STATE')
        self.assertEqual(self.city.status()['revision'], revision)
        self.assertEqual(
            self.city._snapshot().physical_state.pump.effective_speed_fraction,
            0.0,
        )
        self.assertTrue(any(
            row['details']['event_type'] == 'ACTUATOR_AUTHORIZATION_BLOCKED'
            for row in self.timeline()
            if row['type'] == 'SCENARIO_LIFECYCLE'
        ))

    def test_scn06_brain_recovery_allows_fresh_authorized_command(self):
        scenario = self.start('scn-06', ticks=4)
        expected_revision = self.city.status()['revision']

        response = self.command(
            'PRT-PMP-01', 'set_speed', {'speed_pct': 30},
            'scn06-brain-recovered-command', scenario,
            expected_revision=expected_revision,
        )

        self.assertTrue(response['ok'])
        self.assertEqual(response['data']['effective_revision'], expected_revision + 1)
        self.assertEqual(
            self.city._snapshot().physical_state.pump.effective_speed_fraction,
            0.3,
        )
        self.assertTrue(any(
            row['details']['event_type'] == 'PARTNER_CHANNEL_RECOVERED'
            and row['details']['payload']['partner'] == 'brain'
            for row in self.timeline()
            if row['type'] == 'SCENARIO_LIFECYCLE'
        ))

    def test_scn06_guardian_recovery_restores_authorized_isolation_path(self):
        self.start('scn-06', ticks=4)

        isolated = self.facade.isolate_device(
            'scenario-test-guardian', 'PRT-PMP-01', 'recovered guardian test'
        )
        restored = self.facade.restore_device(
            'scenario-test-guardian', 'PRT-PMP-01'
        )

        self.assertTrue(isolated['ok'])
        self.assertTrue(restored['ok'])
        self.assertFalse(self.city.is_device_isolated('PRT-PMP-01'))
        events = [
            row['details'] for row in self.timeline()
            if row['type'] == 'SCENARIO_LIFECYCLE'
        ]
        self.assertTrue(any(
            event['event_type'] == 'PARTNER_CHANNEL_RECOVERED'
            and event['payload']['partner'] == 'guardian'
            for event in events
        ))
        self.assertTrue(any(
            event['event_type'] == 'GUARDIAN_REJECTED'
            and event['payload']['action'] == 'ISOLATED'
            for event in events
        ))

    def test_scn06_delayed_request_is_revalidated_after_recovery(self):
        scenario = self.start('scn-06')
        stale_revision = self.city.status()['revision']
        self.city.step()
        during_outage = self.command(
            'PRT-PMP-01', 'set_speed', {'speed_pct': 30},
            'scn06-delayed-command', scenario,
            expected_revision=stale_revision,
        )
        self.assertFalse(during_outage['ok'])

        for _ in range(3):
            self.city.step()
        current_revision = self.city.status()['revision']
        stale_replay = self.command(
            'PRT-PMP-01', 'set_speed', {'speed_pct': 30},
            'scn06-delayed-command', scenario,
            expected_revision=stale_revision,
        )

        self.assertFalse(stale_replay['ok'])
        self.assertEqual(stale_replay['error']['code'], 'STATE_CONFLICT')
        self.assertEqual(self.city.status()['revision'], current_revision)
        self.assertEqual(
            self.city._snapshot().physical_state.pump.effective_speed_fraction,
            0.0,
        )

    def test_scn06_duplicate_request_is_idempotent_and_payload_reuse_is_rejected(self):
        scenario = self.start('scn-06', ticks=4)
        revision = self.city.status()['revision']
        context = {
            'scenario_id': scenario['scenario_id'],
            'correlation_id': scenario['correlation_id'],
            'expected_revision': revision,
        }
        effect_counter = self.city._effect_counter

        first = self.facade.send_actuator_command(
            'scenario-test-brain', 'PRT-PMP-01', 'set_speed',
            {'speed_pct': 30}, 'scn06-duplicate-request', context,
        )
        duplicate = self.facade.send_actuator_command(
            'scenario-test-brain', 'PRT-PMP-01', 'set_speed',
            {'speed_pct': 30}, 'scn06-duplicate-request', context,
        )
        conflicting_duplicate = self.facade.send_actuator_command(
            'scenario-test-brain', 'PRT-PMP-01', 'set_speed',
            {'speed_pct': 50}, 'scn06-duplicate-request', context,
        )

        self.assertTrue(first['ok'])
        self.assertEqual(first, duplicate)
        self.assertFalse(conflicting_duplicate['ok'])
        self.assertEqual(
            conflicting_duplicate['error']['code'], 'IDEMPOTENCY_CONFLICT'
        )
        self.assertEqual(self.city.status()['revision'], revision + 1)
        self.assertEqual(self.city._effect_counter, effect_counter + 1)

    def test_scn06_event_correlation_tracks_failure_recovery_and_commands(self):
        scenario = self.start('scn-06')
        for _ in range(4):
            self.city.step()
        self.command(
            'PRT-PMP-01', 'set_speed', {'speed_pct': 30},
            'scn06-correlation-command', scenario,
        )

        events = [
            row['details'] for row in self.timeline()
            if row['type'] == 'SCENARIO_LIFECYCLE'
        ]
        self.assertTrue(events)
        self.assertEqual(
            {event['correlation_id'] for event in events},
            {scenario['correlation_id']},
        )
        event_ids = [event['event_id'] for event in events]
        self.assertEqual(len(event_ids), len(set(event_ids)))
        event_types = [event['event_type'] for event in events]
        for expected in (
            'PARTNER_CHANNEL_AVAILABLE',
            'PARTNER_CHANNEL_UNAVAILABLE',
            'PARTNER_CHANNEL_RECOVERED',
            'COMMAND_EXECUTED',
        ):
            self.assertIn(expected, event_types)


if __name__ == '__main__':
    unittest.main()
