"""Immutable primary state, core-generated water accounting and pure geometry."""
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from .catalog import CATALOG_VERSION, CONTRACT_VERSION, MODEL_VERSION, baseline_config
from .validation import InvalidInput, boolean, fixed_profile, number, shape, version


@dataclass(frozen=True, slots=True)
class WaterStep:
    rain_in_m3: float
    inlet_in_m3: float
    gravity_out_m3: float
    pump_out_m3: float

    @property
    def inflow_m3(self):
        return self.rain_in_m3 + self.inlet_in_m3

    @property
    def outflow_m3(self):
        return self.gravity_out_m3 + self.pump_out_m3


@dataclass(frozen=True, slots=True)
class WaterState:
    basin_volume_m3: float
    surface_volume_m3: float
    rain_mm_h: float
    river_level_m: float
    outfall_blocked: bool
    cumulative_in_m3: float = 0.0
    cumulative_out_m3: float = 0.0
    last_step: WaterStep | None = None

    @property
    def basin_level_m(self):
        return self.basin_volume_m3 / 2000.0

    @property
    def surface_depth_m(self):
        return self.surface_volume_m3 / 1000.0


@dataclass(frozen=True, slots=True)
class ValveState:
    effective_opening_fraction: float


@dataclass(frozen=True, slots=True)
class PumpState:
    effective_enabled: bool
    effective_speed_fraction: float


@dataclass(frozen=True, slots=True)
class SupplyState:
    source_available: bool


@dataclass(frozen=True, slots=True)
class FeederState:
    effective_closed: bool


@dataclass(frozen=True, slots=True)
class RoadState:
    queue_vehicles: float
    arrival_rate_veh_s: float
    obstruction_fraction: float


@dataclass(frozen=True, slots=True)
class SignalState:
    effective_mode: str


@dataclass(frozen=True, slots=True)
class ResponderState:
    status: str
    at_node_id: str
    mission_ref: str | None
    destination_node_id: str | None
    route_road_ids: tuple[str, ...]
    edge_index: int
    edge_remaining_s: float


@dataclass(frozen=True, slots=True)
class AirState:
    pm25_ug_m3: float
    external_source_ug_m3_s: float


@dataclass(frozen=True, slots=True)
class PhysicalState:
    water: WaterState
    valve: ValveState
    pump: PumpState
    source: SupplyState
    feeders: Mapping[str, FeederState]
    roads: Mapping[str, RoadState]
    signals: Mapping[str, SignalState]
    responders: Mapping[str, ResponderState]
    air: Mapping[str, AirState]


def _record(value, cls, converters):
    # Only declared initialization converters are writable. Water accounting
    # defaults belong to A, never input configuration/effects.
    shape(value, converters)
    return cls(**{key: convert(value[key]) for key, convert in converters.items()})


def _signal(value):
    shape(value, ("effective_mode",))
    mode = value["effective_mode"]
    if type(mode) is not str or mode not in ("normal", "priority", "stop"):
        raise InvalidInput()
    return SignalState(mode)


def _responder(value):
    shape(value, ResponderState.__dataclass_fields__)
    expected = {"status": "available", "at_node_id": "J-H1", "mission_ref": None,
                "destination_node_id": None, "route_road_ids": [], "edge_index": 0,
                "edge_remaining_s": 0.0}
    # Phase 02 supports initial construction, not mission checkpoint recovery.
    if value != expected or type(value["edge_index"]) is not int:
        raise InvalidInput()
    if type(value["route_road_ids"]) is not list:
        raise InvalidInput()
    remaining = number(value["edge_remaining_s"], 0, 0)
    return ResponderState("available", "J-H1", None, None, (), 0, remaining)


def _map(value, ids, parser):
    shape(value, ids)
    return MappingProxyType({key: parser(value[key]) for key in sorted(ids)})


def parse_config(value, topology):
    shape(value, ("model_version", "catalog_version", "contract_version",
                  "parameters", "initial_physical"))
    version(value["model_version"], MODEL_VERSION)
    version(value["catalog_version"], CATALOG_VERSION)
    version(value["contract_version"], CONTRACT_VERSION)
    fixed_profile(value["parameters"], baseline_config()["parameters"])
    actual = shape(value["initial_physical"], PhysicalState.__dataclass_fields__)
    water = _record(actual["water"], WaterState, {
        "basin_volume_m3": lambda n: number(n, 0, 6000),
        "surface_volume_m3": number, "rain_mm_h": lambda n: number(n, 0, 200),
        "river_level_m": lambda n: number(n, 0, 10), "outfall_blocked": boolean})
    valve = _record(actual["valve"], ValveState,
                    {"effective_opening_fraction": lambda n: number(n, 0, 1)})
    pump_data = shape(actual["pump"], ("effective_enabled",))
    pump_enabled = boolean(pump_data["effective_enabled"])
    pump = PumpState(pump_enabled, 1.0 if pump_enabled else 0.0)
    source = _record(actual["source"], SupplyState, {"source_available": boolean})
    ids = lambda kind: {e.id for e in topology.entities if e.kind == kind}
    feeders = _map(actual["feeders"], ids("feeder"), lambda v: _record(
        v, FeederState, {"effective_closed": boolean}))
    roads = _map(actual["roads"], ids("road"), lambda v: _record(v, RoadState, {
        "queue_vehicles": number, "arrival_rate_veh_s": lambda n: number(n, 0, 2),
        "obstruction_fraction": lambda n: number(n, 0, 1)}))
    signal_ids = {d.target_id for d in topology.devices if d.family == "traffic_signal"}
    signals = _map(actual["signals"], signal_ids, _signal)
    responders = _map(actual["responders"], ids("ambulance") | ids("police"), _responder)
    air = _map(actual["air"], ids("zone"), lambda v: _record(v, AirState, {
        "pm25_ug_m3": number, "external_source_ug_m3_s": lambda n: number(n, 0, 100)}))
    return PhysicalState(water, valve, pump, source, feeders, roads, signals, responders, air)
