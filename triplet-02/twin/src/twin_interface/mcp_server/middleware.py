"""HTTP Bearer token extraction middleware and session helpers.

Inspects incoming HTTP connection headers and resolves tokens to verified roles.
"""
from typing import Any
from twin_interface.auth import resolve_token


def extract_bearer_token(headers: dict[str, str] | Any) -> str | None:
    """Extract one Bearer token from an HTTP headers mapping or dictionary.

    Args:
        headers: A dictionary or mapping of HTTP headers (case-insensitive search).

    Returns:
        The extracted token string, or None if missing or malformed.
    """
    if not headers:
        return None

    if hasattr(headers, "getlist"):
        authorization_values = headers.getlist("authorization")
    elif hasattr(headers, "get"):
        auth_header = headers.get("authorization") or headers.get("Authorization")
        authorization_values = [auth_header] if auth_header is not None else []
    else:
        return None

    if len(authorization_values) != 1:
        return None

    authorization = authorization_values[0]
    if not isinstance(authorization, str):
        return None
    scheme, separator, token = authorization.strip().partition(" ")
    if (
        not separator
        or scheme.casefold() != "bearer"
        or not token
        or token != token.strip()
    ):
        return None
    return token


def resolve_header_role(headers: dict[str, str] | Any) -> str | None:
    """Extract token from headers and resolve to authorized role.

    Args:
        headers: Inbound HTTP headers mapping.

    Returns:
        Role string (e.g. 'brain', 'guardian') or None if unauthenticated.
    """
    token = extract_bearer_token(headers)
    return resolve_token(token)
