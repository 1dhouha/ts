"""Allow-list data projections to prevent Ground Truth leakage.

Enforces strict information boundaries: the Brain and Guardian receive only
observed values and public statuses. Secret simulation variables (actual_value,
delta, fault) are stripped unless the caller is the Evaluator (Jury).
"""
import copy
from typing import Any
from twin_interface.auth import Role

# Strict whitelist of fields that public layers (Brain and Guardian) are permitted to see
PUBLIC_READING_ALLOWLIST = frozenset({
    "sensor_id",
    "zone_id",
    "type",
    "unit",
    "timestamp",
    "sim_tick",
    "seq",
    "reported_value",
    "status",
    "available",
    "backup_ids",
    "linked_device_ids",
})

# Forbidden ground-truth fields that must NEVER appear in Brain or Guardian responses
FORBIDDEN_TRUTH_FIELDS = frozenset({
    "actual_value",
    "true_value",
    "true_level",
    "sensor_value",
    "delta",
    "fault",
    "noise",
    "drift_rate",
    "lag_ticks",
    "bias",
    "ground_truth",
})


def _strip_forbidden(value: Any) -> Any:
    """Recursively copy a value while removing every forbidden ground-truth key."""
    if isinstance(value, dict):
        return {
            key: _strip_forbidden(item)
            for key, item in value.items()
            if key not in FORBIDDEN_TRUTH_FIELDS
        }
    if isinstance(value, list):
        return [_strip_forbidden(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_strip_forbidden(item) for item in value)
    return copy.deepcopy(value)


def project_reading(reading: dict[str, Any], role: str) -> dict[str, Any]:
    """Sanitize a single sensor reading dictionary based on the caller's role.

    Args:
        reading: Raw sensor reading dictionary from the simulation.
        role: Verified role of the caller (from Role).

    Returns:
        Sanitized dictionary with private ground-truth fields removed for public roles.
    """
    if role == Role.EVALUATOR:
        # The Evaluator/Jury role is explicitly granted full access to verify truth
        return copy.deepcopy(reading)

    # Brain, Guardian, and Operator receive ONLY allow-listed fields, and every
    # retained value is recursively scrubbed so nested dictionaries cannot leak.
    return {
        key: _strip_forbidden(value)
        for key, value in reading.items()
        if key in PUBLIC_READING_ALLOWLIST
    }


def project_readings(readings: list[dict[str, Any]], role: str) -> list[dict[str, Any]]:
    """Sanitize a list of sensor reading dictionaries."""
    return [project_reading(r, role) for r in readings]


def project_event(event: dict[str, Any], role: str) -> dict[str, Any]:
    """Sanitize an event dictionary before it is published to the public event log.

    Both the legacy ``details`` shape and the observability ``payload`` shape are
    scrubbed recursively, along with any other top-level or nested keys.
    """
    if role == Role.EVALUATOR:
        return copy.deepcopy(event)

    return _strip_forbidden(event)
