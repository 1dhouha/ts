"""Bearer token authentication and role resolution.

Resolves caller tokens to trusted roles using constant-time comparison
to guard against side-channel timing attacks.

Production deployments MUST provide one token per role through environment
variables:

    TWIN_TOKEN_BRAIN
    TWIN_TOKEN_GUARDIAN
    TWIN_TOKEN_OPERATOR
    TWIN_TOKEN_EVALUATOR

The built-in development tokens are disabled unless explicitly enabled with
``TWIN_ALLOW_INSECURE_DEV_TOKENS=1``. Never enable that flag in production.
"""
import hmac
import os
from typing import Any


class Role:
    """The four official roles in the Twin Layer security perimeter."""
    BRAIN = "brain"
    GUARDIAN = "guardian"
    OPERATOR = "operator"
    EVALUATOR = "evaluator"
    ALL_ROLES = {BRAIN, GUARDIAN, OPERATOR, EVALUATOR}
    ORDERED = (BRAIN, GUARDIAN, OPERATOR, EVALUATOR)


# Local-development tokens only. They are ignored unless the explicit
# TWIN_ALLOW_INSECURE_DEV_TOKENS opt-in is present.
DEFAULT_TOKENS = {
    Role.BRAIN: "token-brain-secret-2026",
    Role.GUARDIAN: "token-guardian-secret-2026",
    Role.OPERATOR: "token-operator-secret-2026",
    Role.EVALUATOR: "token-evaluator-secret-2026",
}

DEV_TOKENS_ENV = "TWIN_ALLOW_INSECURE_DEV_TOKENS"
CREDENTIAL_FIELD_NAMES = frozenset({"token", "access_token", "authorization"})


def contains_credential_field(value: Any) -> bool:
    """Return whether a JSON-like value contains a credential field."""
    if isinstance(value, dict):
        return any(
            (isinstance(name, str) and name.casefold() in CREDENTIAL_FIELD_NAMES)
            or contains_credential_field(nested)
            for name, nested in value.items()
        )
    if isinstance(value, list):
        return any(contains_credential_field(item) for item in value)
    return False


def dev_tokens_enabled() -> bool:
    """Return True only when built-in development tokens were explicitly enabled."""
    return os.environ.get(DEV_TOKENS_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def get_token_for_role(role: str) -> str:
    """Return the configured token for a role, or an empty string if unavailable.

    Environment configuration always wins. Development defaults are used only
    when the explicit insecure-development opt-in is set.
    """
    env_key = f"TWIN_TOKEN_{role.upper()}"
    configured = os.environ.get(env_key)
    if configured:
        return configured
    if dev_tokens_enabled():
        return DEFAULT_TOKENS.get(role, "")
    return ""


def missing_token_roles() -> list[str]:
    """Return roles that have no configured token (useful as a startup health check)."""
    return [role for role in Role.ORDERED if not get_token_for_role(role)]


def resolve_token(raw_token: str | None) -> str | None:
    """Resolve an incoming bearer token string to a verified role.

    Supports both raw tokens ('token-xyz') and authorization header style ('Bearer token-xyz').
    Uses hmac.compare_digest for constant-time comparison and always checks every
    configured role so the caller cannot infer a role from early-exit timing.

    Args:
        raw_token: The token string provided by the caller, or None.

    Returns:
        The matched Role string, or None if authentication failed.
    """
    if not raw_token or not isinstance(raw_token, str):
        return None

    cleaned = raw_token.strip()
    if cleaned.lower().startswith("bearer "):
        cleaned = cleaned[7:].strip()

    if not cleaned:
        return None

    matched: str | None = None
    for role in Role.ORDERED:
        expected = get_token_for_role(role)
        if expected and hmac.compare_digest(cleaned, expected):
            matched = role
    return matched
