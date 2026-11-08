"""Atomic privileged local requests; no partner transport or Brain policy."""
import json
from city_core.transitions import freeze, plain
from .records import LiveError, exact_keys, identifier, integer

LIFECYCLE = ('start', 'pause', 'resume', 'stop', 'speed')
ARGUMENTS = {
    'start': ((), ()), 'pause': ((), ()), 'resume': ((), ()), 'stop': ((), ()),
    'speed': (('speed',), ()), 'step': ((), ()), 'reset': (('new_run_id',), ()),
    'traffic': (('name',), ('config',)), 'incident': (('operation', 'params'), ()),
    'preset': (('name', 'delay'), ()),
    'schedule': (('action_id', 'due_tick', 'operation', 'params'), ()),
    'cancel': (('action_id',), ()),
    'sensor_fault': (('sensor_id', 'fault_type', 'parameters'), ()),
    'sensor_clear': (('sensor_id',), ()), 'sensor_repair': (('sensor_id',), ()),
    'sensor_replace': (('sensor_id', 'new_id'), ()),
    'device': (('device_id',), ('requested', 'effective', 'available', 'command', 'params')),
}


def apply_request(owner, envelope):
    """Called only while the owning LiveCity condition lock is held."""
    exact_keys(envelope, ('command_id', 'run_id', 'expected_revision', 'kind', 'args'))
    identifier(envelope['command_id'])
    identifier(envelope['run_id'])
    kind, args = envelope['kind'], envelope['args']
    if type(kind) is not str:
        raise LiveError('INVALID_ARGUMENT')
    if kind not in ARGUMENTS:
        raise LiveError('UNSUPPORTED_OPERATION')
    if kind == 'device':
        exact_keys(args, *ARGUMENTS[kind])
        for key in ('device_id',):
            if key in args:
                identifier(args[key])
        value = dispatch(
            owner,
            kind,
            args,
            envelope['command_id'],
            run_id=envelope['run_id'],
            expected_revision=envelope['expected_revision'],
        )
        outcome = value.get('status', 'COMPLETED') if type(value) is dict else 'COMPLETED'
        return {
            'command_id': envelope['command_id'],
            'request_run_id': envelope['run_id'],
            'run_id': owner.status()['run_id'],
            'status': outcome,
            'result': value,
            'state': owner.status(),
        }

    try:
        identity = json.dumps(envelope, sort_keys=True, allow_nan=False, separators=(',', ':'))
    except (TypeError, ValueError, OverflowError, RecursionError):
        raise LiveError('INVALID_ARGUMENT') from None

    ledger = owner._operator_requests
    existing = ledger.get(envelope['command_id'])
    if existing is not None:
        if existing['identity'] != identity:
            raise LiveError('IDENTITY_CONFLICT')
        if existing['error']:
            raise LiveError(existing['error'])
        return plain(existing['result'])
    exact_keys(args, *ARGUMENTS[kind])
    for key in ('name', 'operation', 'action_id', 'device_id', 'new_run_id', 'sensor_id', 'new_id', 'fault_type'):
        if key in args:
            identifier(args[key])
    owner._run_check(envelope['run_id'])
    status = owner.status()
    revision = envelope['expected_revision']
    if kind in LIFECYCLE:
        if revision is not None:
            raise LiveError('INVALID_ARGUMENT')
    else:
        integer(revision, 0, 200000)
        if status['revision'] != revision:
            raise LiveError('STATE_CONFLICT')
        if kind != 'device':
            allowed = ('READY', 'PAUSED', 'STOPPED')
            if kind == 'reset':
                allowed += ('ERROR', 'BUDGET_EXHAUSTED')
            if status['state'] not in allowed:
                raise LiveError('INVALID_STATE')
    if len(ledger) >= 2048:
        raise LiveError('COMMAND_BUDGET_EXHAUSTED')
    result, error = None, None
    try:
        value = dispatch(owner, kind, args, envelope['command_id'])
        outcome = value.get('status', 'COMPLETED') if type(value) is dict else 'COMPLETED'
        if type(value) is list and any(row.get('status') == 'PENDING' for row in value):
            outcome = 'PENDING'
        result = {'command_id': envelope['command_id'], 'request_run_id': envelope['run_id'],
                  'run_id': owner.status()['run_id'], 'status': outcome,
                  'result': value, 'state': owner.status()}
    except LiveError as failure:
        error = failure.code
    owner._operator_requests = freeze({**ledger, envelope['command_id']:
        {'identity': identity, 'result': result, 'error': error}})
    if error:
        raise LiveError(error)
    return plain(result)


def dispatch(owner, kind, args, command_id, *, run_id=None, expected_revision=None):
    if kind.startswith('sensor_'):
        sensor = owner._sensors.sensor(args['sensor_id'])
        if sensor.source is None:
            raise LiveError('SENSOR_UNBOUND')
        if kind == 'sensor_fault':
            return owner.sensor_fault(args['sensor_id'], args['fault_type'], args['parameters'])
        if kind == 'sensor_clear':
            return owner.sensor_clear(args['sensor_id'])
        if kind == 'sensor_repair':
            return owner.sensor_repair(args['sensor_id'])
        return owner.sensor_replace(args['sensor_id'], args['new_id'])
    if kind == 'start':
        return owner.start()
    if kind == 'pause':
        return owner.pause()
    if kind == 'resume':
        return owner.resume()
    if kind == 'stop':
        return owner.stop()
    if kind == 'speed':
        return owner.set_speed(args['speed'])
    if kind == 'step':
        return owner.step()
    if kind == 'reset':
        return owner.reset(args['new_run_id'])
    if kind == 'traffic':
        return owner.traffic(args['name'], args.get('config'))
    if kind == 'incident':
        return owner.incident(args['operation'], args['params'])
    if kind == 'preset':
        return owner.run_scenario(args['name'], args['delay'])
    if kind == 'schedule':
        return owner.schedule(args['action_id'], args['due_tick'], args['operation'],
                              args['params'], run_id=owner.status()['run_id'])
    if kind == 'cancel':
        return owner.cancel(args['action_id'], run_id=owner.status()['run_id'])
    if 'command' in args or 'params' in args:
        if ('command' not in args or 'params' not in args or
                'requested' in args or 'effective' in args):
            raise LiveError('INVALID_ARGUMENT')
        return owner.execute_actuator_command(
            args['device_id'], args['command'], args['params'],
            command_id=command_id, available=args.get('available', True),
            run_id=run_id,
            expected_revision=expected_revision)
    if 'requested' not in args:
        raise LiveError('INVALID_ARGUMENT')
    return owner.command(args['device_id'], args['requested'], command_id=command_id,
        effective=args.get('effective'), available=args.get('available', True),
        run_id=run_id, expected_revision=expected_revision)
