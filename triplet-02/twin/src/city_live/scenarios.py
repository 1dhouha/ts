"""Local operator presets, replaceable by B; all effects use existing core tags."""
from city_core.transitions import freeze
from .records import LiveError, exact_keys, identifier, numeric
from .demand import ROADS

KINDS = ('power_outage', 'power_restore', 'heavy_rain', 'rain_clear', 'pollution',
         'pollution_clear', 'accident', 'partial_block', 'road_clear', 'pump_off',
         'pump_on', 'signal_stop', 'signal_normal', 'signal_priority', 'feeder', 'valve', 'dispatch', 'release',
         'sensor_fault', 'sensor_clear')
PRESETS = freeze({
    'normal': ((0, 'power_restore', {}), (0, 'rain_clear', {})),
    'combined_crisis': ((0, 'accident', {'target': 'R-LOW'}),
        (5, 'power_outage', {}), (10, 'heavy_rain', {}), (15, 'pollution', {}),
        (60, 'power_restore', {}), (70, 'road_clear', {'target': 'R-LOW'}),
        (75, 'rain_clear', {}), (80, 'pollution_clear', {}), (80, 'pump_on', {})),
    'recovery': ((0, 'power_restore', {}), (0, 'rain_clear', {}),
                 (0, 'pollution_clear', {}), (0, 'pump_on', {})),
})


def validate(kind, params):
    if kind not in KINDS:
        raise LiveError('UNSUPPORTED_OPERATION')
    if kind in ('sensor_fault', 'sensor_clear'):
        required = ('sensor_id', 'fault_type', 'parameters') if kind == 'sensor_fault' else ('sensor_id',)
        exact_keys(params, required)
        identifier(params['sensor_id'])
        if kind == 'sensor_fault':
            from .faults import create_fault
            create_fault('validation-only', params['fault_type'], 0, params['parameters'], 0)
    elif kind in ('accident', 'partial_block', 'road_clear'):
        exact_keys(params, ('target',))
        if params['target'] not in ROADS:
            raise LiveError('UNKNOWN_TARGET')
    elif kind in ('signal_stop', 'signal_normal', 'signal_priority'):
        exact_keys(params, ('target',))
        if params['target'] not in ('J-D1', 'J-D2'):
            raise LiveError('UNKNOWN_TARGET')
    elif kind == 'feeder':
        exact_keys(params, ('target', 'closed'))
        if params['target'] not in ('F-ESS', 'F-URBAN', 'F-IND') or type(params['closed']) is not bool:
            raise LiveError('INVALID_ARGUMENT')
    elif kind == 'valve':
        exact_keys(params, ('opening',))
        numeric(params['opening'], 0, 1)
    elif kind == 'dispatch':
        exact_keys(params, ('target', 'mission', 'destination', 'route'))
        for key in ('target', 'mission', 'destination'):
            identifier(params[key])
        if type(params['route']) not in (list, tuple) or len(params['route']) > 5:
            raise LiveError('INVALID_ARGUMENT')
        for road in params['route']:
            identifier(road)
    elif kind == 'release':
        exact_keys(params, ('target', 'mission'))
        identifier(params['target'])
        identifier(params['mission'])
    else:
        exact_keys(params, ())
    return freeze(params)


def effect_for(kind, params, physical):
    if kind.startswith('power_'):
        return {'tag': 'source_driver', 'available': kind == 'power_restore'}
    if kind in ('heavy_rain', 'rain_clear'):
        return {'tag': 'water_driver', 'rain_mm_h': 180 if kind == 'heavy_rain' else 0,
                'river_level_m': physical.water.river_level_m,
                'outfall_blocked': physical.water.outfall_blocked}
    if kind in ('pollution', 'pollution_clear'):
        return {'tag': 'air_driver', 'target_id': 'Z-INDUSTRIAL',
                'source_ug_m3_s': 5 if kind == 'pollution' else 0}
    if kind in ('accident', 'partial_block', 'road_clear'):
        target = params['target']
        return {'tag': 'road_driver', 'target_id': target,
                'arrival_rate_veh_s': physical.roads[target].arrival_rate_veh_s,
                'obstruction_fraction': {'accident': 1, 'partial_block': .5, 'road_clear': 0}[kind]}
    if kind in ('pump_on', 'pump_off'):
        return {'tag': 'pump_enable', 'target_id': 'P-03', 'enabled': kind == 'pump_on'}
    if kind in ('signal_stop', 'signal_normal', 'signal_priority'):
        return {'tag': 'signal_mode', 'target_id': params['target'],
                'mode': kind.removeprefix('signal_')}
    if kind == 'feeder':
        return {'tag': 'feeder_position', 'target_id': params['target'], 'closed': params['closed']}
    if kind == 'valve':
        return {'tag': 'valve_position', 'target_id': 'V-12', 'opening_fraction': params['opening']}
    if kind == 'release':
        return {'tag': 'release_responder', 'target_id': params['target'], 'mission_ref': params['mission']}
    return {'tag': 'reserve_responder', 'target_id': params['target'], 'mission_ref': params['mission'],
            'destination_node_id': params['destination'], 'route_road_ids': list(params['route'])}
