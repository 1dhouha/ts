"""Private A-core integration preflight, never a B/C or triplet implementation.

No movement submitted means exactly that: no actuator effect is delivered to A.
It is an oracle for future stuck-valve integration, not a simulated B failure.
The JSON contains private truth and must not enter partner logs or public UI.
"""
import json
from pathlib import Path
import sys

if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from city_core import baseline_config, create_core
from city_core.catalog import CONTRACT_VERSION
from city_core.replay import export_replay, replay_recording
from city_core.transitions import plain


def _need(condition, name):
    if not condition:
        raise RuntimeError(f'A preflight check failed: {name}')


def _value(result):
    _need(result.ok, 'expected successful core result')
    return result.value


def _request(run_id, revision, opening=0.0):
    return {'contract_version': CONTRACT_VERSION, 'run_id': run_id,
            'effect_id': 'valve-movement-1', 'expected_revision': revision,
            'source': 'B_ACTUATOR', 'correlation_id': 'preflight-operation-1',
            'effect': {'tag': 'valve_position', 'target_id': 'V-12',
                       'opening_fraction': opening}}


def _snapshot(core, run_id):
    return _value(core.observations.read_private(run_id))


def _case(movement):
    base = baseline_config()
    initial = base['initial_physical']
    config = {**base, 'initial_physical': {**initial,
        'water': {**initial['water'], 'basin_volume_m3': 6000.0,
                  'surface_volume_m3': 199.4, 'rain_mm_h': 36.0},
        'valve': {'effective_opening_fraction': 1.0}}}
    run_id = 'preflight-moved' if movement else 'preflight-held'
    core = create_core()
    _value(core.lifecycle.initialize(run_id, initial_config=config))
    held = _snapshot(core, run_id)
    request = _request(run_id, held.context.revision)
    if movement:
        _value(core.actuators.apply_effect(request))
    current = _snapshot(core, run_id)
    _value(core.lifecycle.advance_one(run_id, current.context.revision))
    after = _snapshot(core, run_id)
    token = _value(core.metadata.make_evidence_token(run_id, 'preflight-operation-1'))
    metadata = _value(core.metadata.read_metadata())
    record = {'surface_m3': after.physical_state.water.surface_volume_m3,
              'valve_opening': after.physical_state.valve.effective_opening_fraction,
              'low_road_flooded': after.flood['R-LOW'].flooded,
              'hospital_powered': after.power.hospital_powered,
              'tick': after.context.tick, 'revision': after.context.revision,
              'metadata': plain(metadata), 'token': plain(token)}
    _need(held.physical_state.water.surface_volume_m3 == 199.4, 'held snapshot')
    _need(after.context.tick == 1 and after.context.sim_time_ms == 1000, 'simulated time')
    return core, run_id, request, after, record


def _metadata_check(records):
    context_keys = {'run_id', 'tick', 'sim_time_ms', 'revision', 'model_version',
                    'catalog_version', 'contract_version'}
    for record in records:
        metadata, token = record['metadata'], record['token']
        _need(set(metadata) == {'context'}, 'metadata outer allowlist')
        _need(set(token) == {'context', 'correlation_id', 'token_id'}, 'token allowlist')
        _need(set(metadata['context']) == context_keys, 'context allowlist')
        _need(metadata['context'] == token['context'], 'same committed context')
    return {'passed': True, 'scope': 'A metadata/token only; actual C outputs untested'}


def _reject_report(core, run_id):
    before = _snapshot(core, run_id)
    request = {**_request(run_id, before.context.revision),
               'effect_id': 'reported-only',
               'effect': {'tag': 'reported_water_level', 'target_id': 'S-WATER-01',
                          'reported_value': 180.0}}
    result = core.actuators.apply_effect(request)
    _need(not result.ok and result.error.code == 'INVALID_ARGUMENT', 'reported value rejected')
    _need(_snapshot(core, run_id) == before, 'rejected report cannot change truth')
    return {'passed': True, 'code': result.error.code,
            'scope': 'A rejects unsupported report input; no B spoof or Guardian test'}


def _retry_check(core, run_id, request):
    before = _snapshot(core, run_id)
    retry = _value(core.actuators.apply_effect(request))
    _need(retry.duplicate and retry.context.revision == 1, 'original duplicate receipt')
    conflict = {**request, 'effect': {**request['effect'], 'opening_fraction': 1.0}}
    rejected = core.actuators.apply_effect(conflict)
    _need(not rejected.ok and rejected.error.code == 'IDENTITY_CONFLICT', 'conflicting retry')
    _need(_snapshot(core, run_id) == before, 'retry/conflict do not commit')
    return {'passed': True, 'duplicate_receipt_revision': retry.context.revision,
            'current_revision': before.context.revision, 'conflict_code': rejected.error.code}


def _replay_check(core, run_id):
    before = _snapshot(core, run_id)
    recording = _value(export_replay(core.lifecycle, run_id))
    payload = json.loads(json.dumps(plain(recording), allow_nan=False))
    replay = _value(replay_recording(payload, 'preflight-replay'))
    _need(_snapshot(replay, 'preflight-replay').physical_state == before.physical_state,
          'same-runtime private replay')
    _need(_snapshot(core, run_id) == before, 'source unchanged by replay')
    return {'passed': True, 'records': len(recording.records),
            'scope': 'private same-runtime replay; not authenticated public evidence'}


def _reset_check(core, run_id, request):
    before = _snapshot(core, run_id)
    observations, actuators, metadata = core.observations, core.actuators, core.metadata
    _value(core.lifecycle.reset(run_id, before.context.revision, 'preflight-next'))
    reset = _value(observations.read_private('preflight-next'))
    rejected = actuators.apply_effect(request)
    _need(not rejected.ok and rejected.error.code == 'OLD_RUN', 'delayed old effect')
    _need(_value(observations.read_private('preflight-next')) == reset, 'new run unchanged')
    _need(reset.context.tick == 0 and reset.context.revision == 0, 'reset clock')
    expected = baseline_config()['initial_physical']['valve']['effective_opening_fraction']
    _need(reset.physical_state.valve.effective_opening_fraction == expected, 'default baseline reset')
    _need(_value(metadata.read_metadata()).context == reset.context, 'same retained handles')
    _need(not metadata.make_evidence_token(run_id).ok, 'old run token rejected')
    _need(_value(core.lifecycle.read_private_transitions('preflight-next', -1, 10)).records == (),
          'reset trace cleared')
    _need(before.physical_state.valve.effective_opening_fraction == 0.0, 'held old truth')
    return {'passed': True, 'old_effect_code': rejected.error.code,
            'reset_valve_opening': reset.physical_state.valve.effective_opening_fraction,
            'new_context': plain(reset.context), 'scope': 'A only; B/C caches untested'}


def run_preflight():
    moved = _case(True)
    held = _case(False)
    core, run_id, request, _, record = moved
    _need(abs(record['surface_m3'] - 199.4) < 1e-10, 'closed inlet balance')
    _need(abs(held[4]['surface_m3'] - 200.0) < 1e-10, 'open inlet balance')
    _need(not record['low_road_flooded'] and held[4]['low_road_flooded'], 'flood threshold')
    checks = {'metadata_allowlist': _metadata_check((record, held[4])),
              'reported_value_rejected': _reject_report(core, run_id),
              'duplicate_conflict': _retry_check(core, run_id, request),
              'private_replay': {'movement_applied': _replay_check(core, run_id),
                                 'no_movement_submitted': _replay_check(held[0], held[1])},
              'reset_old_run': _reset_check(core, run_id, request)}
    return {'visibility': 'PRIVATE_A_EVALUATOR_ONLY', 'scope': 'A_CORE_PREFLIGHT_ONLY',
            'contract_version': CONTRACT_VERSION, 'python_version': sys.version.split()[0],
            'actual_bc_compatibility': 'UNVERIFIED', 'whole_twin_r1_r2_r3': 'UNVERIFIED',
            'phase10_gate': 'BLOCKED_REAL_BC_PREREQUISITES', 'a_checks_passed': True,
            'cases': {'movement_applied': record, 'no_movement_submitted': held[4]},
            'checks': checks}


if __name__ == '__main__':
    print(json.dumps(run_preflight(), indent=2, allow_nan=False))
