"""Frozen immediate-effect input/results and a run-scoped immutable ID index."""
from dataclasses import dataclass
import hashlib
import math
from types import MappingProxyType
from typing import Mapping

from .catalog import CONTRACT_VERSION
from .clock import validate_revision
from .snapshots import Context
from .validation import InvalidInput, boolean, identity, shape, version

# Explicit field allowlist, not a path-based world setter.
SCHEMAS = MappingProxyType({
    "valve_position": ("target_id", "opening_fraction"),
    "pump_enable": ("target_id", "enabled"),
    "pump_speed": ("target_id", "speed_fraction"),
    "feeder_position": ("target_id", "closed"),
    "signal_mode": ("target_id", "mode"),
    "reserve_responder": ("target_id", "mission_ref", "destination_node_id", "route_road_ids"),
    "release_responder": ("target_id", "mission_ref"),
    "water_driver": ("rain_mm_h", "river_level_m", "outfall_blocked"),
    "source_driver": ("available",),
    "road_driver": ("target_id", "arrival_rate_veh_s", "obstruction_fraction"),
    "air_driver": ("target_id", "source_ug_m3_s"),
})
DRIVERS = frozenset(("water_driver", "source_driver", "road_driver", "air_driver"))


@dataclass(frozen=True, slots=True)
class EffectRequest:
    contract_version: str
    run_id: str
    effect_id: str
    expected_revision: int
    source: str
    correlation_id: str | None
    effect: Mapping


@dataclass(frozen=True, slots=True)
class EffectReceipt:
    context: Context
    effect_id: str
    sequence: int
    correlation_id: str | None
    outcome: str = "APPLIED"
    duplicate: bool = False


def _value(key, value):
    if key.endswith("_id") or key == "mission_ref":
        return identity(value)
    if key in ("enabled", "closed", "available", "outfall_blocked"):
        return boolean(value)
    if key == "mode":
        if type(value) is not str or value not in ("normal", "priority", "stop"):
            raise InvalidInput()
        return value
    if key == "route_road_ids":
        if type(value) not in (list, tuple) or len(value) > 5:
            raise InvalidInput()
        return tuple(identity(item) for item in value)
    if type(value) not in (int, float):
        raise InvalidInput()
    try:
        numeric = float(value)
    except OverflowError:
        raise InvalidInput() from None
    if not math.isfinite(numeric):
        raise InvalidInput()
    return 0.0 if numeric == 0 else numeric


def parse_request(value, source):
    """Detach/validate syntax before lookup; state/range checks follow duplicate lookup."""
    shape(value, EffectRequest.__dataclass_fields__)
    version(value["contract_version"], CONTRACT_VERSION)
    for key in ("run_id", "effect_id"):
        identity(value[key])
    validate_revision(value["expected_revision"])
    if type(value["source"]) is not str or value["source"] not in ("B_ACTUATOR", "B_SCENARIO"):
        raise InvalidInput()
    correlation = value["correlation_id"]
    if correlation is not None and (type(correlation) is not str or not 1 <= len(correlation) <= 128
            or not correlation.isascii() or any(ord(c) < 32 or ord(c) == 127 for c in correlation)):
        raise InvalidInput()
    effect = value["effect"]
    if type(effect) is not dict or type(effect.get("tag")) is not str or effect["tag"] not in SCHEMAS:
        raise InvalidInput()
    tag = effect["tag"]
    shape(effect, ("tag", *SCHEMAS[tag]))
    if value["source"] != source or (tag in DRIVERS and source != "B_SCENARIO"):
        raise InvalidInput("FORBIDDEN_CAPABILITY")
    normalized = MappingProxyType({"tag": tag, **{key: _value(key, effect[key]) for key in SCHEMAS[tag]}})
    return EffectRequest(value["contract_version"], value["run_id"], value["effect_id"],
                         value["expected_revision"], source, correlation, normalized)


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    request: EffectRequest
    receipt: EffectReceipt


@dataclass(frozen=True, slots=True)
class EffectLedger:
    # Copy one bucket plus the fixed-size index per commit, not the entire run.
    buckets: tuple = (None,) * 256
    count: int = 0

    def _index(self, effect_id):
        return hashlib.sha256(effect_id.encode("ascii")).digest()[0]

    def find(self, effect_id):
        bucket = self.buckets[self._index(effect_id)]
        return bucket.get(effect_id) if bucket is not None else None

    def append(self, request, receipt):
        index = self._index(request.effect_id)
        bucket = MappingProxyType({**(self.buckets[index] or {}),
                                   request.effect_id: LedgerEntry(request, receipt)})
        buckets = self.buckets[:index] + (bucket,) + self.buckets[index + 1:]
        return EffectLedger(buckets, self.count + 1)
