"""Privileged same-runtime replay of one complete committed core run."""
from dataclasses import dataclass

from .core import create_core
from .lifecycle import Lifecycle
from .replay_validation import (FORMAT_VERSION, MODEL_PROFILE, NUMERIC_PROFILE,
                                PYTHON_VERSION, validate_recording)
from .snapshots import Context, Failure, Rejected, Success
from .transitions import ReplayHeader, digest, plain
from .validation import InvalidInput, identity


@dataclass(frozen=True, slots=True)
class ReplayRecording:
    format_version: str
    model_profile: str
    python_version: str
    numeric_profile: str
    randomness: str
    header: ReplayHeader
    records: tuple
    final_context: Context
    final_digest: str


def rejected(code):
    # Private error remains small and has no partial core/config/truth in it.
    return Rejected(Failure(code,'Replay rejected',False,None))


def export_replay(owner,run_id):
    if type(owner) is not Lifecycle:
        return rejected('INVALID_ARGUMENT')
    with owner._private.lock:
        try:
            current=owner._current(run_id)
            return Success(ReplayRecording(FORMAT_VERSION,MODEL_PROFILE,PYTHON_VERSION,NUMERIC_PROFILE,
                'none',current.header,current.trace.read(0,current.trace.count),
                current.snapshot.context,digest(current.snapshot.physical_state)))
        except InvalidInput as error:
            return rejected(error.code)


def _apply(core,record,new_run_id):
    data={**record['input'],'run_id':new_run_id}
    if record['cause']=='STEP': result=core.lifecycle.advance_one(**data)
    else:
        handle=core.scenarios if data['source']=='B_SCENARIO' else core.actuators
        result=handle.apply_effect(data)
    if not result.ok: raise InvalidInput(result.error.code)
    current=core.lifecycle._private.current
    actual=plain(current.trace.read(record['record_index'],1)[0])
    expected={**record,'input':data,'context':{**record['context'],'run_id':new_run_id}}
    if actual!=expected:
        raise InvalidInput('REPLAY_MISMATCH')


def replay_recording(payload,new_run_id):
    """Return an isolated core only after every input/transition verifies."""
    try:
        identity(new_run_id)
        validate_recording(payload)
        core=create_core()
        result=core.lifecycle.initialize(new_run_id,initial_config=payload['header']['initial_config'])
        if not result.ok: raise InvalidInput(result.error.code)
        current=core.lifecycle._private.current
        actual=plain(current.header)
        expected={**payload['header'],'initial_context':{**payload['header']['initial_context'],'run_id':new_run_id}}
        if actual!=expected: raise InvalidInput('REPLAY_MISMATCH')
        for record in payload['records']:
            _apply(core,record,new_run_id)
        final=core.observations.read_private(new_run_id).value
        if digest(final.physical_state)!=payload['final_digest']:
            raise InvalidInput('REPLAY_MISMATCH')
        return Success(core)
    except InvalidInput as error:
        return rejected(error.code)
