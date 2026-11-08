"""Uniform response envelope formatter and error definitions.

Every response produced by the Twin Layer (whether via FastMCP or REST)
is enclosed in this standardized envelope structure.
"""
from datetime import datetime, timezone
from typing import Any


class ErrorCode:
    """Standardized error codes catalog for external partners and LLMs."""
    NOT_AUTHORIZED = "NOT_AUTHORIZED"
    GUARDIAN_RESTRICTED = "GUARDIAN_RESTRICTED"
    DEVICE_NOT_FOUND = "DEVICE_NOT_FOUND"
    INVALID_PARAM = "INVALID_PARAM"
    STALE_RUN = "STALE_RUN"
    DUPLICATE_CONFLICT = "DUPLICATE_CONFLICT"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    DEVICE_UNAVAILABLE = "DEVICE_UNAVAILABLE"
    INVALID_STATE = "INVALID_STATE"
    STATE_CONFLICT = "STATE_CONFLICT"
    COMMAND_BUDGET_EXHAUSTED = "COMMAND_BUDGET_EXHAUSTED"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"
    PERSISTENCE_FAILURE = "PERSISTENCE_FAILURE"
    INTERNAL_ERROR = "INTERNAL_ERROR"


def utc_now_iso() -> str:
    """Return the current UTC timestamp formatted as ISO 8601 UTC string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def success_envelope(run_id: str, data: dict[str, Any], timestamp: str | None = None) -> dict[str, Any]:
    """Wrap successful payload in standard {ok, run_id, data, error, timestamp} envelope.

    Args:
        run_id: Identifier of the active simulation run.
        data: The output payload dictionary.
        timestamp: Optional explicit timestamp string; defaults to current UTC time.

    Returns:
        Standard envelope dictionary with ok=True.
    """
    return {
        "ok": True,
        "run_id": run_id,
        "data": data,
        "error": None,
        "timestamp": timestamp or utc_now_iso(),
    }


def error_envelope(
    run_id: str,
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
    timestamp: str | None = None,
) -> dict[str, Any]:
    """Wrap error in standard {ok, run_id, data, error, timestamp} envelope.

    Args:
        run_id: Identifier of the active simulation run.
        code: Machine-readable error string (from ErrorCode).
        message: Human- and LLM-readable diagnostic explanation.
        details: Optional dictionary containing additional structured debugging context.
        timestamp: Optional explicit timestamp string; defaults to current UTC time.

    Returns:
        Standard envelope dictionary with ok=False.
    """
    err_body: dict[str, Any] = {
        "code": code,
        "message": message,
    }
    if details:
        err_body["details"] = details

    return {
        "ok": False,
        "run_id": run_id,
        "data": None,
        "error": err_body,
        "timestamp": timestamp or utc_now_iso(),
    }
