"""Canonical actuator command translation for the LiveCity simulation."""
from .device_catalog import load_catalog
from .records import LiveError, identifier, numeric


def build_command_effect(device_id, command, params):
    if type(device_id) is not str or type(command) is not str or not command:
        raise LiveError("INVALID_ARGUMENT")
    if type(params) is not dict:
        raise LiveError("INVALID_ARGUMENT")

    actuator = load_catalog().actuator_row(device_id)
    if actuator is None or not actuator.supported or actuator.target_id is None:
        raise LiveError("UNSUPPORTED_DEVICE")

    cmd = command.lower()
    target_id = actuator.target_id

    if actuator.family == "pump":
        if cmd == "stop":
            _validate_params(params)
            speed = 0.0
        elif cmd in ("start", "set_speed"):
            _validate_params(params, required=("speed_pct",))
            if type(params["speed_pct"]) not in (int, float):
                raise LiveError("INVALID_ARGUMENT")
            speed = numeric(params["speed_pct"], 0, 100) / 100.0
        else:
            raise LiveError("UNSUPPORTED_COMMAND")
        return {"tag": "pump_enable", "target_id": target_id, "enabled": speed > 0}

    if actuator.family == "valve":
        if cmd != "set_opening":
            raise LiveError("UNSUPPORTED_COMMAND")
        _validate_params(params, required=("open_pct",))
        if type(params["open_pct"]) not in (int, float):
            raise LiveError("INVALID_ARGUMENT")
        opening = numeric(params["open_pct"], 0, 100)
        return {
            "tag": actuator.effect_tag,
            "target_id": target_id,
            "opening_fraction": opening / 100.0,
        }

    if actuator.family == "signal":
        if cmd == "set_mode":
            _validate_params(params, required=("mode",))
            mode = params["mode"]
            if type(mode) is not str:
                raise LiveError("INVALID_ARGUMENT")
            mode = mode.lower()
        elif cmd == "set_priority_corridor":
            _validate_params(params)
            mode = "priority"
        elif cmd == "set_phase":
            _validate_params(params, required=("phase",))
            phase = params["phase"]
            if type(phase) is not str:
                raise LiveError("INVALID_ARGUMENT")
            phase = phase.lower()
            if phase in ("green_ns", "green_ew"):
                raise LiveError("UNSUPPORTED_COMMAND")
            if phase != "all_red":
                raise LiveError("UNSUPPORTED_COMMAND")
            mode = "stop"
        else:
            raise LiveError("UNSUPPORTED_COMMAND")
        if mode not in ("normal", "priority", "stop"):
            raise LiveError("INVALID_ARGUMENT")
        return {"tag": actuator.effect_tag, "target_id": target_id, "mode": mode}

    if actuator.family == "feeder":
        _validate_params(params)
        if cmd in ("close", "connect"):
            closed = True
        elif cmd in ("open", "disconnect"):
            closed = False
        else:
            raise LiveError("UNSUPPORTED_COMMAND")
        return {"tag": actuator.effect_tag, "target_id": target_id, "closed": closed}

    if actuator.family == "responder":
        if cmd == "dispatch":
            _validate_params(
                params,
                required=("destination_node_id", "route_road_ids"),
                optional=("mission_ref",),
            )
            mission_ref = params.get("mission_ref") or f"m-{target_id.lower()}-01"
            identifier(mission_ref)
            destination = identifier(params["destination_node_id"])
            route = params["route_road_ids"]
            if type(route) is not list or len(route) > 5:
                raise LiveError("INVALID_ARGUMENT")
            route = [identifier(road_id) for road_id in route]
            return {
                "tag": "reserve_responder",
                "target_id": target_id,
                "mission_ref": mission_ref,
                "destination_node_id": destination,
                "route_road_ids": route,
            }
        if cmd == "release":
            _validate_params(params, required=("mission_ref",))
            mission_ref = identifier(params["mission_ref"])
            return {
                "tag": "release_responder",
                "target_id": target_id,
                "mission_ref": mission_ref,
            }
        raise LiveError("UNSUPPORTED_COMMAND")

    raise LiveError("UNSUPPORTED_DEVICE")


def preview_command_effect(device_id, command, params):
    try:
        return build_command_effect(device_id, command, params), None
    except LiveError as error:
        return None, error.code


def build_legacy_command_effect(device_id, requested, effective):
    actual = requested if effective is None else effective
    if device_id == "A-VALVE-01":
        numeric(requested, 0, 1)
        numeric(actual, 0, 1)
        return build_command_effect(device_id, "set_opening", {"open_pct": actual * 100}), actual
    if device_id == "A-PUMP-01":
        if type(requested) is not bool or type(actual) is not bool:
            raise LiveError("INVALID_ARGUMENT")
        effect = build_command_effect(
            device_id, "set_speed", {"speed_pct": 100 if actual else 0})
        return effect, actual
    elif device_id in ("A-SIG-01", "A-SIG-02"):
        modes = ("normal", "priority", "stop")
        if requested not in modes or actual not in modes:
            raise LiveError("UNSUPPORTED_COMMAND")
        command = "set_mode"
        params = {"mode": actual}
    elif device_id in ("A-SW-ESS", "A-SW-URBAN", "A-SW-IND"):
        if type(requested) is not bool or type(actual) is not bool:
            raise LiveError("INVALID_ARGUMENT")
        command = "close" if actual else "open"
        params = {}
    else:
        raise LiveError("UNSUPPORTED_DEVICE")
    return build_command_effect(device_id, command, params), actual


def _validate_params(params, *, required=(), optional=()):
    keys = set(params)
    if not set(required) <= keys or keys - set(required) - set(optional):
        raise LiveError("INVALID_ARGUMENT")
