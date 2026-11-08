"""Controlled fixed-step clock; caller pacing never enters physical time."""
from dataclasses import replace

from .validation import InvalidInput

STEP_MS = 1000
# Approved domain order is implemented; no wall-time scheduling changes it.
DOMAIN_ORDER = ("power", "water", "roads", "traffic_responders", "air")


def validate_revision(value):
    if type(value) is not int or value < 0:
        raise InvalidInput()


def next_context(context, duration_ms):
    if type(duration_ms) is not int or duration_ms != STEP_MS:
        raise InvalidInput()
    return replace(context, tick=context.tick + 1,
                   sim_time_ms=context.sim_time_ms + STEP_MS,
                   revision=context.revision + 1)
