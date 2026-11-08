"""Trusted local operator console; grammar only, never eval/exec or shell dispatch."""
import json
from pathlib import Path
import shlex
import time

from .records import LiveError, integer, numeric
from .scenarios import PRESETS

HELP = '''start | pause | resume | stop | status | inspect | speed NUMBER | step [COUNT]
reset FRESH_RUN_ID | traffic normal/morning_rush/evening_rush/low/surge/congestion/time_of_day
incident power_outage/power_restore/heavy_rain/rain_clear/pollution/pollution_clear/pump_on/pump_off
incident accident/partial_block/road_clear ROAD | incident signal_stop/signal_normal JUNCTION
incident valve FRACTION | recover power/rain/pollution | sensors list
sensor status/compare/clear/repair ID | sensor replace OLD NEW
sensor fault ID FALSE_READING VALUE | sensor fault ID OFFSET VALUE
sensor fault ID STUCK/DEAD | sensor fault ID DRIFT RATE | sensor fault ID NOISE AMPLITUDE
sensor fault ID DELAYED LAG_TICKS | sensor fault ID DROPOUT [DURATION_TICKS]
scenarios list/active | scenarios run NAME [DELAY_TICKS] | events | observations
schedule ACTION_ID TICK INCIDENT [TARGET] | cancel ACTION_ID | watch SECONDS
command valve FRACTION [EFFECTIVE_FRACTION] | command pump on/off
export PATH (public reports only) | replay PATH (PRIVATE core recording) | quit'''


def _count(tokens, low, high=None):
    if not low <= len(tokens) <= (low if high is None else high):
        raise LiveError('INVALID_ARGUMENT')


def _incident(tokens):
    _count(tokens, 1, 2)
    kind = tokens[0]
    if kind in ('accident', 'partial_block', 'road_clear', 'signal_stop', 'signal_normal'):
        _count(tokens, 2)
        return kind, {'target': tokens[1]}
    if kind == 'valve':
        _count(tokens, 2)
        return kind, {'opening': float(tokens[1])}
    _count(tokens, 1)
    return kind, {}


def _sensor(city, tokens):
    _count(tokens, 2, 4)
    operation, sensor = tokens[:2]
    if operation in ('status', 'compare', 'clear', 'repair'):
        _count(tokens, 2)
        return {'status': city.sensor_compare, 'compare': city.sensor_compare,
                'clear': city.sensor_clear, 'repair': city.sensor_repair}[operation](sensor)
    if operation == 'replace':
        _count(tokens, 3)
        return city.sensor_replace(sensor, tokens[2])
    if operation != 'fault':
        raise LiveError('UNSUPPORTED_OPERATION')
    _count(tokens, 3, 4)
    kind = tokens[2]
    if kind in ('STUCK', 'DEAD'):
        _count(tokens, 3)
        params = {}
    elif kind == 'DROPOUT':
        params = {'duration_ticks': int(tokens[3])} if len(tokens) == 4 else {}
    else:
        _count(tokens, 4)
        keys = {'FALSE_READING': 'value', 'OFFSET': 'offset', 'DRIFT': 'rate',
                'NOISE': 'amplitude', 'DELAYED': 'lag_ticks'}
        if kind not in keys:
            raise LiveError('INVALID_FAULT_TYPE')
        params = {keys[kind]: int(tokens[3]) if kind == 'DELAYED' else float(tokens[3])}
        kind = 'FALSE_READING' if kind == 'OFFSET' else kind
    return city.sensor_fault(sensor, kind, params)


def execute(city, line):
    try:
        tokens = shlex.split(line)
        _count(tokens, 1, 8)
        op, args = tokens[0], tokens[1:]
        simple = {'start': city.start, 'pause': city.pause, 'resume': city.resume,
                  'stop': city.stop, 'status': city.status, 'inspect': city.diagnostics,
                  'events': city.events, 'observations': city.public.observations}
        if op in simple:
            _count(args, 0)
            result = simple[op]()
            return result if result is not None else city.status()
        return _extended(city, op, args)
    except LiveError:
        raise
    except (ValueError, TypeError, OSError, OverflowError):
        raise LiveError('INVALID_ARGUMENT') from None


def _extended(city, op, args):
    if op in ('speed', 'step', 'reset', 'traffic'):
        return _controls(city, op, args)
    if op == 'help':
        _count(args, 0)
        return {'help': HELP, 'visibility': 'LOCAL_PRIVILEGED_OPERATOR_CONSOLE'}
    elif op == 'incident':
        kind, params = _incident(args)
        return city.incident(kind, params)
    elif op == 'recover':
        _count(args, 1)
        mapping = {'power': 'power_restore', 'rain': 'rain_clear', 'pollution': 'pollution_clear'}
        if args[0] not in mapping:
            raise LiveError('UNSUPPORTED_OPERATION')
        return city.incident(mapping[args[0]])
    elif op == 'sensors':
        if args != ['list']:
            raise LiveError('INVALID_ARGUMENT')
        return city.sensors()
    elif op == 'sensor':
        return _sensor(city, args)
    elif op == 'scenarios':
        return _scenarios(city, args)
    elif op == 'schedule':
        _count(args, 3, 4)
        kind, params = _incident(args[2:])
        return city.schedule(args[0], int(args[1]), kind, params, run_id=city.status()['run_id'])
    elif op == 'cancel':
        _count(args, 1)
        city.cancel(args[0], run_id=city.status()['run_id'])
    elif op == 'command':
        return _command(city, args)
    elif op in ('export', 'replay'):
        return _export(city, op, args)
    elif op == 'watch':
        _count(args, 1)
        seconds = numeric(float(args[0]), 0, 60)
        time.sleep(seconds)
    else:
        raise LiveError('UNSUPPORTED_OPERATION')
    return city.status()


def _controls(city, op, args):
    if op == 'step':
        _count(args, 0, 1)
        count = integer(int(args[0]) if args else 1, 1, 10000)
        for _ in range(count):
            city.step()
    else:
        _count(args, 1)
        if op == 'speed':
            city.set_speed(float(args[0]))
        elif op == 'reset':
            city.reset(args[0])
        else:
            city.traffic(args[0])
    return city.status()


def _scenarios(city, args):
    _count(args, 1, 3)
    if args == ['list']:
        return list(PRESETS)
    if args == ['active']:
        return {'pending_actions': [a for a in city.actions() if a['status'] == 'PENDING'],
                'disturbances': city.diagnostics()['active_disturbances']}
    if args[0] != 'run':
        raise LiveError('INVALID_ARGUMENT')
    _count(args, 2, 3)
    return city.run_scenario(args[1], int(args[2]) if len(args) == 3 else 0)


def _export(city, op, args):
    _count(args, 1)
    path = Path(args[0])
    payload = ({'schema_version': 'twin-export-0.1-local',
                'observations': city.public.observations()} if op == 'export' else
               {'visibility': 'PRIVATE_OPERATOR_ONLY', 'core_replay': city.export_private_replay()})
    with path.open('x', encoding='utf-8') as stream:
        json.dump(payload, stream, indent=2, allow_nan=False)
    return {'written': str(path), 'visibility': 'PUBLIC_REPORTED_ONLY' if op == 'export' else 'PRIVATE_OPERATOR_ONLY'}


def _command(city, args):
    _count(args, 2, 3)
    command_id = f'cli-{city.status()["revision"]:010d}-{len(city.events()["events"]):05d}'
    if args[0] == 'valve':
        return city.command('A-VALVE-01', float(args[1]), command_id=command_id,
                            effective=float(args[2]) if len(args) == 3 else None)
    if args[0] == 'pump' and args[1] in ('on', 'off'):
        _count(args, 2)
        return city.command('A-PUMP-01', args[1] == 'on', command_id=command_id)
    raise LiveError('UNSUPPORTED_COMMAND')


def console(city, source, target, *, interactive=True):
    if interactive:
        target.write('PRIVATE LOCAL OPERATOR CONSOLE. Type help; EOF/quit exits.\n')
    while True:
        if interactive:
            target.write('city> ')
            target.flush()
        line = source.readline()
        if not line or line.strip() in ('quit', 'exit'):
            return
        if not line.strip():
            continue
        try:
            result = {'ok': True, 'data': execute(city, line)}
        except LiveError as error:
            result = {'ok': False, 'error': {'code': error.code, 'message': 'Operation rejected'}}
        target.write(json.dumps(result, ensure_ascii=False, allow_nan=False) + '\n')
        target.flush()
