"""Validated local inputs and immutable event/action records."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import math
import re

from city_core.transitions import freeze, plain

VERSION = 'twin-live-0.1-local'
EPOCH = '2026-10-18T00:00:00Z'


class LiveError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def identifier(value):
    if type(value) is not str or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,95}', value):
        raise LiveError('INVALID_ARGUMENT')
    return value


def integer(value, low=0, high=100000):
    if type(value) is not int or not low <= value <= high:
        raise LiveError('INVALID_ARGUMENT')
    return value


def numeric(value, low, high):
    if type(value) not in (float, int):
        raise LiveError('INVALID_ARGUMENT')
    try:
        result = float(value)
    except OverflowError:
        raise LiveError('INVALID_ARGUMENT') from None
    if not math.isfinite(result) or not low <= result <= high:
        raise LiveError('INVALID_ARGUMENT')
    return result


def utc_epoch(value):
    try:
        if type(value) is not str or not value.endswith('Z'):
            raise ValueError()
        parsed = datetime.fromisoformat(value[:-1] + '+00:00')
        if parsed.utcoffset() != timedelta(0) or parsed.microsecond:
            raise ValueError()
        # Ensure conversion through the entire allowed run cannot overflow.
        parsed + timedelta(seconds=100000)
        return parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        raise LiveError('INVALID_EPOCH') from None


def timestamp(epoch, tick):
    return (epoch + timedelta(seconds=tick)).isoformat().replace('+00:00', 'Z')


def exact_keys(value, required, optional=()):
    if type(value) is not dict or not set(required) <= set(value) or set(value) - set(required) - set(optional):
        raise LiveError('INVALID_ARGUMENT')


def require(result):
    if not result.ok:
        raise LiveError(result.error.code)
    return result.value


@dataclass(frozen=True, slots=True)
class Action:
    action_id: str
    run_id: str
    due_tick: int
    order: int
    kind: str
    params: object
    status: str = 'PENDING'
    error: str | None = None


@dataclass(frozen=True, slots=True)
class Event:
    seq: int
    run_id: str
    sim_tick: int
    timestamp: str
    type: str
    details: object
    event_id: str
    zone_id: str | None = None
    device_id: str | None = None
    incident_id: str | None = None
    schema_version: str = 'twin-private-events-0.1-local'


def detached(value):
    return plain(value)
