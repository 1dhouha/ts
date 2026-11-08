"""Fixed one-second water balance and private instantaneous/exposure facts."""
from dataclasses import dataclass, replace
import math
from types import MappingProxyType

from .power import derive_power
from .state import WaterStep
from .validation import InvalidInput, identity

# Version-bound profile; tests bind it to accepted initialization parameters.
WATER_PROFILE = MappingProxyType({"basin_capacity_m3": 6000.0,
    "surface_area_m2": 1000.0, "catchment_area_m2": 20000.0, "runoff_fraction": 1.0,
    "inlet_max_m3_s": 0.6, "gravity_max_m3_s": 0.2, "pump_max_m3_s": 1.2,
    "river_allow_below_m": 2.0})
ROAD_OFFSETS = MappingProxyType({"R-LOW": 0.0, "R-HIGH": 0.8, "R-LINK": 0.0,
                                "R-DOWN": 0.0, "R-IND": 0.0})
FLOOD_ROADS = frozenset(("R-LOW", "R-HIGH"))
FLOOD_CLOSE_M = 0.2


@dataclass(frozen=True, slots=True)
class WaterInputs:
    rain_inflow_m3_s: float
    inlet_flow_m3_s: float
    outfall_available: bool
    gravity_capacity_m3_s: float
    pump_capacity_m3_s: float


@dataclass(frozen=True, slots=True)
class FloodExposure:
    depth_m: float
    flooded: bool


def derive_water(physical, parameters=WATER_PROFILE):
    water = physical.water
    available = not water.outfall_blocked and water.river_level_m < parameters["river_allow_below_m"]
    rain = water.rain_mm_h / 1000.0 / 3600.0 * parameters["catchment_area_m2"] * parameters["runoff_fraction"]
    inlet = parameters["inlet_max_m3_s"] * physical.valve.effective_opening_fraction
    pump = (parameters["pump_max_m3_s"] * physical.pump.effective_speed_fraction
            if available and derive_power(physical).pump_running else 0.0)
    return WaterInputs(rain, inlet, available,
                       parameters["gravity_max_m3_s"] if available else 0.0, pump)


def _sum(*values):
    result = math.fsum(values)
    if not math.isfinite(result):
        raise InvalidInput("MODEL_LIMIT")
    return result


def advance_water(physical, parameters):
    """Limit actual discharge, then retain every excess volume in surface storage."""
    try:
        inputs = derive_water(physical, parameters)
        previous = physical.water
        available = _sum(previous.basin_volume_m3, previous.surface_volume_m3,
                         inputs.rain_inflow_m3_s, inputs.inlet_flow_m3_s)
        gravity = min(available, inputs.gravity_capacity_m3_s)
        pump = min(available - gravity, inputs.pump_capacity_m3_s)
        step = WaterStep(inputs.rain_inflow_m3_s, inputs.inlet_flow_m3_s, gravity, pump)
        discharged = step.outflow_m3
        capacity = parameters["basin_capacity_m3"]
        if discharged == available:
            basin, surface = 0.0, 0.0
        else:
            # Sum relative to the crest; adding a full 6000m³ basin first would
            # round away adjacent surface-threshold values on a zero-net step.
            excess = _sum(previous.basin_volume_m3 - capacity, previous.surface_volume_m3,
                          step.rain_in_m3, step.inlet_in_m3, -discharged)
            basin = capacity if excess >= 0 else _sum(previous.basin_volume_m3,
                previous.surface_volume_m3, step.rain_in_m3, step.inlet_in_m3, -discharged)
            surface = excess if excess >= 0 else 0.0
        incoming = _sum(previous.cumulative_in_m3, step.rain_in_m3, step.inlet_in_m3)
        outgoing = _sum(previous.cumulative_out_m3, gravity, pump)
        if min(basin, surface, incoming, outgoing, gravity, pump) < 0 or discharged > available:
            raise InvalidInput("MODEL_LIMIT")
        water = replace(previous, basin_volume_m3=basin, surface_volume_m3=surface,
                        cumulative_in_m3=incoming, cumulative_out_m3=outgoing, last_step=step)
        return replace(physical, water=water)
    except (OverflowError, ValueError):
        raise InvalidInput("MODEL_LIMIT") from None


def road_exposure(physical, road_id):
    identity(road_id)
    if road_id not in ROAD_OFFSETS:
        raise InvalidInput("UNKNOWN_TARGET")
    if road_id not in FLOOD_ROADS:
        return FloodExposure(0.0, False)
    area = WATER_PROFILE["surface_area_m2"]
    offset = ROAD_OFFSETS[road_id]
    volume = physical.water.surface_volume_m3
    depth = max(0.0, (volume - offset * area) / area)
    # Exact threshold equality is closed, without a broad tolerance/margin.
    return FloodExposure(depth, volume >= (offset + FLOOD_CLOSE_M) * area)


def flood_exposure(physical):
    return MappingProxyType({road: road_exposure(physical, road) for road in ROAD_OFFSETS})
