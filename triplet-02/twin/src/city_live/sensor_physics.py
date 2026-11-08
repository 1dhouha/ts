"""Small deterministic PDF sensor model, owned only by the live composition.

One-second balances, illustrative parameters (not calibrated engineering data).
Independent harbour/drain/reservoir storages do not rename core river assets.
"""
from dataclasses import dataclass, replace
from math import sin, pi, sqrt

MODEL_VERSION = 'pdf-city-physics-1'
HARBOUR_AREA_M2 = 1000.0
HARBOUR_TARGET_M3 = 500.0
HARBOUR_BASE_INFLOW_M3_S = .06
HARBOUR_CATCHMENT_M2 = 10000.0
PRIMARY_PUMP_CAPACITY_M3_S = .25
DRAIN_AREA_M2 = 100.0
DRAIN_CATCHMENT_M2 = 3000.0
RESERVOIR_AREA_M2 = 100.0
RESERVOIR_TARGET_M3 = 200.0


@dataclass(frozen=True, slots=True)
class SensorPhysics:
    version: str = MODEL_VERSION
    tick: int = 0
    harbour_volume_m3: float = HARBOUR_TARGET_M3
    harbour_inflow_m3_s: float = HARBOUR_BASE_INFLOW_M3_S
    harbour_outflow_m3_s: float = HARBOUR_BASE_INFLOW_M3_S
    harbour_header_bar: float = 4.0
    drain_volume_m3: float = 20.0
    drain_inflow_m3_s: float = 0.0
    drain_outflow_m3_s: float = 0.0
    reservoir_volume_m3: float = RESERVOIR_TARGET_M3
    reservoir_supply_m3_s: float = .025
    network_flow_m3_s: float = .025
    network_pressure_bar: float = 4.0
    coolant_flow_l_s: float = 60.0
    coolant_pressure_bar: float = 4.0
    process_c: float = 65.0
    ambient_c: float = 20.0


def advance(state, snapshot, epoch):
    """Return the next state; inputs are committed core conditions, never reports."""
    tick = snapshot.context.tick
    if tick != state.tick + 1:
        from .records import LiveError
        raise LiveError('SENSOR_MODEL_TICK_MISMATCH')
    rain_m_s = snapshot.physical_state.water.rain_mm_h / 1000 / 3600
    feeders = snapshot.power.feeders
    urban = feeders['F-URBAN'].energized
    industrial = feeders['F-IND'].energized
    seconds = epoch.hour * 3600 + epoch.minute * 60 + epoch.second + tick
    day_phase = 2 * pi * (seconds % 86400) / 86400
    ambient = 20 + 5 * sin(day_phase)

    inlet = HARBOUR_BASE_INFLOW_M3_S + rain_m_s * HARBOUR_CATCHMENT_M2
    requested = max(0, min(PRIMARY_PUMP_CAPACITY_M3_S,
        HARBOUR_BASE_INFLOW_M3_S + .002 * (state.harbour_volume_m3 - HARBOUR_TARGET_M3)))
    outlet = min(requested, state.harbour_volume_m3 + inlet) if urban else 0.0
    harbour = state.harbour_volume_m3 + inlet - outlet
    drain_in = rain_m_s * DRAIN_CATCHMENT_M2
    drain_out = min(.04 * sqrt(max(0, state.drain_volume_m3) / 20), state.drain_volume_m3 + drain_in)
    drain = max(0, state.drain_volume_m3 + drain_in - drain_out)

    demand = .025 + .005 * sin(day_phase)
    supply = max(0, min(.06, .025 + .001 * (RESERVOIR_TARGET_M3 - state.reservoir_volume_m3))) if urban else 0.0
    # A powered network supplies demand only while water exists. The retained
    # reservoir is independent of harbour/drain storage and the old core basin.
    distribution = min(demand, state.reservoir_volume_m3 + supply) if urban else 0.0
    reservoir = max(0, state.reservoir_volume_m3 + supply - distribution)
    pressure = 4 * sqrt(reservoir / RESERVOIR_TARGET_M3) if distribution else 0.0
    coolant = 60.0 if industrial else 0.0
    heat = .03 if industrial else .002  # residual process heat after power loss
    cooling = (.0005 * coolant / 60 + .00005) * (state.process_c - ambient)
    return replace(state, tick=tick, harbour_volume_m3=harbour,
        harbour_inflow_m3_s=inlet, harbour_outflow_m3_s=outlet,
        harbour_header_bar=2 + 4 * outlet / PRIMARY_PUMP_CAPACITY_M3_S if outlet else 0.0,
        drain_volume_m3=drain, drain_inflow_m3_s=drain_in, drain_outflow_m3_s=drain_out,
        reservoir_volume_m3=reservoir, reservoir_supply_m3_s=supply,
        network_flow_m3_s=distribution, network_pressure_bar=pressure,
        coolant_flow_l_s=coolant, coolant_pressure_bar=4.0 if industrial else 0.0,
        process_c=state.process_c + heat - cooling, ambient_c=ambient)


def measurements(state):
    """Privileged physical quantities in PDF units, shared by backup sensors."""
    return {
        'harbour_level': state.harbour_volume_m3 / HARBOUR_AREA_M2 * 100,
        'harbour_flow': state.harbour_outflow_m3_s * 1000,
        'harbour_pressure': state.harbour_header_bar,
        'process_temperature': state.process_c,
        'coolant_pressure': state.coolant_pressure_bar,
        'coolant_flow': state.coolant_flow_l_s,
        'drain_level': state.drain_volume_m3 / DRAIN_AREA_M2 * 100,
        'ambient_temperature': state.ambient_c,
        'reservoir_level': state.reservoir_volume_m3 / RESERVOIR_AREA_M2 * 100,
        'network_pressure': state.network_pressure_bar,
        'network_flow': state.network_flow_m3_s * 1000,
    }
