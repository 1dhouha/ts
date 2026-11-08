"""Private deterministic fixtures; no B scenario scheduler or public adapter."""
from city_core import baseline_config
from tests.support import city
from tests.effect_support import request


def traffic_city(*, queue=10.0, arrivals=0.2, surface=0.0, obstructions=None,
                 modes=None, urban=True, ess=True):
    config = baseline_config()
    state = config['initial_physical']
    state['water'].update(basin_volume_m3=6000.0, surface_volume_m3=surface)
    for road, values in state['roads'].items():
        values.update(queue_vehicles=queue, arrival_rate_veh_s=arrivals,
                      obstruction_fraction=(obstructions or {}).get(road, 0.0))
    for node, mode in (modes or {}).items():
        state['signals'][node]['effective_mode'] = mode
    state['feeders']['F-URBAN']['effective_closed'] = urban
    state['feeders']['F-ESS']['effective_closed'] = ess
    return city(initial_config=config)


def snapshot(core, run='r-test'):
    return core.observations.read_private(run).value


def step(core):
    before = snapshot(core)
    result = core.lifecycle.advance_one('r-test', before.context.revision)
    assert result.ok, result
    return result.value


def effect(core, tag, *, source='B_ACTUATOR', **fields):
    revision = snapshot(core).context.revision
    data = request(tag, revision=revision, effect_id=f'eff-{revision}', source=source, **fields)
    handle = core.scenarios if source == 'B_SCENARIO' else core.actuators
    result = handle.apply_effect(data)
    assert result.ok, result
    return data


def reserve(core, unit='AMB-01', destination='J-D1', route=('R-LOW',), mission='m-one'):
    return effect(core, 'reserve_responder', target_id=unit, mission_ref=mission,
                  destination_node_id=destination, route_road_ids=list(route))
