"""Run an evidence-producing integration campaign against isolated LiveCity owners."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from city_core.transitions import plain
from city_live.records import LiveError
from city_live.runtime import LiveCity
from twin_interface.facade import TwinFacade
from twin_observability.event_store import EventStore
from twin_observability.scenario_log import ScenarioLogger

SCENARIOS = (
    ('SCN-01', 't5', 45),
    ('SCN-02', 'scn-02', 3),
    ('SCN-03', 'scn-03', 5),
    ('SCN-04', 'scn-04', 2),
    ('SCN-05', 'scn-05', 7),
    ('SCN-06', 'scn-06', 5),
)
PRIVATE_AUDIT_FIELDS = frozenset({
    'actual_value', 'true_value', 'true_level', 'sensor_value', 'delta',
    'fault', 'noise', 'drift_rate', 'lag_ticks', 'bias', 'ground_truth',
})


def json_write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(plain(value), indent=2, sort_keys=True, allow_nan=False) + '\n',
        encoding='utf-8',
    )


def physical_state(city):
    return plain(city._snapshot().physical_state)


def command_state(city):
    state = city._snapshot().physical_state
    return {
        'revision': city.status()['revision'],
        'pump': plain(state.pump),
        'valve': plain(state.valve),
        'signals': plain(state.signals),
    }


def lifecycle_events(city):
    return [
        plain(event['details'])
        for event in city.events()['events']
        if event['type'] == 'SCENARIO_LIFECYCLE'
    ]


def contains_private_audit_field(value):
    if isinstance(value, dict):
        return any(
            key in PRIVATE_AUDIT_FIELDS or contains_private_audit_field(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(contains_private_audit_field(item) for item in value)
    return False


def audit_log_is_sanitized(path):
    return all(
        not contains_private_audit_field(json.loads(line))
        for line in path.read_text(encoding='utf-8').splitlines()
        if line.strip()
    )


def save_owner_evidence(directory, label, city, store, frame=None):
    json_write(directory / f'{label}-city-events.json', city.events()['events'])
    json_write(directory / f'{label}-event-store.json', store.get_latest(10000, 'operator'))
    if frame is not None:
        json_write(directory / f'{label}-dashboard-frame.json', frame)


class Campaign:
    def __init__(self, evidence_dir):
        self.evidence_dir = evidence_dir
        self.audit_dir = evidence_dir / 'audit'
        self.audit_dir.mkdir(parents=True, exist_ok=False)
        self.state_dir = evidence_dir / 'state'
        self.state_dir.mkdir()
        self.report = {
            'schema_version': 'scenario-integration-campaign-1',
            'started_at': datetime.now(timezone.utc).isoformat(),
            'repository_root': str(ROOT),
            'evidence_directory': str(evidence_dir),
            'statuses': ['PASS', 'FAIL', 'BLOCKED', 'SKIPPED', 'NOT TESTED'],
            'partner_service_boundary': {
                'brain': 'No Brain service/client is present in this repository. Campaign calls to TwinFacade are controlled local test-client calls, not actual Brain service responses.',
                'guardian': 'No Guardian service/client is present in this repository. Campaign calls to TwinFacade are controlled local test-client calls, not actual Guardian service responses.',
                'kafka': 'No Kafka component is configured or present in this repository.',
            },
            'parts': {},
            'limitations': [],
        }
        self._previous_env = {
            key: os.environ.get(key)
            for key in ('TWIN_TOKEN_BRAIN', 'TWIN_TOKEN_GUARDIAN')
        }
        os.environ['TWIN_TOKEN_BRAIN'] = f'campaign-brain-{uuid.uuid4().hex}'
        os.environ['TWIN_TOKEN_GUARDIAN'] = f'campaign-guardian-{uuid.uuid4().hex}'
        self.brain_token = os.environ['TWIN_TOKEN_BRAIN']
        self.guardian_token = os.environ['TWIN_TOKEN_GUARDIAN']

    def close(self):
        for key, value in self._previous_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def owner(self, label, run_id):
        database_path = self.state_dir / f'{label}.sqlite3'
        city = LiveCity(run_id, persistence_path=database_path)
        city._campaign_database_path = database_path
        store = EventStore()
        logger = ScenarioLogger(label, run_id, log_dir=self.audit_dir)
        facade = TwinFacade(city, event_store=store, scenario_logger=logger)
        logger.log_start(config={'campaign': True, 'label': label})
        return city, store, logger, facade

    def run_to_window_end(self, city, scenario_id, max_tick):
        scenario = city.start_b_scenario(scenario_id)
        while city.status()['sim_tick'] < max_tick:
            city.step()
        return scenario

    def brain_command(self, facade, city, scenario, device_id, command, params,
                      request_id, **context):
        return facade.send_actuator_command(
            self.brain_token,
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

    def check(self, results, name, condition, evidence=''):
        status = 'PASS' if condition else 'FAIL'
        results.append({'check': name, 'status': status, 'evidence': evidence})
        if not condition:
            raise AssertionError(f'{name}: {evidence}')

    def scenario_action(self, facade, city, scenario, scenario_id):
        before = command_state(city)
        actions = []
        if scenario_id == 'SCN-01':
            actions.append(self.brain_command(
                facade, city, scenario, 'PRT-PMP-02', 'set_speed',
                {'speed_pct': 80}, f'{scenario_id.lower()}-spoofed-command',
            ))
        elif scenario_id == 'SCN-02':
            actions.append(self.brain_command(
                facade, city, scenario, 'PRT-PMP-01', 'set_speed',
                {'speed_pct': 30}, f'{scenario_id.lower()}-pump-command',
            ))
            actions.append(self.brain_command(
                facade, city, scenario, 'PRT-PMP-01', 'set_speed',
                {'speed_pct': 30}, f'{scenario_id.lower()}-pump-command',
            ))
        elif scenario_id == 'SCN-03':
            actions.append(self.brain_command(
                facade, city, scenario, 'DTN-TLT-01', 'set_mode',
                {'mode': 'priority'}, f'{scenario_id.lower()}-no-traffic-backup',
            ))
            actions.append(self.brain_command(
                facade, city, scenario, 'DTN-TLT-01', 'set_mode',
                {'mode': 'priority'}, f'{scenario_id.lower()}-unrelated-industrial-backup',
                source_sensor_id='IND-TMP-02',
            ))
        elif scenario_id == 'SCN-04':
            actions.append(self.brain_command(
                facade, city, scenario, 'PRT-PMP-01', 'set_speed',
                {'speed_pct': 25}, f'{scenario_id.lower()}-pending-guardian',
            ))
            actions.append(facade.isolate_device(
                self.guardian_token, 'PRT-PMP-01', 'campaign quarantine',
            ))
            actions.append(self.brain_command(
                facade, city, scenario, 'PRT-PMP-01', 'set_speed',
                {'speed_pct': 25}, f'{scenario_id.lower()}-isolated-primary',
            ))
            actions.append(self.brain_command(
                facade, city, scenario, 'PRT-PMP-02', 'set_speed',
                {'speed_pct': 25}, f'{scenario_id.lower()}-aliased-backup',
            ))
        elif scenario_id == 'SCN-06':
            actions.append(self.brain_command(
                facade, city, scenario, 'PRT-PMP-01', 'set_speed',
                {'speed_pct': 30}, f'{scenario_id.lower()}-recovered-command',
                expected_revision=city.status()['revision'],
            ))
        else:
            actions.append(self.brain_command(
                facade, city, scenario, 'DTN-TLT-01',
                'set_priority_corridor', {},
                f'{scenario_id.lower()}-traffic-signal',
            ))
        after = command_state(city)
        return before, after, actions

    def individual_scenarios(self):
        results = []
        for index, (scenario_id, alias, end_tick) in enumerate(SCENARIOS, start=1):
            label = f'individual-{scenario_id.lower()}'
            run_id = f'campaign-{index}-{uuid.uuid4().hex[:12]}'
            city = store = logger = None
            try:
                city, store, logger, facade = self.owner(label, run_id)
                scenario = self.run_to_window_end(city, alias, end_tick)
                before, after, actions = self.scenario_action(
                    facade, city, scenario, scenario_id
                )
                frame = city.capture_dashboard_frame()
                events = lifecycle_events(city)
                kinds = {event['event_type'] for event in events}
                sensor_events = [
                    event for event in events
                    if event['event_type'] == 'TWIN_UPDATED'
                ]
                command_events = [
                    event for event in events
                    if event['event_type'] in (
                        'BRAIN_DECIDED', 'COMMAND_PENDING',
                        'COMMAND_EXECUTED', 'COMMAND_REJECTED',
                    )
                ]
                expected_effect = scenario_id in (
                    'SCN-02', 'SCN-05', 'SCN-06',
                )
                effect_applied = after['revision'] > before['revision']
                outcomes = [
                    {'ok': row.get('ok'), 'error': row.get('error')}
                    for row in actions
                ]
                checks = []
                twin_scenario_id = 'T5' if scenario_id == 'SCN-01' else scenario_id
                self.check(checks, 'scenario_received', 'RECEIVED' in kinds)
                if scenario_id in ('SCN-04', 'SCN-06'):
                    checks.append({
                        'check': 'sensor_state_update',
                        'status': 'NOT TESTED',
                        'evidence': (
                            'SCN-04 defines an actuator quarantine trigger and no sensor event.'
                            if scenario_id == 'SCN-04'
                            else 'SCN-06 defines communication outage/recovery triggers, not a sensor event.'
                        ),
                    })
                else:
                    self.check(
                        checks, 'Twin_update_observed', bool(sensor_events)
                    )
                if scenario_id == 'SCN-06':
                    channel_events = {
                        event['event_type'] for event in events
                        if event['event_type'] in (
                            'PARTNER_CHANNEL_UNAVAILABLE',
                            'PARTNER_CHANNEL_RECOVERED',
                        )
                    }
                    self.check(
                        checks, 'controlled_channel_failure_and_recovery',
                        channel_events == {
                            'PARTNER_CHANNEL_UNAVAILABLE',
                            'PARTNER_CHANNEL_RECOVERED',
                        },
                        f'channel_lifecycle_events={sorted(channel_events)}',
                    )
                self.check(
                    checks, 'dashboard_frame_current',
                    frame['scenario']['scenario_id'] == twin_scenario_id
                    and frame['scenario']['correlation_id'] == scenario['correlation_id'],
                )
                self.check(
                    checks, 'correlation_consistent',
                    all(event['correlation_id'] == scenario['correlation_id']
                        for event in events),
                )
                self.check(
                    checks, 'command_lifecycle_present',
                    bool(command_events),
                    'Local controlled Brain-role command calls exercise TwinFacade; not external Brain service calls.',
                )
                if scenario_id == 'SCN-03':
                    rejection_reasons = [
                        row.get('error', {}).get('details', {}).get('reason')
                        for row in actions
                    ]
                    self.check(
                        checks, 'scenario_expected_safe_rejections',
                        rejection_reasons == [
                            'SCENARIO_GUARDIAN_PENDING', 'SCENARIO_GUARDIAN_PENDING',
                        ],
                        f'actual_rejection_reasons={rejection_reasons}; '
                        'DTN-TLT-01 is supported and linked to frozen DTN-TRF-01, '
                        'which has no registered backup; unrelated IND-TMP-02 '
                        'cannot clear the traffic safety gate.',
                    )
                self.check(
                    checks, 'expected_effect_semantics',
                    effect_applied == expected_effect,
                    f'before_revision={before["revision"]}; after_revision={after["revision"]}; '
                    f'expected_effect={expected_effect}; outcomes={outcomes}; '
                    'SCN-06 submits a fresh command only after channel recovery; no outage request is queued.',
                )
                self.check(checks, 'audit_log_written',
                           logger.health()['status'] == 'UP' and logger.line_count > 1)
                self.check(
                    checks, 'audit_log_sanitized',
                    audit_log_is_sanitized(logger.log_path),
                )
                private_events = city.events()['events']
                event_sequences = [event['seq'] for event in private_events]
                self.check(
                    checks, 'event_order_monotonic',
                    event_sequences == sorted(event_sequences),
                )

                guardian_call = scenario_id == 'SCN-04'
                result = {
                    'scenario_id': scenario_id,
                    'overall': 'FAIL' if any(
                        row['status'] == 'FAIL' for row in checks
                    ) else 'PASS',
                    'checks': checks,
                    'partner_integration': {
                        'brain_service': 'BLOCKED',
                        'guardian_service': 'BLOCKED' if not guardian_call else 'NOT TESTED',
                        'test_client_calls_are_external_service_responses': False,
                        'guardian_test_call': 'controlled local TwinFacade role call' if guardian_call else None,
                    },
                    'guardian_lifecycle': {
                        'status': 'CONTROLLED_TEST_CALL_ONLY' if guardian_call else 'BLOCKED',
                        'pending_marker_observed': 'GUARDIAN_PENDING' in kinds,
                        'real_guardian_service_verdict': False,
                    },
                    'brain_lifecycle': {
                        'status': 'CONTROLLED_TEST_CALL_ONLY' if command_events else 'BLOCKED',
                        'brain_decision_events': sum(
                            event['event_type'] == 'BRAIN_DECIDED'
                            for event in command_events
                        ),
                        'real_brain_service_response': False,
                    },
                    'lifecycle_types': sorted(kinds),
                    'sensor_event_ids': [event['event_id'] for event in sensor_events],
                    'command_events': command_events,
                    'actions': outcomes,
                    'before_city_state': before,
                    'after_city_state': after,
                    'dashboard': 'PASS: private operator frame includes current scenario and readings',
                    'dashboard_browser_render': 'NOT TESTED',
                    'audit_log': str(logger.log_path),
                    'correlation_id': scenario['correlation_id'],
                    'audit_line_count': logger.line_count,
                }
                save_owner_evidence(
                    self.evidence_dir / 'individual', label, city, store, frame
                )
                results.append(result)
            except Exception as error:
                results.append({
                    'scenario_id': scenario_id,
                    'overall': 'FAIL',
                    'error': f'{type(error).__name__}: {error}',
                })
            finally:
                if logger is not None:
                    logger.close()
                if city is not None:
                    city.close()
        self.report['parts']['A_individual'] = results

    def sequential_execution(self):
        label = 'sequential-five-scenarios'
        run_id = f'sequential-{uuid.uuid4().hex[:12]}'
        city, store, logger, facade = self.owner(label, run_id)
        results = []
        previous_scenario = None
        previous_run_id = None
        try:
            for index, (scenario_id, alias, end_tick) in enumerate(SCENARIOS):
                prior_audit_count = logger.line_count
                prior_event_count = store.total_recorded
                if index:
                    old_context = {
                        'scenario_id': previous_scenario['scenario_id'],
                        'correlation_id': previous_scenario['correlation_id'],
                        'run_id': previous_run_id,
                    }
                    city.reset(f'sequential-{index}-{uuid.uuid4().hex[:8]}')
                scenario = self.run_to_window_end(city, alias, end_tick)
                before, after, actions = self.scenario_action(
                    facade, city, scenario, scenario_id
                )
                stale_response = None
                if index:
                    stale_response = facade.send_actuator_command(
                        self.brain_token, 'DTN-TLT-01',
                        'set_priority_corridor', {},
                        f'stale-{scenario_id.lower()}-{uuid.uuid4().hex[:8]}',
                        old_context,
                    )
                    if stale_response['ok']:
                        raise AssertionError('stale prior-scenario command was accepted')
                events = lifecycle_events(city)
                current_ids = {row['correlation_id'] for row in events}
                if current_ids != {scenario['correlation_id']}:
                    raise AssertionError(f'wrong scenario correlation set: {current_ids}')
                if index and (
                    logger.line_count <= prior_audit_count
                    or store.total_recorded <= prior_event_count
                ):
                    raise AssertionError('reset lost the historical audit/event evidence')
                if city.status()['pending_actions'] != 0:
                    raise AssertionError('a scheduled action remained pending after the scenario window')
                result = {
                    'scenario_id': scenario_id,
                    'status': 'PASS',
                    'run_id': city.status()['run_id'],
                    'correlation_id': scenario['correlation_id'],
                    'before_city_state': before,
                    'after_city_state': after,
                    'action_outcomes': [
                        {'ok': row.get('ok'), 'error': row.get('error')}
                        for row in actions
                    ],
                    'stale_prior_command': (
                        {'ok': stale_response['ok'],
                         'error': stale_response.get('error', {}).get('code')}
                        if stale_response else None
                    ),
                    'historical_event_store_count': store.total_recorded,
                    'historical_audit_lines': logger.line_count,
                    'no_pending_scheduled_actions': city.status()['pending_actions'] == 0,
                    'history_preserved_from_previous_scenario': (
                        None if not index else
                        logger.line_count > prior_audit_count
                        and store.total_recorded > prior_event_count
                    ),
                }
                results.append(result)
                previous_scenario = scenario
                previous_run_id = city.status()['run_id']
                frame = city.capture_dashboard_frame()
                save_owner_evidence(
                    self.evidence_dir / 'sequential',
                    f'{index + 1:02d}-{scenario_id.lower()}',
                    city, store, frame,
                )
            logger.log_end(city.status()['sim_tick'], {'scenario_count': len(results)})
            self.report['parts']['B_sequential'] = {
                'overall': 'PASS' if len(results) == len(SCENARIOS) else 'FAIL',
                'runs': results,
                'historical_audit_log': str(logger.log_path),
                'historical_audit_line_count': logger.line_count,
                'event_store_retained_across_supported_resets': store.total_recorded,
                'pending_commands': 'No command queue exists in this in-process command path; stale correlated command rejected after each reset.',
            }
        except Exception as error:
            self.report['parts']['B_sequential'] = {
                'overall': 'FAIL',
                'runs': results,
                'error': f'{type(error).__name__}: {error}',
            }
        finally:
            logger.close()
            city.close()

    def cross_scenario_synchronization(self):
        cases = []
        city = store = logger = None
        try:
            city, store, logger, facade = self.owner(
                'cross-synchronization', f'cross-{uuid.uuid4().hex[:12]}'
            )
            scenario = self.run_to_window_end(city, 'scn-02', 1)
            initial_event_count = len(lifecycle_events(city))
            city._scenario_runner.advance(1)
            duplicate_event_count = len(lifecycle_events(city))
            cases.append({
                'case': 'duplicate_trigger_delivery',
                'status': 'PASS' if initial_event_count == duplicate_event_count else 'FAIL',
                'event_count_before': initial_event_count,
                'event_count_after_duplicate_delivery': duplicate_event_count,
            })

            city.sensor_fault('PRT-WLV-01', 'FALSE_READING', {'value': 180})
            city._sensors.force_sample('PRT-WLV-01', city.status()['sim_tick'] + 1)
            city.step()
            before = command_state(city)
            raced_id = 'cross-concurrent-idempotent-command'

            def same_command(_):
                return self.brain_command(
                    facade, city, scenario, 'PRT-PMP-01', 'set_speed',
                    {'speed_pct': 50}, raced_id,
                )

            with ThreadPoolExecutor(max_workers=8) as executor:
                duplicates = list(executor.map(same_command, range(8)))
            after = command_state(city)
            expected_single_result = not duplicates[0]['ok']
            cases.append({
                'case': 'concurrent_duplicate_command_with_active_spoof',
                'status': 'PASS' if all(row == duplicates[0] for row in duplicates)
                          and expected_single_result and before == after else 'FAIL',
                'call_count': len(duplicates),
                'all_responses_identical': all(row == duplicates[0] for row in duplicates),
                'response': duplicates[0],
                'before_city_state': before,
                'after_city_state': after,
                'effect_count_delta': after['revision'] - before['revision'],
            })

            city.reset(f'cross-compete-{uuid.uuid4().hex[:8]}')
            competing = self.run_to_window_end(city, 'scn-02', 1)
            expected_revision = city.status()['revision']

            def distinct_command(index):
                return self.brain_command(
                    facade, city, competing, 'PRT-PMP-01', 'set_speed',
                    {'speed_pct': 20 + index}, f'competing-{index}',
                    expected_revision=expected_revision,
                )

            with ThreadPoolExecutor(max_workers=2) as executor:
                outcomes = list(executor.map(distinct_command, (1, 2)))
            success_count = sum(row['ok'] for row in outcomes)
            conflict_count = sum(
                (row.get('error') or {}).get('code') == 'STATE_CONFLICT'
                for row in outcomes
            )
            competed_events = lifecycle_events(city)
            competed_types = [
                event['event_type'] for event in competed_events
            ]
            decision_index = competed_types.index('BRAIN_DECIDED')
            pending_index = competed_types.index('COMMAND_PENDING')
            executed_index = competed_types.index('COMMAND_EXECUTED')
            ordering_ok = decision_index < pending_index < executed_index
            cases.append({
                'case': 'competing_commands_same_expected_revision',
                'status': 'PASS' if success_count == 1 and conflict_count == 1
                          and ordering_ok else 'FAIL',
                'outcomes': outcomes,
                'revision_before': expected_revision,
                'revision_after': city.status()['revision'],
                'decision_pending_execution_order': ordering_ok,
                'ordered_lifecycle': competed_types,
                'unauthorized_execution': False,
            })

            city.reset(f'cross-flood-fault-{uuid.uuid4().hex[:8]}')
            emergency = self.run_to_window_end(city, 'scn-02', 1)
            city.sensor_fault('PRT-WLV-01', 'DEAD', {})
            city._sensors.force_sample('PRT-WLV-01', city.status()['sim_tick'] + 1)
            city.step()
            before = command_state(city)
            blocked = self.brain_command(
                facade, city, emergency, 'PRT-PMP-01', 'set_speed',
                {'speed_pct': 35}, 'sensor-failure-during-flood',
            )
            after = command_state(city)
            cases.append({
                'case': 'sensor_failure_during_emergency',
                'status': 'PASS' if not blocked['ok'] and before == after else 'FAIL',
                'command_response': blocked,
                'before_city_state': before,
                'after_city_state': after,
            })

            city.reset(f'cross-quarantine-{uuid.uuid4().hex[:8]}')
            emergency = self.run_to_window_end(city, 'scn-02', 1)
            isolated = facade.isolate_device(
                self.guardian_token, 'PRT-PMP-01', 'competing emergency test'
            )
            before = command_state(city)
            primary = self.brain_command(
                facade, city, emergency, 'PRT-PMP-01', 'set_speed',
                {'speed_pct': 40}, 'preferred-quarantined-actuator',
            )
            alias = self.brain_command(
                facade, city, emergency, 'PRT-PMP-02', 'set_speed',
                {'speed_pct': 40}, 'same-target-backup-alias',
            )
            after = command_state(city)
            cases.append({
                'case': 'preferred_actuator_quarantined_during_emergency',
                'status': 'PASS' if isolated['ok'] and not primary['ok']
                          and not alias['ok'] and before == after else 'FAIL',
                'guardian_call': 'controlled local TwinFacade role call',
                'primary_response': primary,
                'alias_response': alias,
                'before_city_state': before,
                'after_city_state': after,
            })
            save_owner_evidence(
                self.evidence_dir / 'cross-synchronization',
                'cross-synchronization',
                city, store, city.capture_dashboard_frame(),
            )
        except Exception as error:
            cases.append({
                'case': 'cross-scenario-campaign-execution',
                'status': 'FAIL',
                'error': f'{type(error).__name__}: {error}',
            })
        finally:
            if logger is not None:
                logger.close()
            if city is not None:
                city.close()
        self.report['parts']['C_cross_scenario'] = {
            'overall': 'PASS' if cases and all(
                case['status'] == 'PASS' for case in cases
            ) else 'FAIL',
            'cases': cases,
            'concurrency_model': 'Isolated LiveCity owner; controlled calls through its serialized facade; no assumed parallel physics.',
        }

    def failure_and_recovery(self):
        cases = [
            {
                'case': 'Brain service unavailable',
                'status': 'BLOCKED',
                'reason': 'No Brain service/client is present; only explicit local test caller calls are available. This is not a partner outage test.',
            },
            {
                'case': 'Guardian service unavailable',
                'status': 'BLOCKED',
                'reason': 'No Guardian service/client is present; only explicit local test caller calls are available. This is not a partner outage test.',
            },
            {
                'case': 'Kafka interruption',
                'status': 'NOT TESTED',
                'reason': 'Kafka is not integrated or configured in the inspected repository.',
            },
            {
                'case': 'Real process crash / restart',
                'status': 'PENDING',
                'reason': 'The dedicated abrupt-process restart probe is executed below and its evidence is included.',
            },
        ]
        city = store = logger = None
        try:
            city, store, logger, facade = self.owner(
                'failure-recovery', f'recovery-{uuid.uuid4().hex[:12]}'
            )
            scenario = self.run_to_window_end(city, 'scn-02', 1)
            before = command_state(city)
            failed = self.brain_command(
                facade, city, scenario, 'PRT-PMP-01', 'set_speed',
                {'speed_pct': 101}, 'failed-actuator-validation',
            )
            after = command_state(city)
            cases.append({
                'case': 'failed actuator validation',
                'status': 'PASS' if not failed['ok'] and before == after else 'FAIL',
                'response': failed,
                'before_city_state': before,
                'after_city_state': after,
            })
            first_effect = self.brain_command(
                facade, city, scenario, 'PRT-PMP-01', 'set_speed',
                {'speed_pct': 30}, 'restart-idempotency-probe',
            )
            pre_restart_state = command_state(city)
            original_run_id = city.status()['run_id']
            logger.close()
            logger = None
            city.close()
            restarted = LiveCity(
                original_run_id,
                persistence_path=city._campaign_database_path,
            )
            effect_count_before_replay = restarted._effect_count()
            replay = restarted.execute_actuator_command(
                'PRT-PMP-01',
                'set_speed', {'speed_pct': 30},
                command_id='restart-idempotency-probe',
                run_id=original_run_id,
                correlation_id=scenario['correlation_id'],
                scenario_id=scenario['scenario_id'],
            )
            effect_count_after_replay = restarted._effect_count()
            restart_state = command_state(restarted)
            replay_matches = all(
                replay.get(key) == first_effect['data'].get(key)
                for key in ('status', 'effective_revision', 'effect_id')
            )
            cases.append({
                'case': 'in-memory owner recreation and idempotency survival',
                'status': 'PASS' if (
                    first_effect['ok']
                    and replay_matches
                    and effect_count_after_replay == effect_count_before_replay
                    and restart_state == pre_restart_state
                ) else 'FAIL',
                'evidence': 'The recreated owner used the same SQLite WAL database; the stored receipt was returned and replay-restored CityCore state did not change again.',
                'first_process_command_response': first_effect,
                'before_restart_city_state': pre_restart_state,
                'recreated_owner_command_response': replay,
                'recreated_owner_state': restart_state,
                'repeated_request_returned_original_result': replay_matches,
                'effect_count_before_replay': effect_count_before_replay,
                'effect_count_after_replay': effect_count_after_replay,
            })
            restarted.close()
            durable_evidence = self.evidence_dir / 'process-restart'
            process_result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / 'tools' / 'run_durable_idempotency_evidence.py'),
                    '--evidence-dir',
                    str(durable_evidence),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=True,
                timeout=60,
            )
            process_summary = json.loads(process_result.stdout)
            cases[3] = {
                'case': 'Real process crash / restart',
                'status': process_summary['result'],
                'evidence_directory': process_summary['evidence_directory'],
                'request_id': process_summary['request_id'],
                'correlation_id': process_summary['correlation_id'],
                'first_process_revision': process_summary['first_process']['revision_after'],
                'second_process_revision_before': process_summary['second_process']['revision_before'],
                'second_process_revision_after': process_summary['second_process']['revision_after'],
                'same_result_returned': (
                    process_summary['first_process']['result']
                    == process_summary['second_process']['result']
                ),
                'process_exit_codes': [
                    process_summary['process_one_exit_code'],
                    process_summary['process_two_exit_code'],
                ],
            }
        except Exception as error:
            cases.append({
                'case': 'failure-recovery-campaign-execution',
                'status': 'FAIL',
                'error': f'{type(error).__name__}: {error}',
            })
        finally:
            if logger is not None:
                logger.close()
            if city is not None and city.status()['state'] != 'CLOSED':
                city.close()
        self.report['parts']['D_failure_recovery'] = {
            'overall': 'FAIL' if any(case['status'] == 'FAIL' for case in cases)
                       else 'BLOCKED',
            'cases': cases,
        }

    def finish(self):
        statuses = []
        for part in self.report['parts'].values():
            if isinstance(part, list):
                statuses.extend(row.get('overall', 'FAIL') for row in part)
            else:
                statuses.append(part.get('overall', 'FAIL'))
        self.report['overall'] = (
            'FAIL' if 'FAIL' in statuses
            else 'BLOCKED' if 'BLOCKED' in statuses
            else 'PASS'
        )
        self.report['completed_at'] = datetime.now(timezone.utc).isoformat()
        json_write(self.evidence_dir / 'campaign.json', self.report)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--evidence-root',
        type=Path,
        default=ROOT / 'test-evidence',
        help='Parent directory for a new, never-overwritten campaign evidence folder.',
    )
    args = parser.parse_args()
    root = args.evidence_root.resolve()
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    evidence_dir = root / f'scenario-campaign-{stamp}-{uuid.uuid4().hex[:8]}'
    evidence_dir.mkdir(parents=True, exist_ok=False)
    campaign = Campaign(evidence_dir)
    try:
        campaign.individual_scenarios()
        campaign.sequential_execution()
        campaign.cross_scenario_synchronization()
        campaign.failure_and_recovery()
        campaign.report['reproduction_commands'] = [
            'python tools/run_scenario_campaign.py --evidence-root test-evidence',
            'python tools/run_scn06_campaign.py',
            'python tools/run_durable_idempotency_evidence.py',
            'python tools/run_live_checks.py --evidence <new-temporary-evidence-directory>',
            'python tools/run_dashboard_checks.py --evidence <new-temporary-evidence-directory>',
            'python tools/run_checks.py --evidence <new-temporary-evidence-directory>',
        ]
        campaign.report['limitations'] = [
            'Brain and Guardian partner service processes are absent; controlled local TwinFacade role calls prove the Twin boundary only.',
            'No Guardian request-bound positive approval endpoint exists; pending is not approval.',
            'SCN-04 PRT-PMP-01 and PRT-PMP-02 map to the same CityCore physical target, so the backup cannot be used after primary quarantine.',
            'SCN-04 declares no sensor trigger, so a sensor-state update is not applicable to that scenario definition.',
            'The database persists CityCore replay transitions and actuator receipts, but runtime scenario/sensor adapters, event-store history, and pending scenario state remain process-local.',
            'Kafka is not present; Kafka failure recovery is not tested.',
            'This Python runner does not exercise browser rendering; see browser-validation.json for the separately executed dashboard session.',
        ]
        campaign.finish()
    except Exception as error:
        campaign.report['campaign_error'] = f'{type(error).__name__}: {error}'
        campaign.finish()
        raise
    finally:
        campaign.close()
    print(json.dumps({
        'overall': campaign.report['overall'],
        'evidence_directory': str(evidence_dir),
        'report': str(evidence_dir / 'campaign.json'),
        'individual': [
            {'scenario_id': row['scenario_id'], 'overall': row['overall']}
            for row in campaign.report['parts'].get('A_individual', [])
        ],
        'sequential': campaign.report['parts'].get('B_sequential', {}).get('overall'),
        'cross_scenario': campaign.report['parts'].get('C_cross_scenario', {}).get('overall'),
        'failure_recovery': campaign.report['parts'].get('D_failure_recovery', {}).get('overall'),
    }, indent=2))
    return 0 if campaign.report['overall'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
