"""Privileged deterministic core inputs; no B/C scenario engine."""
from pathlib import Path
import json

from city_core import create_core
from city_core.transitions import plain

FIXTURES=Path(__file__).parents[1]/'fixtures'


def api():
    from city_core.replay import export_replay, replay_recording
    return export_replay, replay_recording


def recording(name='normal'):
    return json.loads((FIXTURES/f'replay_{name}_recording.json').read_text())


def execute_payload(core, payload, run_id):
    """Replay-independent test consumer of existing real-core methods."""
    for record in payload['records']:
        data={**record['input'],'run_id':run_id}
        if record['cause']=='STEP': result=core.lifecycle.advance_one(**data)
        else:
            handle=core.scenarios if data['source']=='B_SCENARIO' else core.actuators
            result=handle.apply_effect(data)
        if not result.ok: raise AssertionError(result)


def original(name='normal',run_id='r-source'):
    payload=recording(name)
    core=create_core()
    assert core.lifecycle.initialize(run_id,initial_config=payload['header']['initial_config']).ok
    execute_payload(core,payload,run_id)
    return core
