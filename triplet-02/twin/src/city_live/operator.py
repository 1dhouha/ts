"""Privileged local diagnostics and provisional device adapter, never public."""
from functools import wraps
import json
import sqlite3

from city_core.transitions import freeze, plain
from .commands import build_command_effect, build_legacy_command_effect
from .device_catalog import load_catalog
from .records import LiveError, identifier, integer, timestamp
from twin_persistence.store import command_fingerprint


def operator_call(method):
    @wraps(method)
    def call(self, *args, **kwargs):
        with self._condition:
            if self._state == 'CLOSED':
                raise LiveError('CLOSED')
            return method(self, *args, **kwargs)
    return call


class OperatorControls:
    @operator_call
    def operator_request(self, envelope):
        """Validate, deduplicate and apply one local privileged intent atomically."""
        from .operator_requests import apply_request
        return apply_request(self, envelope)

    @operator_call
    def diagnostics(self):
        snapshot = self._snapshot()
        return {'visibility': 'PRIVATE_OPERATOR_ONLY', 'context': plain(snapshot.context),
                'physical_state': plain(snapshot.physical_state),
                'power': {**plain(snapshot.power), 'hospital_powered': snapshot.power.hospital_powered},
                'traffic': plain(snapshot.traffic), 'flood': plain(snapshot.flood),
                'access': plain(snapshot.access), 'active_faults': plain(self._sensors.faults),
                'active_disturbances': disturbances(snapshot.physical_state),
                'traffic_profile': self._traffic.profile,
                'sensor_model': plain(self._sensor_physics),
                'water_levels': {'basin_m': snapshot.physical_state.water.basin_level_m,
                                 'surface_m': snapshot.physical_state.water.surface_depth_m}}

    @operator_call
    def actions(self):
        return plain(self._actions)

    @operator_call
    def events(self):
        return {'visibility': 'PRIVATE_OPERATOR_ONLY', 'events': plain(self._events)}

    @operator_call
    def sensors(self):
        return plain(self._sensors.registry)

    @operator_call
    def actuator_inventory(self):
        physical = plain(self._snapshot().physical_state)
        inventory = []
        for actuator in load_catalog().actuators:
            family = actuator.family
            target = actuator.target_id
            state = None
            if actuator.supported and target is not None:
                state_group = {
                    'pump': 'pump',
                    'valve': 'valve',
                    'signal': 'signals',
                    'feeder': 'feeders',
                    'responder': 'responders',
                }.get(family)
                if state_group is not None:
                    group = physical[state_group]
                    state = group if family in ('pump', 'valve') else group.get(target)
            inventory.append({
                'device_id': actuator.device_id,
                'zone_id': actuator.zone_id,
                'core_zone_id': actuator.core_zone_id,
                'type': actuator.type,
                'family': family,
                'target_id': target,
                'supported': actuator.supported,
                'aliases': list(actuator.aliases),
                'commands': list(actuator.commands),
                'simulated_state': state,
            })
        return plain(tuple(inventory))

    @operator_call
    def sensor_compare(self, sensor_id):
        return self._sensors.compare(sensor_id)

    @operator_call
    def sensor_fault(self, sensor_id, kind, params):
        fault = self._sensors.inject(sensor_id, kind, params, self._snapshot().context.tick)
        self._event('FAULT_ACTIVATED', {'sensor_id': sensor_id, 'fault': plain(fault)})
        return plain(fault)

    @operator_call
    def sensor_clear(self, sensor_id):
        self._sensors.clear(sensor_id)
        self._event('FAULT_CLEARED', {'sensor_id': sensor_id})

    @operator_call
    def sensor_repair(self, sensor_id):
        self._sensors.clear(sensor_id, repair=True)
        self._event('FAULT_CLEARED', {'sensor_id': sensor_id, 'repair': True})

    @operator_call
    def sensor_replace(self, old_id, new_id):
        self._sensors.replace(old_id, new_id, self._snapshot().context.tick, self._run_id)
        self._event('DEVICE_REPLACED', {'old_id': old_id, 'new_id': new_id})

    @operator_call
    def execute_actuator_command(self, device_id, command, params, *, command_id,
                                 available=True, run_id=None, expected_revision=None,
                                 legacy_request=None, correlation_id=None,
                                 scenario_id=None):
        """Validate, deduplicate and apply one actuator command atomically."""
        identifier(command_id)
        effective_run_id = self._run_id if run_id is None else run_id
        try:
            fingerprint = json.dumps(
                {
                    'request_id': command_id,
                    'device_id': device_id,
                    'command': command,
                    'params': params,
                    'run_id': effective_run_id,
                    'expected_revision': expected_revision,
                    'available': available,
                    'legacy_request': legacy_request,
                },
                sort_keys=True,
                allow_nan=False,
                separators=(',', ':'),
            )
        except (TypeError, ValueError, OverflowError, RecursionError):
            raise LiveError('INVALID_ARGUMENT') from None

        durable = self._persistence
        if durable is not None:
            try:
                existing = durable.request(command_id)
                if existing is not None:
                    try:
                        return durable.duplicate_result(
                            existing, command_fingerprint(json.loads(fingerprint)),
                        )
                    except ValueError as error:
                        raise LiveError(str(error)) from None
            except LiveError:
                raise
            except (sqlite3.Error, OSError, RuntimeError, ValueError) as error:
                raise LiveError('PERSISTENCE_FAILURE') from error
        else:
            existing = self._actuator_requests.get(command_id)
            if existing is not None:
                if existing['fingerprint'] != fingerprint:
                    raise LiveError('IDEMPOTENCY_CONFLICT')
                return plain(existing['result'])
        if durable is None and len(self._actuator_requests) >= 2048:
            raise LiveError('COMMAND_BUDGET_EXHAUSTED')
        if durable is not None:
            try:
                request_count = durable.request_count(effective_run_id)
            except (sqlite3.Error, OSError, RuntimeError, ValueError) as error:
                raise LiveError('PERSISTENCE_FAILURE') from error
            if request_count >= 2048:
                raise LiveError('COMMAND_BUDGET_EXHAUSTED')

        if run_id is not None:
            self._run_check(run_id)
        if expected_revision is not None:
            integer(expected_revision, 0, 200000)
            if expected_revision != self._snapshot().context.revision:
                raise LiveError('STATE_CONFLICT')
        identifier(command_id)
        if type(available) is not bool:
            raise LiveError('INVALID_ARGUMENT')
        if self._state not in ('READY', 'RUNNING', 'PAUSED', 'STOPPED'):
            raise LiveError('INVALID_STATE')

        if legacy_request is None:
            effect = build_command_effect(device_id, command, params)
            legacy_effective = None
        else:
            if type(legacy_request) is not tuple or len(legacy_request) != 2:
                raise LiveError('INVALID_ARGUMENT')
            effect, legacy_effective = build_legacy_command_effect(
                device_id, legacy_request[0], legacy_request[1])
        if self.is_device_isolated(device_id):
            raise LiveError('GUARDIAN_RESTRICTED')
        if not available:
            raise LiveError('DEVICE_UNAVAILABLE')

        if durable is not None:
            payload = json.loads(fingerprint)
            try:
                existing = durable.claim_request(
                    request_id=command_id,
                    command_hash=command_fingerprint(payload),
                    run_id=effective_run_id,
                    correlation_id=correlation_id if type(correlation_id) is str else None,
                    scenario_id=scenario_id if type(scenario_id) is str else None,
                    device_id=device_id,
                    command_type=command,
                    payload=payload,
                )
                if existing is not None:
                    try:
                        return durable.duplicate_result(
                            existing, command_fingerprint(payload),
                        )
                    except ValueError as error:
                        raise LiveError(str(error)) from None
            except LiveError:
                raise
            except (sqlite3.Error, OSError, RuntimeError, ValueError) as error:
                raise LiveError('PERSISTENCE_FAILURE') from error

        receipt = self._effect(
            effect,
            persist=durable is None,
            correlation_id=command_id if correlation_id is None else correlation_id,
        )
        result = {
            'command_id': command_id,
            'device_id': device_id,
            'command': command,
            'status': 'APPLIED',
            'requested_effect': effect,
            'effective_effect': effect,
            'execution_status': 'EXECUTED',
            'execution_reason': 'CITYCORE_EFFECT_COMMITTED',
            'city_core_updated': True,
            'correlation_id': receipt.correlation_id,
            'effective_revision': receipt.context.revision,
            'effect_id': receipt.effect_id,
        }
        if legacy_request is not None:
            result['effective'] = legacy_effective
        if durable is not None:
            self._persist_core(request_id=command_id, result=result)
        self._actuator_requests = freeze({
            **self._actuator_requests,
            command_id: {'fingerprint': fingerprint, 'result': result},
        })
        return result

    def _quarantine_key(self, device_id):
        actuator = load_catalog().actuator_row(device_id)
        if actuator is not None and actuator.target_id is not None:
            return f'actuator:{actuator.target_id}'
        return f'device:{device_id}'

    @operator_call
    def quarantine_device(self, device_id, reason='', isolated_by='guardian'):
        identifier(device_id)
        if type(reason) is not str or type(isolated_by) is not str:
            raise LiveError('INVALID_ARGUMENT')
        key = self._quarantine_key(device_id)
        tick = self._snapshot().context.tick
        quarantine = {
            'device_id': device_id,
            'reason': reason or 'Unspecified Guardian quarantine',
            'isolated_by': isolated_by,
            'timestamp': timestamp(self._epoch, tick),
        }
        self._quarantined_devices[key] = quarantine
        if self._persistence is not None:
            try:
                self._persistence.save_quarantine(key, quarantine)
            except (sqlite3.Error, OSError, RuntimeError) as error:
                self._quarantined_devices.pop(key, None)
                raise LiveError('PERSISTENCE_FAILURE') from error
        return plain(quarantine)

    @operator_call
    def restore_quarantined_device(self, device_id):
        identifier(device_id)
        key = self._quarantine_key(device_id)
        quarantine = self._quarantined_devices.pop(key, None)
        if quarantine is None:
            raise LiveError('DEVICE_NOT_FOUND')
        if self._persistence is not None:
            try:
                self._persistence.delete_quarantine(key)
            except (sqlite3.Error, OSError, RuntimeError) as error:
                self._quarantined_devices[key] = quarantine
                raise LiveError('PERSISTENCE_FAILURE') from error
        return plain(quarantine)

    @operator_call
    def is_device_isolated(self, device_id):
        return self._quarantine_key(device_id) in self._quarantined_devices

    @operator_call
    def quarantined_device(self, device_id):
        quarantine = self._quarantined_devices.get(self._quarantine_key(device_id))
        return plain(quarantine) if quarantine is not None else None

    @operator_call
    def quarantine_count(self):
        return len(self._quarantined_devices)

    @operator_call
    def command(self, device_id, requested, *, command_id, effective=None, available=True,
                run_id=None, expected_revision=None):
        identifier(command_id)
        if type(available) is not bool:
            raise LiveError('INVALID_ARGUMENT')
        actual = requested if effective is None else effective
        existing = self._legacy_command_results.get(command_id)
        outcome, error = 'FAILED', 'DEVICE_UNAVAILABLE'
        execution = None
        try:
            execution = self.execute_actuator_command(
                device_id, 'legacy', {},
                command_id=command_id, available=available, run_id=run_id,
                expected_revision=expected_revision,
                legacy_request=(requested, effective),
            )
            actual = execution['effective']
            outcome, error = ('COMPLETED' if actual == requested else 'PARTIAL'), None
        except LiveError as failure:
            if failure.code in ('INVALID_ARGUMENT', 'UNSUPPORTED_COMMAND', 'UNSUPPORTED_DEVICE',
                                'IDEMPOTENCY_CONFLICT', 'OLD_RUN', 'STATE_CONFLICT'):
                raise
            error = failure.code
        if existing is not None:
            return plain(existing)
        self._event('ACTION_REQUESTED', {'command_id': command_id, 'device_id': device_id})
        result_data = {'command_id': command_id, 'device_id': device_id, 'status': outcome,
                       'requested': requested,
                       'effective': actual if available and error is None else None,
                       'error': error, 'run_id': self._run_id}
        if execution is not None:
            result_data['effect_id'] = execution['effect_id']
        result = freeze(result_data)
        self._event('ACTION_FAILED' if outcome == 'FAILED' else 'ACTION_COMPLETED', plain(result))
        if execution is not None and len(self._legacy_command_results) < 1024:
            self._legacy_command_results = freeze({
                **self._legacy_command_results,
                command_id: result,
            })
        return plain(result)


def disturbances(physical):
    rows = []
    if physical.water.rain_mm_h:
        rows.append({'kind': 'rain', 'target': 'CATCH-01', 'rate_mm_h': physical.water.rain_mm_h})
    if not physical.source.source_available:
        rows.append({'kind': 'power_outage', 'target': 'GRID-01'})
    rows.extend({'kind': 'road_obstruction', 'target': road, 'fraction': state.obstruction_fraction}
                for road, state in physical.roads.items() if state.obstruction_fraction)
    rows.extend({'kind': 'pollution', 'target': zone, 'rate_ug_m3_s': state.external_source_ug_m3_s}
                for zone, state in physical.air.items() if state.external_source_ug_m3_s)
    return rows
