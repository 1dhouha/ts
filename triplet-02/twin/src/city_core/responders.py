"""Finite reservations and deterministic physical travel on a supplied route."""
from dataclasses import replace
import math
from types import MappingProxyType

from .state import ResponderState
from .traffic import capacity, ROAD_SIGNALS
from .validation import InvalidInput

def edge_seconds(physical, topology, parameters, road_id):
    seconds = (parameters['road_length_m'][road_id] / parameters['free_speed_m_s'][road_id]
               + physical.roads[road_id].queue_vehicles / capacity(physical, topology, parameters, road_id))
    if not math.isfinite(seconds):
        raise InvalidInput('MODEL_LIMIT')
    return seconds


def other_endpoint(topology, road_id, node):
    endpoints = tuple(r.target_id for r in topology.relationships if r.source_id == road_id
                      and r.relation in ('endpoint_a', 'endpoint_b'))
    return endpoints[1] if node == endpoints[0] else endpoints[0]


def reserve(physical, topology, parameters, effect, unit):
    if unit.status != "available":
        raise InvalidInput("RESOURCE_UNAVAILABLE")
    destination = effect["destination_node_id"]
    if destination not in {e.id for e in topology.entities if e.kind == "junction"}:
        raise InvalidInput("UNKNOWN_TARGET")
    route = effect["route_road_ids"]
    if any(road not in physical.roads for road in route):
        raise InvalidInput("UNKNOWN_TARGET")
    if len(set(route)) != len(route) or (not route and destination != unit.at_node_id):
        raise InvalidInput("ROUTE_UNAVAILABLE")
    node = unit.at_node_id
    for road in route:
        endpoints = tuple(r.target_id for r in topology.relationships if r.source_id == road
                          and r.relation in ("endpoint_a", "endpoint_b"))
        if node not in endpoints or capacity(physical, topology, parameters, road) <= 0:
            raise InvalidInput("ROUTE_UNAVAILABLE")
        node = endpoints[1] if node == endpoints[0] else endpoints[0]
    if node != destination:
        raise InvalidInput("ROUTE_UNAVAILABLE")
    remaining = 0.0
    if route:
        first = route[0]
        remaining = edge_seconds(physical, topology, parameters, first)
    return ResponderState("enroute" if route else "arrived", unit.at_node_id,
                          effect["mission_ref"], destination, route, 0, remaining)


def release(effect, unit):
    if (unit.status != "arrived" or unit.mission_ref != effect["mission_ref"]
            or unit.at_node_id != unit.destination_node_id):
        raise InvalidInput("RESOURCE_UNAVAILABLE")
    return replace(unit, status="available", mission_ref=None, destination_node_id=None,
                   route_road_ids=(), edge_index=0, edge_remaining_s=0.0)


def advance_unit(physical, topology, parameters, unit):
    if unit.status not in ('enroute', 'blocked'):
        return unit
    budget = 1.0
    index, remaining, node = unit.edge_index, unit.edge_remaining_s, unit.at_node_id
    # At most five distinct roads; no callbacks, reroute or banked time.
    while index < len(unit.route_road_ids):
        road = unit.route_road_ids[index]
        if capacity(physical, topology, parameters, road) <= 0:
            return replace(unit, status='blocked', at_node_id=node,
                           edge_index=index, edge_remaining_s=remaining)
        if remaining == 0:
            remaining = edge_seconds(physical, topology, parameters, road)
        consumed = min(budget, remaining)
        remaining -= consumed
        budget -= consumed
        if remaining > 0:
            return replace(unit, status='enroute', at_node_id=node,
                           edge_index=index, edge_remaining_s=remaining)
        node = other_endpoint(topology, road, node)
        index += 1
        # Even when budget is exhausted, enter/sample or mark the next edge
        # blocked in this tick; that entry delay must not be resampled later.
    return replace(unit, status='arrived', at_node_id=node,
                   edge_index=index, edge_remaining_s=0.0)


def advance_responders(physical, topology, parameters):
    units = {name: advance_unit(physical, topology, parameters, unit)
             for name, unit in physical.responders.items()}
    return replace(physical, responders=MappingProxyType(units))
