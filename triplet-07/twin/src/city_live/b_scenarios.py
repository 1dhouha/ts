"""Translate Person B scenario JSON into LiveCity actions.

B's standalone runner is deliberately not imported: its fixture logic is kept
as scenario data, and the effects are applied to the real city core here.
"""
from dataclasses import dataclass
from functools import lru_cache
import json
from pathlib import Path
import re
import uuid

from .records import LiveError, identifier, timestamp

SCENARIO_DIR = Path(__file__).resolve().parents[2] / "scenarios"


@dataclass(frozen=True, slots=True)
class Trigger:
    sim_tick: int
    action: str
    target: str
    fault_type: str | None
    parameters: dict
    reason: str
    after_scenario: bool
    scenario_local_fixture: bool


@dataclass(frozen=True, slots=True)
class Scenario:
    scenario_id: str
    name: str
    duration_ticks: int
    triggers: tuple
    fixture: dict
    description: str = ''


def _aliases(scenario_id, path):
    values = {scenario_id.lower(), scenario_id.lower().replace("_", "-"),
              path.stem.lower(), path.stem.lower().replace("_", "-")}
    match = re.match(r"scn[_-]?0*(\d+)", path.stem.lower())
    if match:
        number = match.group(1)
        values.update({f"scn-{number}", f"scn_{number}", f"scn-0{number}",
                       f"scn_0{number}"})
    if scenario_id.upper() == "T5":
        values.add("t5")
    return values


@lru_cache(maxsize=1)
def load_scenarios():
    result = {}
    for path in sorted(SCENARIO_DIR.glob("scn_*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        triggers = tuple(
            Trigger(
                int(row["sim_tick"]), row["action"], row["target_device_id"],
                row.get("fault_type"), dict(row.get("parameters", {})),
                row.get("reason", ""), bool(row.get("after_scenario", False)),
                bool(row.get("scenario_local_fixture", False)),
            )
            for row in data["triggers"]
        )
        scenario = Scenario(data["ID"], data["Name"],
                            int(data.get("duration_ticks", 1)), triggers,
                            dict(data.get("fixture", {})), data.get("Description", ""))
        for alias in _aliases(scenario.scenario_id, path):
            result[alias] = scenario
    return result


def scenario_catalog():
    unique = {item.scenario_id: item for item in load_scenarios().values()}
    return [
        {
            'scenario_id': item.scenario_id,
            'name': item.name,
            'description': item.description,
            'duration_ticks': item.duration_ticks,
            'trigger_count': len(item.triggers),
        }
        for item in sorted(unique.values(), key=lambda row: row.scenario_id)
    ]


def scenario(name):
    found = load_scenarios().get(str(name).lower().replace("_", "-"))
    if found is None:
        raise LiveError("UNSUPPORTED_SCENARIO")
    return found


def _fault_params(kind, params):
    if kind == "FALSE_READING":
        if "fixed_value" in params:
            return {"value": params["fixed_value"]}
        if "offset" in params:
            return {"offset": params["offset"]}
    if kind == "DRIFT" and "rate_per_tick" in params:
        return {"rate": params["rate_per_tick"]}
    if kind == "NOISE" and "amplitude" in params:
        return {"amplitude": params["amplitude"]}
    if kind == "DELAYED" and "lag_ticks" in params:
        return {"lag_ticks": params["lag_ticks"]}
    if kind == "DROPOUT" and "duration_ticks" in params:
        return {"duration_ticks": params["duration_ticks"]}
    return dict(params)


class ScenarioRunner:
    def __init__(self, city, event_sink=None):
        self.city = city
        self.event_sink = event_sink
        self.reset()

    def reset(self):
        self.scenario = None
        self.start_tick = None
        self.processed = set()
        self.done = False
        self.correlation_id = None
        self.guardian_isolated = set()
        self.untrusted_sensors = set()
        self.reported_readings = set()
        self.completed_commands = set()
        self.duplicate_commands = set()
        self.sensor_event_ids = {}
        self.command_origins = {}
        self.partner_channels = {}

    def start(self, name):
        if self.scenario is not None and not self.done:
            raise LiveError('SCENARIO_ACTIVE')
        selected = scenario(name)
        faulted = {
            trigger.target for trigger in selected.triggers
            if trigger.action == 'INJECT_FAULT' and not trigger.scenario_local_fixture
        }.intersection(self.city._sensors.faults)
        if faulted:
            raise LiveError('FAULT_ALREADY_ACTIVE')
        self.scenario = selected
        self.start_tick = self.city._snapshot().context.tick
        self.processed = set()
        self.done = False
        self.correlation_id = (
            f"{self.city._run_id}:{self.scenario.scenario_id}:"
            f"{uuid.uuid4()}"
        )
        self.guardian_isolated = set()
        self.untrusted_sensors = set()
        self.reported_readings = set()
        self.completed_commands = set()
        self.duplicate_commands = set()
        self.sensor_event_ids = {}
        self.command_origins = {}
        self.partner_channels = (
            {'brain': True, 'guardian': True}
            if self.scenario.scenario_id == 'SCN-06' else {}
        )
        try:
            self._setup()
        except Exception:
            self.city._sensors.clear_scenario_inputs(self.scenario.scenario_id)
            self._emit(
                'FAILED',
                payload={'reason': 'scenario setup failed'},
            )
            self.reset()
            raise
        self._emit(
            'RECEIVED',
            source='scenario_trigger',
            payload={
                'name': self.scenario.name,
                'partner_mode': 'existing inbound MCP/REST contracts',
                'guardian_policy': 'veto_only',
            },
        )
        for partner in sorted(self.partner_channels):
            self._emit(
                'PARTNER_CHANNEL_AVAILABLE',
                source='scenario_failure_injection',
                payload={
                    'partner': partner,
                    'direction': 'partner_to_twin_inbound',
                    'availability': 'AVAILABLE',
                    'mode': 'controlled_scenario_baseline',
                    'external_connection_probed': False,
                },
            )
        return self.scenario

    def status(self):
        if self.scenario is None:
            return None
        return {
            'scenario_id': self.scenario.scenario_id,
            'name': self.scenario.name,
            'correlation_id': self.correlation_id,
            'start_tick': self.start_tick,
            'status': 'WINDOW_COMPLETE' if self.done else 'RUNNING',
            'guardian_policy': 'veto_only',
        }

    def _setup(self):
        s = self.scenario
        sensors = self.city._sensors
        if s.scenario_id == "T5":
            sensor_id = s.fixture["sensor_id"]
            sensors.set_scenario_input("T5", sensor_id, s.fixture["ground_truth_value"])
            sensors.force_sample(sensor_id, self.start_tick + 1)
        elif s.scenario_id == "SCN-03":
            sensors.set_scenario_input(
                s.scenario_id, "DTN-TRF-01", s.fixture["traffic_before_fault"]
            )
            sensors.set_scenario_input(
                s.scenario_id, "IND-TMP-01", s.fixture["dead_sensor_actual"]
            )
            sensors.set_scenario_input(
                s.scenario_id, "IND-TMP-02", s.fixture["backup_actual"]
            )
            sensors.force_sample("DTN-TRF-01", self.start_tick + 1)
            sensors.force_sample("IND-TMP-01", self.start_tick + 1)
            sensors.force_sample("IND-TMP-02", self.start_tick + 1)

    def advance(self, tick):
        if self.scenario is None or self.done:
            return
        for index, trigger in enumerate(self.scenario.triggers):
            if index in self.processed or trigger.after_scenario:
                continue
            absolute = self.start_tick + trigger.sim_tick
            if absolute != tick:
                continue
            self.processed.add(index)
            if trigger.action == "SET_GROUND_TRUTH":
                value = trigger.parameters.get("value", trigger.parameters.get("to_cm"))
                if value is not None:
                    self.city._sensors.set_scenario_input(
                        self.scenario.scenario_id, trigger.target, value
                    )
                    self.city._sensors.force_sample(trigger.target, tick)
                    self._emit(
                        'SENSOR_EVENT_SCHEDULED',
                        device_id=trigger.target,
                        source='scenario_seed',
                        tick=tick,
                        payload={
                            'unit': self.city._sensors.sensor(trigger.target).unit,
                            'scenario_seeded': True,
                        },
                    )
            elif trigger.action == "INJECT_FAULT":
                if trigger.scenario_local_fixture:
                    self._emit_fixture_reading(trigger, tick)
                    continue
                if (
                    self.scenario.scenario_id == 'SCN-03'
                    and trigger.target == self.scenario.fixture.get('traffic_sensor_id')
                ):
                    self.city._sensors.set_scenario_input(
                        self.scenario.scenario_id,
                        trigger.target,
                        self.scenario.fixture['traffic_actual_during_fault'],
                    )
                try:
                    self.city._sensors.inject(
                        trigger.target,
                        trigger.fault_type,
                        _fault_params(trigger.fault_type, trigger.parameters),
                        tick,
                    )
                except LiveError as error:
                    if error.code != 'FAULT_ALREADY_ACTIVE':
                        raise
                self.city._sensors.force_sample(trigger.target, tick)
                self._emit(
                    'TWIN_UPDATED',
                    device_id=trigger.target,
                    source='scenario_trigger',
                    tick=tick,
                    payload={
                        'fault_type': trigger.fault_type,
                        'scenario_local_fixture': False,
                    },
                )
            elif trigger.action == "CLEAR_FAULT":
                self.city._sensors.clear(trigger.target, repair=True)
                self._emit(
                    'TWIN_UPDATED',
                    device_id=trigger.target,
                    source='scenario_trigger',
                    tick=tick,
                    payload={'fault_cleared': True},
                )
            elif trigger.action == "QUARANTINE":
                self._emit(
                    'GUARDIAN_PENDING',
                    device_id=trigger.target,
                    source='twin',
                    tick=tick,
                    payload={
                        'reason': trigger.reason,
                        'required_action': 'isolate_device',
                        'partner_status': 'awaiting_real_guardian_call',
                    },
                )
            elif trigger.action in ("PARTNER_UNAVAILABLE", "PARTNER_RECOVERED"):
                partner = trigger.target.lower()
                if partner not in self.partner_channels:
                    raise LiveError('UNSUPPORTED_OPERATION')
                available = trigger.action == "PARTNER_RECOVERED"
                if self.partner_channels[partner] == available:
                    continue
                self.partner_channels[partner] = available
                self._emit(
                    'PARTNER_CHANNEL_RECOVERED' if available
                    else 'PARTNER_CHANNEL_UNAVAILABLE',
                    source='scenario_failure_injection',
                    tick=tick,
                    payload={
                        'partner': partner,
                        'direction': 'partner_to_twin_inbound',
                        'availability': 'AVAILABLE' if available else 'UNAVAILABLE',
                        'mode': 'controlled_scenario_injection',
                        'external_connection_probed': False,
                    },
                )
            else:
                raise LiveError("UNSUPPORTED_OPERATION")

    def finish(self, tick):
        if self.scenario is None or self.done:
            return
        last_trigger = max(trigger.sim_tick for trigger in self.scenario.triggers)
        end = self.start_tick + last_trigger + self.scenario.duration_ticks
        if tick < end:
            return
        self.done = True
        for index, trigger in enumerate(self.scenario.triggers):
            if index in self.processed or not trigger.after_scenario:
                continue
            self.processed.add(index)
            if trigger.action == "CLEAR_FAULT":
                self.city._sensors.clear(trigger.target, repair=True)
        for trigger in self.scenario.triggers:
            if (
                trigger.action == "INJECT_FAULT"
                and not trigger.scenario_local_fixture
                and trigger.target in self.city._sensors.faults
            ):
                self.city._sensors.clear(trigger.target, repair=True)
        self.city._sensors.clear_scenario_inputs(self.scenario.scenario_id)
        self.done = True
        self._emit(
            'SCENARIO_WINDOW_COMPLETE',
            source='twin',
            tick=tick,
            payload={
                'partner_decisions_confirmed': False,
                'note': 'Window complete does not imply Brain or Guardian completion.',
            },
        )

    def observe(self, readings, tick):
        if self.scenario is None:
            return
        relevant = {
            trigger.target for trigger in self.scenario.triggers
            if not trigger.scenario_local_fixture
        }
        relevant.update(
            sensor_id for key, sensor_id in (
                ('sensor_id', self.scenario.fixture.get('sensor_id')),
                ('traffic_sensor_id', self.scenario.fixture.get('traffic_sensor_id')),
                ('dead_sensor_id', self.scenario.fixture.get('dead_sensor_id')),
                ('backup_sensor_id', self.scenario.fixture.get('backup_sensor_id')),
            ) if sensor_id
        )
        for reading in readings:
            sensor_id = reading['sensor_id']
            if sensor_id not in relevant:
                continue
            key = (sensor_id, reading['delivery_tick'])
            if key in self.reported_readings:
                continue
            self.reported_readings.add(key)
            public = {
                name: reading[name] for name in (
                    'sensor_id', 'zone_id', 'type', 'unit', 'sim_tick',
                    'delivery_tick', 'reported_value', 'status', 'available',
                    'backup_ids', 'linked_device_ids',
                )
            }
            sensor_event = self._emit(
                'TWIN_UPDATED',
                device_id=sensor_id,
                tick=tick,
                payload={'reading': public, 'input_origin': 'Twin SensorBank'},
            )
            if sensor_event is not None:
                self.sensor_event_ids[sensor_id] = sensor_event['event_id']
            self._emit(
                'GUARDIAN_PENDING',
                device_id=sensor_id,
                tick=tick,
                payload={
                    'reason': 'sensor event is available for Guardian review',
                    'partner_status': 'awaiting_real_guardian_call',
                    'positive_approval_supported': False,
                },
            )
            self._emit(
                'BRAIN_PENDING',
                device_id=sensor_id,
                tick=tick,
                payload={
                    'reason': 'Twin observation is available for Brain review',
                    'partner_status': 'awaiting_real_brain_command',
                },
            )
            if (
                reading.get('reported_value') is None
                or reading.get('actual_value') != reading.get('reported_value')
                or reading.get('status') not in ('OK', 'DEGRADED')
            ):
                self.untrusted_sensors.add(sensor_id)

    def guardian_action(self, device_id, action, reason=''):
        if self.scenario is None:
            return None
        if action == 'ISOLATED':
            self.guardian_isolated.add(device_id)
        else:
            self.guardian_isolated.discard(device_id)
        event_type = 'GUARDIAN_REJECTED' if action == 'ISOLATED' else 'GUARDIAN_REVIEW'
        return self._emit(
            event_type,
            device_id=device_id,
            source='guardian',
            payload={
                'action': action,
                'reason': reason,
                'service_response': 'real_inbound_guardian_call',
                'approval_semantics': 'veto_only',
            },
        )

    def partner_call_guard(self, partner, device_id, operation, request_id=None):
        if self.scenario is None or self.scenario.scenario_id != 'SCN-06':
            return
        if self.partner_channels.get(partner, True):
            return
        self._emit(
            'PARTNER_REQUEST_BLOCKED',
            device_id=device_id,
            source='twin',
            payload={
                'partner': partner,
                'operation': operation,
                'request_id': request_id,
                'reason': 'controlled partner channel is unavailable',
                'queued': False,
            },
        )
        raise LiveError(f'SCENARIO_{partner.upper()}_UNAVAILABLE')

    def command_started(self, device_id, command, request_id, role, context):
        scenario = self.scenario
        supplied = context if type(context) is dict else {}
        has_scenario_context = (
            'scenario_id' in supplied or 'scenario_correlation_id' in supplied
        )
        if has_scenario_context:
            requested_scenario_id = supplied.get('scenario_id')
            requested_scenario_correlation = supplied.get(
                'scenario_correlation_id',
                supplied.get('correlation_id') if 'scenario_id' in supplied else None,
            )
            if (
                scenario is None
                or requested_scenario_id not in (None, scenario.scenario_id)
                or requested_scenario_correlation not in (None, self.correlation_id)
            ):
                raise LiveError('SCENARIO_CONTEXT_STALE')
        if scenario is None:
            return None
        related_devices = {
            value for key, value in scenario.fixture.items()
            if key in ('actuator_id', 'backup_actuator_id') and isinstance(value, str)
        }
        sensor_ids = {
            value for key, value in scenario.fixture.items()
            if key.endswith('_sensor_id') and isinstance(value, str)
        }
        sensor_ids.update(
            trigger.target for trigger in scenario.triggers
            if trigger.action in ('SET_GROUND_TRUTH', 'INJECT_FAULT')
            and not trigger.scenario_local_fixture
        )
        for sensor_id in sensor_ids:
            try:
                related_devices.update(
                    self.city._sensors.sensor(sensor_id).linked_device_ids
                )
            except LiveError:
                continue
        if device_id not in related_devices:
            return None
        correlation_id = self.correlation_id
        if role != 'brain':
            return None
        self.partner_call_guard('brain', device_id, command, request_id)
        if request_id in self.completed_commands:
            self.duplicate_commands.add(request_id)
            return correlation_id
        origin_event_ids = tuple(sorted(
            event_id for sensor_id, event_id in self.sensor_event_ids.items()
            if device_id in self.city._sensors.latest.get(sensor_id, {}).get(
                'linked_device_ids', ()
            )
        ))
        self.command_origins[request_id] = origin_event_ids
        self._emit(
            'BRAIN_DECIDED',
            device_id=device_id,
            source='brain',
            correlation_id=correlation_id,
            payload={
                'command': command,
                'request_id': request_id,
                'service_response': 'real_inbound_brain_command',
                'origin_event_ids': origin_event_ids,
            },
        )
        self._emit(
            'COMMAND_PENDING',
            device_id=device_id,
            source='brain',
            correlation_id=correlation_id,
            payload={'command': command, 'request_id': request_id},
        )
        return correlation_id

    def command_finished(self, correlation_id, device_id, command, request_id, result=None, error=None):
        if correlation_id is None:
            return
        duplicate = request_id in self.duplicate_commands
        if duplicate and error is None:
            self.duplicate_commands.discard(request_id)
            return
        if duplicate:
            self.duplicate_commands.discard(request_id)
        origin_event_ids = self.command_origins.pop(request_id, ())
        if error is not None:
            self._emit(
                'COMMAND_REJECTED',
                device_id=device_id,
                source='twin',
                correlation_id=correlation_id,
                payload={
                    'command': command,
                    'request_id': request_id,
                    'error': error,
                    'reason': 'TwinFacade validation or safety check rejected command',
                    'origin_event_ids': origin_event_ids,
                },
            )
            return
        self._emit(
            'COMMAND_EXECUTED',
            device_id=device_id,
            source='twin',
            correlation_id=correlation_id,
            payload={
                'command': command,
                'request_id': request_id,
                'effective_revision': result['effective_revision'],
                'effect_id': result['effect_id'],
                'city_core_updated': True,
                'origin_event_ids': origin_event_ids,
            },
        )
        self._emit(
            'COMPLETED',
            device_id=device_id,
            source='twin',
            correlation_id=correlation_id,
            payload={'request_id': request_id},
        )
        self.completed_commands.add(request_id)

    def command_guard(self, device_id, role, context=None, request_id=None):
        if self.scenario is None:
            return
        if (
            self.scenario.scenario_id == 'SCN-06'
            and role in ('brain', 'operator')
            and not self.partner_channels.get('guardian', True)
        ):
            self._emit(
                'ACTUATOR_AUTHORIZATION_BLOCKED',
                device_id=device_id,
                source='twin',
                payload={
                    'request_id': request_id,
                    'caller_role': role,
                    'reason': 'Guardian channel unavailable; authorization fails closed',
                    'queued': False,
                },
            )
            raise LiveError('SCENARIO_GUARDIAN_UNAVAILABLE')
        if role != 'brain':
            return
        if request_id in self.duplicate_commands:
            return
        if self.scenario.scenario_id == 'SCN-04':
            target = self.scenario.fixture.get('actuator_id')
            if device_id == target and device_id not in self.guardian_isolated:
                raise LiveError('SCENARIO_GUARDIAN_PENDING')
        for sensor_id in self.untrusted_sensors:
            reading = self.city._sensors.latest.get(sensor_id)
            if reading is None or device_id not in reading.get('linked_device_ids', ()):
                continue
            supplied = context if type(context) is dict else {}
            backup_id = supplied.get('source_sensor_id')
            backup_reading = self.city._sensors.latest.get(backup_id)
            valid_backup = (
                backup_id in reading.get('backup_ids', ())
                and backup_reading is not None
                and backup_reading.get('status') in ('OK', 'DEGRADED')
                and backup_reading.get('reported_value') is not None
                and backup_reading.get('reported_value')
                == backup_reading.get('actual_value')
                and device_id in backup_reading.get('linked_device_ids', ())
            )
            if not valid_backup:
                raise LiveError('SCENARIO_GUARDIAN_PENDING')

    def guardian_call_guard(self, device_id, operation, role):
        if role == 'guardian' or (role == 'operator' and operation == 'restore_device'):
            self.partner_call_guard('guardian', device_id, operation)

    def _emit(self, event_type, *, device_id=None, source='twin', payload=None,
              tick=None, correlation_id=None):
        if self.scenario is None:
            return None
        now_tick = self.city._snapshot().context.tick if tick is None else tick
        details = {
            'event_id': f'event-{uuid.uuid4()}',
            'correlation_id': correlation_id or self.correlation_id,
            'scenario_id': self.scenario.scenario_id,
            'device_id': device_id,
            'timestamp': timestamp(self.city._epoch, now_tick),
            'event_type': event_type,
            'source': source,
            'payload': payload or {},
        }
        if hasattr(self.city, '_scenario_event'):
            self.city._scenario_event(details)
        else:
            self.city._event('SCENARIO_LIFECYCLE', details)
        if self.event_sink is not None:
            self.event_sink(details)
        return details

    def _emit_fixture_reading(self, trigger, tick):
        fixture = self.scenario.fixture
        if trigger.target != fixture.get('air_sensor_id'):
            return
        self._emit(
            'TWIN_UPDATED',
            device_id=trigger.target,
            source='scenario_fixture',
            tick=tick,
            payload={
                'scenario_local_fixture': True,
                'zone_id': 'RES',
                'type': 'air_quality',
                'unit': 'ug/m3',
                'actual_value': fixture['air_actual'],
                'reported_value': fixture['air_reported'],
                'status': 'OK',
            },
        )
        self._emit(
            'GUARDIAN_PENDING',
            device_id=trigger.target,
            tick=tick,
            payload={
                'reason': 'scenario-local fixture is available for review',
                'partner_status': 'awaiting_real_guardian_call',
                'positive_approval_supported': False,
            },
        )
