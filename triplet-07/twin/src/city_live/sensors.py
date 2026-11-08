"""Committed truth → bounded measurement buffers → fault → separate DTOs."""
from dataclasses import replace
from types import MappingProxyType

from city_core.transitions import freeze, plain
from .faults import MAX_LAG, create_fault, reported
from .records import LiveError, identifier, integer, numeric, timestamp
from .registry import load_registry, replacement


def measurement(source, snapshot, departures, model_values=None):
    physical = snapshot.physical_state
    if source is None:
        return None
    if source in ('basin', 'river', 'surface'):
        metres = {'basin': physical.water.basin_level_m, 'river': physical.water.river_level_m,
                  'surface': physical.water.surface_depth_m}[source]
        return metres * 100
    family, target = source.split(':', 1)
    if family == 'model':
        if model_values is None or target not in model_values:
            raise LiveError('INVALID_BINDING')
        return model_values[target]
    if family == 'departures':
        return departures[target]
    if family == 'power':
        return snapshot.power.feeders[target].delivered_load_kw
    if family == 'air':
        return physical.air[target].pm25_ug_m3
    raise LiveError('INVALID_BINDING')


class SensorBank:
    def __init__(self, seed, epoch, registry=None):
        self.registry = load_registry() if registry is None else registry
        self.seed, self.epoch = seed, epoch
        self.reset()

    def reset(self):
        # Registration/replacement history intentionally survives run reset.
        self.faults, self.buffers, self.latest = freeze({}), freeze({}), freeze({})
        self.last_valid, self.counter, self.last_tick = freeze({}), 0, 0
        self.scenario_inputs, self.forced_samples = freeze({}), freeze({})

    def set_scenario_input(self, scenario_id, sensor_id, value):
        sensor = self.sensor(sensor_id)
        if sensor.source is None:
            raise LiveError('SENSOR_UNBOUND')
        identifier(scenario_id)
        numeric(value, sensor.hard_limits[0], sensor.hard_limits[1])
        self.scenario_inputs = freeze({
            **self.scenario_inputs,
            sensor_id: {'scenario_id': scenario_id, 'value': float(value)},
        })

    def clear_scenario_inputs(self, scenario_id):
        self.scenario_inputs = freeze({
            key: value for key, value in self.scenario_inputs.items()
            if value['scenario_id'] != scenario_id
        })
        self.forced_samples = freeze({
            key: tick for key, tick in self.forced_samples.items()
            if key in self.scenario_inputs
        })

    def force_sample(self, sensor_id, tick):
        self.sensor(sensor_id)
        integer(tick, 0, 100000)
        self.forced_samples = freeze({**self.forced_samples, sensor_id: tick})

    def sensor(self, sensor_id):
        sensor = next((r for r in self.registry if r.sensor_id == sensor_id and r.active), None)
        if sensor is None:
            raise LiveError('SENSOR_NOT_FOUND')
        return sensor

    def inject(self, sensor_id, kind, params, tick):
        sensor = self.sensor(sensor_id)
        if sensor.source is None:
            raise LiveError('SENSOR_UNBOUND')
        if sensor_id in self.faults:
            raise LiveError('FAULT_ALREADY_ACTIVE')
        fault = create_fault(f'f-{self.counter + 1:08d}', kind, tick, params, self.last_valid.get(sensor_id))
        self.counter += 1
        self.faults = freeze({**self.faults, sensor_id: fault})
        return fault

    def clear(self, sensor_id, repair=False):
        self.sensor(sensor_id)
        fault = self.faults.get(sensor_id)
        if fault is not None and fault.type == 'DEAD' and not repair:
            raise LiveError('REPAIR_REQUIRED')
        self.faults = freeze({key: value for key, value in self.faults.items() if key != sensor_id})

    def replace(self, old_id, new_id, tick, run_id=None):
        updated = replacement(self.registry, old_id, new_id, tick, run_id)
        self.clear(old_id, repair=True)
        self.registry = updated
        self.latest = freeze({key: value for key, value in self.latest.items() if key != old_id})

    def capture(self, snapshot, departures, model_values=None):
        tick = snapshot.context.tick
        if tick <= self.last_tick:
            raise LiveError('DUPLICATE_SAMPLE_TICK')
        buffers = {}
        for sensor in self.registry:
            if not sensor.active:
                continue
            value = measurement(sensor.source, snapshot, departures, model_values)
            history = self.buffers.get(sensor.sensor_id, ())
            # A delayed window needs its entire sampling interval before the
            # delayed endpoint; point measurements need only that endpoint.
            window = sensor.interval if sensor.source and sensor.source.startswith('departures:') else 1
            buffers[sensor.sensor_id] = (*history, (tick, value))[-(MAX_LAG + window):]
        # Histories contain immutable tuples/scalars already. Do not recursively
        # copy every retained sample on every tick (measured in the first suite).
        self.buffers, self.last_tick = MappingProxyType(buffers), tick
        expired = tuple(key for key, fault in self.faults.items()
                        if fault.end_tick is not None and tick >= fault.end_tick)
        self.faults = freeze({key: fault for key, fault in self.faults.items() if key not in expired})
        return expired

    def sample(self, tick, publish):
        readings = []
        for sensor in sorted(self.registry, key=lambda r: r.sensor_id):
            forced = self.forced_samples.get(sensor.sensor_id) == tick
            if sensor.active and (tick % sensor.interval == 0 or forced):
                scenario_input = self.scenario_inputs.get(sensor.sensor_id)
                reading = self._reading(
                    sensor,
                    tick,
                    scenario_input['value'] if scenario_input is not None else None,
                    scenario_input is not None,
                )
                public = publish(public_reading(reading))
                private = freeze({**reading, 'seq': public['seq'], 'run_id': public['run_id']})
                self.latest = MappingProxyType({**self.latest, sensor.sensor_id: private})
                if private['reported_value'] is not None:
                    self.last_valid = freeze({**self.last_valid, sensor.sensor_id: private['reported_value']})
                readings.append(private)
                if forced:
                    self.forced_samples = freeze({
                        key: value for key, value in self.forced_samples.items()
                        if key != sensor.sensor_id
                    })
        return tuple(readings)

    def _reading(self, sensor, tick, scenario_value=None, has_scenario_value=False):
        fault = self.faults.get(sensor.sensor_id)
        sample_tick = tick - fault.params['lag_ticks'] if fault and fault.type == 'DELAYED' else tick
        history = self.buffers.get(sensor.sensor_id, ())
        if sensor.source and sensor.source.startswith('departures:'):
            window = [value for t, value in history if sample_tick - sensor.interval < t <= sample_tick]
            actual = sum(window) / sensor.interval * 60 if len(window) == sensor.interval else None
        else:
            actual = next((value for t, value in reversed(history) if t == sample_tick), None)
        if has_scenario_value:
            actual = scenario_value
        value, status = reported(fault, actual, tick, sensor.sensor_id, self.seed)
        if value is not None:
            value = round(max(sensor.hard_limits[0], min(sensor.hard_limits[1], value)), 2)
        true = round(actual, 2) if actual is not None else None
        if actual is None and value is None and status == 'OK':
            status = 'OFFLINE'
        return {'schema_version': 'twin-reading-0.2-local', 'sensor_id': sensor.sensor_id,
                'zone_id': sensor.zone_id, 'type': sensor.type, 'unit': sensor.unit,
                'timestamp': timestamp(self.epoch, max(0, sample_tick)), 'sim_tick': max(0, sample_tick),
                'delivery_tick': tick, 'actual_value': true, 'reported_value': value,
                'delta': round(value - true, 2) if value is not None and true is not None else None,
                'status': status, 'fault': fault.type if fault else None, 'available': value is not None,
                'backup_ids': list(sensor.backup_ids), 'linked_device_ids': list(sensor.linked_device_ids)}

    def compare(self, sensor_id):
        self.sensor(sensor_id)
        value = self.latest.get(sensor_id)
        if value is None:
            raise LiveError('NO_SAMPLE')
        return plain(value)


def public_reading(reading):
    # Construct from an explicit allowlist; never subtract private fields.
    fields = ('schema_version', 'sensor_id', 'zone_id', 'type', 'unit', 'timestamp',
              'sim_tick', 'delivery_tick', 'reported_value', 'status', 'available',
              'backup_ids', 'linked_device_ids')
    return {key: reading[key] for key in fields}
