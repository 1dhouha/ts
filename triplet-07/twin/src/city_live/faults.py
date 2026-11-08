"""Deterministic local B substitute; faults never hold a physical write handle."""
from dataclasses import dataclass
import hashlib

from city_core.transitions import freeze
from .records import LiveError, exact_keys, integer, numeric

TYPES = ('FALSE_READING', 'STUCK', 'DRIFT', 'NOISE', 'DELAYED', 'DROPOUT', 'DEAD')
MAX_LAG = 600


@dataclass(frozen=True, slots=True)
class Fault:
    fault_id: str
    type: str
    start_tick: int
    params: object
    end_tick: int | None = None
    frozen_value: float | None = None


def create_fault(fault_id, kind, tick, params, last_value):
    if kind not in TYPES:
        raise LiveError('INVALID_FAULT_TYPE')
    if kind == 'FALSE_READING':
        if type(params) is not dict or set(params) not in ({'value'}, {'offset'}):
            raise LiveError('INVALID_ARGUMENT')
        numeric(next(iter(params.values())), -1000000, 1000000)
    elif kind in ('DRIFT', 'NOISE'):
        key = 'rate' if kind == 'DRIFT' else 'amplitude'
        exact_keys(params, (key,))
        numeric(params[key], -1000 if kind == 'DRIFT' else 0, 1000)
    elif kind == 'DELAYED':
        exact_keys(params, ('lag_ticks',))
        integer(params['lag_ticks'], 1, MAX_LAG)
    elif kind == 'DROPOUT':
        exact_keys(params, (), ('duration_ticks',))
        if 'duration_ticks' in params:
            integer(params['duration_ticks'], 1, 100000)
    else:
        exact_keys(params, ())
    if kind == 'STUCK' and last_value is None:
        raise LiveError('NO_VALID_SAMPLE')
    end = tick + params['duration_ticks'] if kind == 'DROPOUT' and 'duration_ticks' in params else None
    return Fault(fault_id, kind, tick, freeze(params), end, last_value if kind == 'STUCK' else None)


def reported(fault, actual, tick, sensor_id, seed):
    if fault is None:
        return actual, 'OK'
    kind, params = fault.type, fault.params
    if kind in ('DROPOUT', 'DEAD'):
        return None, 'OFFLINE' if kind == 'DROPOUT' else 'FAILED'
    if actual is None:
        return None, 'OFFLINE'
    if kind == 'FALSE_READING':
        return params['value'] if 'value' in params else actual + params['offset'], 'OK'
    if kind == 'STUCK':
        return fault.frozen_value, 'OK'
    if kind == 'DRIFT':
        return actual + params['rate'] * (tick - fault.start_tick), 'OK'
    if kind == 'NOISE':
        raw = hashlib.sha256(f'{seed}|noise|{sensor_id}|{fault.fault_id}|{tick}'.encode()).digest()
        jitter = int.from_bytes(raw[:8], 'big') / (2**64 - 1) * 2 - 1
        return actual + params['amplitude'] * jitter, 'OK'
    return actual, 'DEGRADED'
