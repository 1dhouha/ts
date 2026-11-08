"""Fixed aggregate traffic rules shared by access, reservation and movement."""
from dataclasses import dataclass, replace
import math
from types import MappingProxyType

from .power import asset_powered
from .water import road_exposure
from .validation import InvalidInput

# Accepted version-bound profile; tests bind these to packaged configuration.
ROAD_SIGNALS = MappingProxyType({'R-LOW': 'J-D1', 'R-IND': 'J-D1',
                                 'R-DOWN': 'J-D2', 'R-LINK': 'J-D2'})
TRAFFIC_PROFILE = MappingProxyType({
    'road_base_capacity_veh_s': MappingProxyType({road: .5 for road in
        ('R-LOW', 'R-HIGH', 'R-LINK', 'R-DOWN', 'R-IND')}),
    'signal_capacity_multiplier': MappingProxyType({
        'normal': 1.0, 'priority': 1.5, 'stop': 0.0, 'unpowered': 0.0})})


@dataclass(frozen=True, slots=True)
class RoadConditions:
    depth_m: float
    usable: bool
    capacity_veh_s: float


def road_conditions(physical, road_id, parameters=TRAFFIC_PROFILE):
    exposure = road_exposure(physical, road_id)
    road = physical.roads[road_id]
    usable = not exposure.flooded and road.obstruction_fraction < 1
    multiplier = 1.0
    controller = ROAD_SIGNALS.get(road_id)
    if controller is not None:
        mode = physical.signals[controller].effective_mode if asset_powered(
            physical, controller) else 'unpowered'
        multiplier = parameters['signal_capacity_multiplier'][mode]
    capacity = (parameters['road_base_capacity_veh_s'][road_id]
                * (1 - road.obstruction_fraction) * multiplier) if usable else 0.0
    return RoadConditions(exposure.depth_m, usable, capacity)


def capacity(physical, topology, parameters, road_id):
    """Compatibility signature for the accepted Phase04 reservation helper."""
    return road_conditions(physical, road_id, parameters).capacity_veh_s


def derive_traffic(physical):
    return MappingProxyType({road: road_conditions(physical, road) for road in physical.roads})


def advance_traffic(physical, topology, parameters):
    """External per-link arrivals/exits; never clip a closed road's queue."""
    roads = {}
    for road_id, road in physical.roads.items():
        try:
            available = math.fsum((road.queue_vehicles, road.arrival_rate_veh_s))
            departed = min(available, capacity(physical, topology, parameters, road_id))
            queue = available - departed
            if not math.isfinite(queue) or queue < 0:
                raise InvalidInput('MODEL_LIMIT')
        except (OverflowError, ValueError):
            raise InvalidInput('MODEL_LIMIT') from None
        roads[road_id] = replace(road, queue_vehicles=queue)
    return replace(physical, roads=MappingProxyType(roads))
