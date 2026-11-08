"""Private current hospital connectivity; no operational route-selection policy."""
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from .traffic import road_conditions

# Exact accepted immutable road graph, independently checked against catalog.
ROAD_ENDPOINTS = MappingProxyType({'R-LOW': ('J-D1', 'J-H1'),
    'R-HIGH': ('J-R1', 'J-H1'), 'R-LINK': ('J-D2', 'J-R1'),
    'R-DOWN': ('J-D1', 'J-D2'), 'R-IND': ('J-I1', 'J-D1')})


@dataclass(frozen=True, slots=True)
class HospitalAccess:
    facility_id: str
    destination_node_id: str
    reachable_by_origin: Mapping[str, bool]


def derive_access(physical):
    nodes = sorted({node for ends in ROAD_ENDPOINTS.values() for node in ends})
    graph = {node: [] for node in nodes}
    for road, (left, right) in ROAD_ENDPOINTS.items():
        if road_conditions(physical, road).capacity_veh_s > 0:
            graph[left].append(right)
            graph[right].append(left)
    reached = {'J-H1'}
    pending = ['J-H1']
    while pending:
        node = pending.pop()
        for neighbor in graph[node]:
            if neighbor not in reached:
                reached.add(neighbor)
                pending.append(neighbor)
    return HospitalAccess('HOSP-01', 'J-H1',
                          MappingProxyType({node: node in reached for node in nodes}))
