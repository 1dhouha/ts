"""Strict construction boundaries. No physics, effects or public transport."""
import math
import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class InvalidInput(Exception):
    code: str = "INVALID_ARGUMENT"


def shape(value, keys):
    if type(value) is not dict or set(value) != set(keys):
        raise InvalidInput()
    return value


def identity(value):
    if type(value) is not str or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}", value) is None:
        raise InvalidInput()
    return value


def version(value, expected):
    identity(value)
    if value != expected:
        raise InvalidInput("UNSUPPORTED_VERSION")
    return value


def number(value, minimum=0.0, maximum=None):
    if type(value) not in (int, float):
        raise InvalidInput()
    try:
        normalized = float(value)
    except OverflowError:
        raise InvalidInput() from None
    if not math.isfinite(normalized) or normalized < minimum:
        raise InvalidInput()
    if maximum is not None and normalized > maximum:
        raise InvalidInput()
    return normalized


def boolean(value):
    if type(value) is not bool:
        raise InvalidInput()
    return value


def fixed_profile(value, expected):
    """Check exact version-bound values without bool/number coercion."""
    shape(value, expected)
    for key, reference in expected.items():
        actual = value[key]
        if type(reference) is dict:
            fixed_profile(actual, reference)
        elif type(reference) is int:
            if type(actual) is not int or actual != reference:
                raise InvalidInput()
        elif number(actual) != reference:
            raise InvalidInput()
