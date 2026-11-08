"""Pure, narrowly allowlisted primary state transitions; no elapsed physics."""
from dataclasses import replace
from types import MappingProxyType

from .responders import release, reserve
from .validation import InvalidInput, number


def _target(target, allowed):
    if target not in allowed:
        raise InvalidInput("UNKNOWN_TARGET")


def _updated(values, key, value):
    return MappingProxyType({**values, key: value})


def apply_state(current, effect):
    state = current.snapshot.physical_state
    parameters = current.header.initial_config["parameters"]
    tag = effect["tag"]
    target = effect.get("target_id")
    if tag == "valve_position":
        _target(target, ("V-12",))
        return replace(state, valve=replace(state.valve,
                       effective_opening_fraction=number(effect["opening_fraction"], 0, 1)))
    if tag == "pump_enable":
        _target(target, ("P-03",))
        speed = 1.0 if effect["enabled"] else 0.0
        return replace(state, pump=replace(state.pump, effective_enabled=effect["enabled"],
                                           effective_speed_fraction=speed))
    if tag == "pump_speed":
        _target(target, ("P-03",))
        speed = number(effect["speed_fraction"], 0, 1)
        return replace(state, pump=replace(state.pump, effective_enabled=speed > 0,
                                           effective_speed_fraction=speed))
    if tag == "feeder_position":
        _target(target, state.feeders)
        return replace(state, feeders=_updated(state.feeders, target,
                       replace(state.feeders[target], effective_closed=effect["closed"])))
    if tag == "signal_mode":
        _target(target, state.signals)
        return replace(state, signals=_updated(state.signals, target,
                       replace(state.signals[target], effective_mode=effect["mode"])))
    if tag == "water_driver":
        water = replace(state.water, rain_mm_h=number(effect["rain_mm_h"], 0, 200),
                        river_level_m=number(effect["river_level_m"], 0, 10),
                        outfall_blocked=effect["outfall_blocked"])
        return replace(state, water=water)
    if tag == "source_driver":
        return replace(state, source=replace(state.source, source_available=effect["available"]))
    if tag == "road_driver":
        _target(target, state.roads)
        road = replace(state.roads[target], arrival_rate_veh_s=number(effect["arrival_rate_veh_s"], 0, 2),
                       obstruction_fraction=number(effect["obstruction_fraction"], 0, 1))
        return replace(state, roads=_updated(state.roads, target, road))
    if tag == "air_driver":
        _target(target, state.air)
        air = replace(state.air[target], external_source_ug_m3_s=number(effect["source_ug_m3_s"], 0, 100))
        return replace(state, air=_updated(state.air, target, air))
    # Only reserve/release reach this branch after schema validation.
    _target(target, state.responders)
    unit = state.responders[target]
    updated = (reserve(state, current.topology, parameters, effect, unit)
               if tag == "reserve_responder" else release(effect, unit))
    return replace(state, responders=_updated(state.responders, target, updated))
