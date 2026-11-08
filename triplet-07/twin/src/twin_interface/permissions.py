"""Tool permission matrix and access control checks.

Maps each public tool to the roles authorized to invoke it.
"""
from twin_interface.auth import Role

# Permissions table: Tool Name -> Set of permitted roles
PERMISSION_MATRIX: dict[str, set[str]] = {
    # Observation tools (Open to all authenticated participants)
    "get_sensor_readings": {Role.BRAIN, Role.GUARDIAN, Role.OPERATOR, Role.EVALUATOR},
    "get_city_state": {Role.BRAIN, Role.GUARDIAN, Role.OPERATOR, Role.EVALUATOR},
    "get_events_since": {Role.BRAIN, Role.GUARDIAN, Role.OPERATOR, Role.EVALUATOR},

    # Control tools (Strictly reserved for Brain decision engine & Operator)
    "send_actuator_command": {Role.BRAIN, Role.OPERATOR},

    # Security & Quarantine tools (Strictly reserved for Guardian watchdog & Operator)
    "isolate_device": {Role.GUARDIAN, Role.OPERATOR},
    "restore_device": {Role.GUARDIAN, Role.OPERATOR},

    # Evaluation / Jury tools (Restricted to Evaluator role)
    "get_ground_truth": {Role.EVALUATOR},

    # Scenario & Fault Injection tools (Restricted to Operator / Demo Runner)
    "simulate_device_failure": {Role.OPERATOR},
    "clear_device_failure": {Role.OPERATOR},
    "run_scenario": {Role.OPERATOR},
    "reset_scenario": {Role.OPERATOR},
}


def is_tool_permitted(tool_name: str, role: str) -> bool:
    """Check whether a given role has permission to call a specific tool."""
    allowed_roles = PERMISSION_MATRIX.get(tool_name)
    if allowed_roles is None:
        return False
    return role in allowed_roles


def check_tool_permission(tool_name: str, role: str | None) -> tuple[bool, str | None]:
    """Validate permission for an invocation.

    Args:
        tool_name: The name of the tool being called.
        role: The role resolved from the caller's token, or None if unauthenticated.

    Returns:
        A tuple of (is_authorized: bool, error_message: str | None).
    """
    if role is None:
        return False, "Missing or invalid authentication token."

    if tool_name not in PERMISSION_MATRIX:
        return False, f"Unknown tool: '{tool_name}'."

    if not is_tool_permitted(tool_name, role):
        return False, f"Role '{role}' is not authorized to call tool '{tool_name}'."

    return True, None
