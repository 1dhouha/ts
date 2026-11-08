"""Synthetic PM2.5 background relaxation and declared source contribution."""
from dataclasses import replace
import math
from types import MappingProxyType

from .power import asset_powered
from .validation import InvalidInput

BACKGROUND_UG_M3 = 10.0


def advance_air(physical, parameters):
    """One immutable stage; units µg/m³, sources µg/m³/s, elapsed seconds."""
    seconds = parameters['step_ms'] / 1000.0
    denominator = 1.0 + parameters['air_decay_per_s'] * seconds
    plant_active = asset_powered(physical, 'PLANT-01')
    air = {}
    for zone, previous in physical.air.items():
        plant_source = parameters['plant_source_ug_m3_s'][zone] if plant_active else 0.0
        concentration = (BACKGROUND_UG_M3
            + (previous.pm25_ug_m3 - BACKGROUND_UG_M3) / denominator
            + (previous.external_source_ug_m3_s + plant_source) * seconds)
        if not math.isfinite(concentration) or concentration < 0:
            raise InvalidInput('MODEL_LIMIT')
        air[zone] = replace(previous, pm25_ug_m3=concentration)
    return replace(physical, air=MappingProxyType(air))
