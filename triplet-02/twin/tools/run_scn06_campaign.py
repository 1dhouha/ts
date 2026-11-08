"""Run SCN-06 with local role callers and save a unique evidence bundle."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from city_core.transitions import plain
from city_live.runtime import LiveCity
from twin_interface.facade import TwinFacade
from twin_observability.event_store import EventStore
from twin_observability.scenario_log import ScenarioLogger

PRIVATE_FIELDS = frozenset({
    'actual_value', 'true_value', 'true_level', 'sensor_value', 'delta',
    'fault', 'noise', 'drift_rate', 'lag_ticks', 'bias', 'ground_truth',
})


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(plain(value), indent=2, sort_keys=True, allow_nan=False) + '\n',
        encoding='utf-8',
    )


def has_private_field(value):
    if isinstance(value, dict):
        return any(
            key in PRIVATE_FIELDS or has_private_field(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(has_private_field(item) for item in value)
    return False


def main():
    evidence_root = ROOT / 'test-evidence'
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    evidence_dir = evidence_root / f'scn06-{stamp}-{uuid.uuid4().hex[:8]}'
    evidence_dir.mkdir(parents=True, exist_ok=False)
    audit_dir = evidence_dir / 'audit'
    audit_dir.mkdir()
    run_id = f'scn06-{uuid.uuid4().hex[:12]}'
    report = {
        'schema_version': 'scn06-communication-recovery-1',
        'scenario_id': 'SCN-06',
        'overall': 'FAIL',
        'service_boundary': {
            'brain': 'No real Brain service/client is connected. The Brain-role calls in this run are controlled local TwinFacade test calls.',
            'guardian': 'No real Guardian service/client is connected. The Guardian-role calls in this run are controlled local TwinFacade test calls.',
            'failure_mode': 'Deterministic ScenarioRunner availability injection; no network connection was dropped or probed.',
            'pending_work': 'No pending command queue exists. Outage requests are rejected and are not durably queued.',
        },
        'checks': [],
        'evidence_directory': str(evidence_dir),
        'reproduction_command': 'python tools/run_scn06_campaign.py',
    }
    previous_env = {
        name: os.environ.get(name)
        for name in ('TWIN_TOKEN_BRAIN', 'TWIN_TOKEN_GUARDIAN', 'TWIN_TOKEN_OPERATOR')
    }
    os.environ['TWIN_TOKEN_BRAIN'] = f'scn06-brain-{uuid.uuid4().hex}'
    os.environ['TWIN_TOKEN_GUARDIAN'] = f'scn06-guardian-{uuid.uuid4().hex}'
    os.environ['TWIN_TOKEN_OPERATOR'] = f'scn06-operator-{uuid.uuid4().hex}'
    brain_token = os.environ['TWIN_TOKEN_BRAIN']
    guardian_token = os.environ['TWIN_TOKEN_GUARDIAN']
    operator_token = os.environ['TWIN_TOKEN_OPERATOR']
    city = store = logger = None

    def check(name, passed, details=None):
        report['checks'].append({
            'name': name,
            'status': 'PASS' if passed else 'FAIL',
            'details': details or {},
        })

    try:
        city = LiveCity(run_id)
        store = EventStore()
        logger = ScenarioLogger('scn-06', run_id, log_dir=audit_dir)
        facade = TwinFacade(city, event_store=store, scenario_logger=logger)
        logger.log_start(config={'scenario_id': 'SCN-06', 'controlled_injection': True})
        scenario = city.start_b_scenario('scn-06')
        correlation_id = scenario['correlation_id']
        initial_isolation = facade.isolate_device(
            guardian_token, 'PRT-PMP-01', 'retain quarantine across outage'
        )
        context = {
            'scenario_id': scenario['scenario_id'],
            'correlation_id': correlation_id,
        }
        initial_revision = city.status()['revision']
        initial_events = [
            row['details'] for row in city.events()['events']
            if row['type'] == 'SCENARIO_LIFECYCLE'
        ]
        check(
            'normal_channel_baseline_recorded',
            initial_isolation['ok']
            and {event['payload'].get('partner') for event in initial_events
             if event['event_type'] == 'PARTNER_CHANNEL_AVAILABLE'}
            == {'brain', 'guardian'}
            and all(
                not event['payload'].get('external_connection_probed')
                for event in initial_events
                if event['event_type'] == 'PARTNER_CHANNEL_AVAILABLE'
            ),
            {'correlation_id': correlation_id},
        )

        city.step()
        revision_during_brain_outage = city.status()['revision']
        brain_outage = facade.send_actuator_command(
            brain_token,
            'PRT-PMP-01',
            'set_speed',
            {'speed_pct': 30},
            'scn06-brain-outage',
            {**context, 'expected_revision': initial_revision},
        )
        check(
            'brain_outage_fails_closed_without_decision',
            not brain_outage['ok']
            and brain_outage['error']['code'] == 'INVALID_STATE'
            and city.status()['revision'] == revision_during_brain_outage
            and not any(
                row['details']['event_type'] == 'BRAIN_DECIDED'
                and row['details']['payload'].get('request_id') == 'scn06-brain-outage'
                for row in city.events()['events']
                if row['type'] == 'SCENARIO_LIFECYCLE'
            ),
            {'response': brain_outage},
        )

        city.step()
        revision_during_guardian_outage = city.status()['revision']
        guardian_outage = facade.isolate_device(
            guardian_token, 'PRT-PMP-01', 'SCN-06 outage probe'
        )
        guardian_restore_outage = facade.restore_device(
            guardian_token, 'PRT-PMP-01'
        )
        operator_restore_outage = facade.restore_device(
            operator_token, 'PRT-PMP-01'
        )
        check(
            'guardian_outage_preserves_quarantine',
            not guardian_outage['ok']
            and guardian_outage['error']['code'] == 'INVALID_STATE'
            and not guardian_restore_outage['ok']
            and guardian_restore_outage['error']['code'] == 'INVALID_STATE'
            and not operator_restore_outage['ok']
            and operator_restore_outage['error']['code'] == 'INVALID_STATE'
            and city.is_device_isolated('PRT-PMP-01')
            and city.status()['revision'] == revision_during_guardian_outage,
            {
                'isolate_response': guardian_outage,
                'restore_response': guardian_restore_outage,
                'operator_restore_response': operator_restore_outage,
                'quarantine_retained': city.is_device_isolated('PRT-PMP-01'),
            },
        )

        city.step()
        brain_recovered = [
            row['details'] for row in city.events()['events']
            if row['type'] == 'SCENARIO_LIFECYCLE'
            and row['details']['event_type'] == 'PARTNER_CHANNEL_RECOVERED'
            and row['details']['payload'].get('partner') == 'brain'
        ]
        guardian_gate_revision = city.status()['revision']
        brain_while_guardian_offline = facade.send_actuator_command(
            brain_token,
            'PRT-PMP-01',
            'set_speed',
            {'speed_pct': 30},
            'scn06-guardian-outage',
            context,
        )
        operator_while_guardian_offline = facade.send_actuator_command(
            operator_token,
            'PRT-PMP-01',
            'set_speed',
            {'speed_pct': 30},
            'scn06-guardian-outage-operator',
            context,
        )
        check(
            'brain_recovers_while_guardian_is_unavailable',
            len(brain_recovered) == 1
            and not brain_while_guardian_offline['ok']
            and brain_while_guardian_offline['error']['code'] == 'INVALID_STATE'
            and not operator_while_guardian_offline['ok']
            and operator_while_guardian_offline['error']['code'] == 'INVALID_STATE'
            and city.status()['revision'] == guardian_gate_revision,
            {
                'brain_command_response': brain_while_guardian_offline,
                'operator_command_response': operator_while_guardian_offline,
            },
        )

        city.step()
        guardian_recovered = [
            row['details'] for row in city.events()['events']
            if row['type'] == 'SCENARIO_LIFECYCLE'
            and row['details']['event_type'] == 'PARTNER_CHANNEL_RECOVERED'
            and row['details']['payload'].get('partner') == 'guardian'
        ]
        recovered_restore = facade.restore_device(
            guardian_token, 'PRT-PMP-01'
        )
        current_revision = city.status()['revision']
        delayed = facade.send_actuator_command(
            brain_token,
            'PRT-PMP-01',
            'set_speed',
            {'speed_pct': 30},
            'scn06-brain-outage',
            {**context, 'expected_revision': initial_revision},
        )
        check(
            'recovered_channels_and_stale_request_revalidation',
            len(guardian_recovered) == 1
            and recovered_restore['ok']
            and not city.is_device_isolated('PRT-PMP-01')
            and not delayed['ok']
            and delayed['error']['code'] == 'STATE_CONFLICT'
            and city.status()['revision'] == current_revision,
            {
                'guardian_restore_response': recovered_restore,
                'delayed_request_response': delayed,
            },
        )

        isolate_after_recovery = facade.isolate_device(
            guardian_token, 'PRT-PMP-01', 'SCN-06 recovery authorization check'
        )
        quarantine_blocked = facade.send_actuator_command(
            brain_token,
            'PRT-PMP-01',
            'set_speed',
            {'speed_pct': 30},
            'scn06-quarantine-check',
            context,
        )
        restore_after_recovery = facade.restore_device(
            guardian_token, 'PRT-PMP-01'
        )
        before_effect_revision = city.status()['revision']
        effect = facade.send_actuator_command(
            brain_token,
            'PRT-PMP-01',
            'set_speed',
            {'speed_pct': 30},
            'scn06-recovered-command',
            {**context, 'expected_revision': before_effect_revision},
        )
        duplicate = facade.send_actuator_command(
            brain_token,
            'PRT-PMP-01',
            'set_speed',
            {'speed_pct': 30},
            'scn06-recovered-command',
            {**context, 'expected_revision': before_effect_revision},
        )
        changed_duplicate = facade.send_actuator_command(
            brain_token,
            'PRT-PMP-01',
            'set_speed',
            {'speed_pct': 60},
            'scn06-recovered-command',
            {**context, 'expected_revision': before_effect_revision},
        )
        final_state = city._snapshot().physical_state
        check(
            'recovery_revalidates_quarantine_and_executes_one_effect',
            isolate_after_recovery['ok']
            and not quarantine_blocked['ok']
            and quarantine_blocked['error']['code'] == 'GUARDIAN_RESTRICTED'
            and restore_after_recovery['ok']
            and effect['ok']
            and duplicate == effect
            and not changed_duplicate['ok']
            and changed_duplicate['error']['code'] == 'IDEMPOTENCY_CONFLICT'
            and city.status()['revision'] == before_effect_revision + 1
            and final_state.pump.effective_speed_fraction == 0.3,
            {
                'quarantine_command': quarantine_blocked,
                'effect': effect,
                'duplicate': duplicate,
                'changed_duplicate': changed_duplicate,
            },
        )

        frame = city.capture_dashboard_frame()
        events = [
            row['details'] for row in city.events()['events']
            if row['type'] == 'SCENARIO_LIFECYCLE'
        ]
        check(
            'correlation_and_event_id_continuity',
            bool(events)
            and {event['correlation_id'] for event in events} == {correlation_id}
            and len({event['event_id'] for event in events}) == len(events),
            {'correlation_id': correlation_id, 'lifecycle_event_count': len(events)},
        )
        check(
            'dashboard_frame_reflects_scenario',
            frame['scenario']['scenario_id'] == 'SCN-06'
            and frame['scenario']['correlation_id'] == correlation_id,
        )
        logger.log_end(city.status()['sim_tick'], {'scenario_id': 'SCN-06'})
        report['audit_log'] = str(logger.log_path)
        logger.close()
        logger = None
        report['audit_log_sanitized'] = all(
            not has_private_field(json.loads(line))
            for line in Path(report['audit_log']).read_text(encoding='utf-8').splitlines()
            if line.strip()
        )
        check('audit_evidence_written_and_sanitized', report['audit_log_sanitized'])
        write_json(evidence_dir / 'twin-events.json', city.events()['events'])
        write_json(evidence_dir / 'event-store.json', store.get_latest(10000, 'operator'))
        write_json(evidence_dir / 'dashboard-frame.json', frame)
        report['final_city_state'] = {
            'revision': city.status()['revision'],
            'pump_speed_fraction': final_state.pump.effective_speed_fraction,
            'pump_enabled': final_state.pump.effective_enabled,
            'quarantined': city.is_device_isolated('PRT-PMP-01'),
        }
        report['overall'] = (
            'PASS' if all(check_row['status'] == 'PASS' for check_row in report['checks'])
            else 'FAIL'
        )
    except Exception as error:
        report['campaign_error'] = f'{type(error).__name__}: {error}'
        report['overall'] = 'FAIL'
        raise
    finally:
        if logger is not None:
            logger.close()
        if city is not None:
            city.close()
        for name, value in previous_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        write_json(evidence_dir / 'campaign.json', report)

    print(json.dumps({
        'overall': report['overall'],
        'evidence_directory': str(evidence_dir),
        'report': str(evidence_dir / 'campaign.json'),
        'checks': report['checks'],
    }, indent=2))
    return 0 if report['overall'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
