"""Instantaneous private power facts for the fixed pa-model-0.1 profile."""
from dataclasses import dataclass
import math
from types import MappingProxyType
from typing import Mapping

from .validation import InvalidInput, boolean, identity, number

# Fixed accepted source/branch/asset bindings and kW profile, not live settings.
# Tests bind these to the versioned packaged catalog and fixed parameters.
ASSET_FEEDER = MappingProxyType({"HOSP-01": "F-ESS", "P-03": "F-ESS",
    "LOAD-URBAN": "F-URBAN", "J-D1": "F-URBAN", "J-D2": "F-URBAN", "PLANT-01": "F-IND"})
DEMAND_KW = MappingProxyType({"HOSP-01": 50.0, "P-03": 10.0, "LOAD-URBAN": 80.0,
                             "J-D1": 0.1, "J-D2": 0.1, "PLANT-01": 100.0})
RATED_KW = MappingProxyType({"F-ESS": 100.0, "F-URBAN": 120.0, "F-IND": 150.0})


@dataclass(frozen=True, slots=True)
class FeederPower:
    energized: bool
    requested_demand_kw: float
    delivered_load_kw: float
    unmet_demand_kw: float
    rated_capacity_kw: float
    overload: bool


@dataclass(frozen=True, slots=True)
class AssetPower:
    feeder_id: str
    powered: bool
    requested_demand_kw: float
    delivered_load_kw: float


@dataclass(frozen=True, slots=True)
class PowerState:
    feeders: Mapping[str, FeederPower]
    assets: Mapping[str, AssetPower]
    pump_running: bool

    @property
    def hospital_powered(self):
        return self.assets["HOSP-01"].powered

    @property
    def plant_active(self):
        return self.assets["PLANT-01"].powered

    @property
    def pump_delivered_demand_kw(self):
        return self.assets["P-03"].delivered_load_kw

    @property
    def total_requested_demand_kw(self):
        return math.fsum(feeder.requested_demand_kw for feeder in self.feeders.values())

    @property
    def total_delivered_load_kw(self):
        return math.fsum(feeder.delivered_load_kw for feeder in self.feeders.values())

    @property
    def total_unmet_demand_kw(self):
        return math.fsum(feeder.unmet_demand_kw for feeder in self.feeders.values())


def account_feeder(source_available, effective_closed, requested_demand_kw, rated_capacity_kw):
    """Pure kW arithmetic; no setting change, supply policy or config override."""
    source = boolean(source_available)
    closed = boolean(effective_closed)
    demand = number(requested_demand_kw)
    rating = number(rated_capacity_kw)
    if rating == 0:
        raise InvalidInput()
    energized = source and closed
    delivered = demand if energized else 0.0
    return FeederPower(energized, demand, delivered, demand - delivered,
                       rating, energized and demand > rating)


def asset_powered(physical, target_id):
    """Only assets with an accepted direct branch binding have a supply fact."""
    identity(target_id)
    if target_id not in ASSET_FEEDER:
        raise InvalidInput("UNKNOWN_TARGET")
    feeder_id = ASSET_FEEDER[target_id]
    return physical.source.source_available and physical.feeders[feeder_id].effective_closed


def derive_power(physical):
    """One deterministic evaluation of immutable committed primary inputs."""
    assets = {}
    for target_id, feeder_id in ASSET_FEEDER.items():
        demand = (DEMAND_KW[target_id] * physical.pump.effective_speed_fraction
                  if target_id == "P-03" else DEMAND_KW[target_id])
        powered = asset_powered(physical, target_id)
        assets[target_id] = AssetPower(feeder_id, powered, demand, demand if powered else 0.0)
    feeders = {name: account_feeder(physical.source.source_available,
        physical.feeders[name].effective_closed,
        math.fsum(asset.requested_demand_kw for asset in assets.values() if asset.feeder_id == name), rating)
        for name, rating in RATED_KW.items()}
    return PowerState(MappingProxyType(feeders), MappingProxyType(assets),
                      physical.pump.effective_enabled and assets["P-03"].powered)
