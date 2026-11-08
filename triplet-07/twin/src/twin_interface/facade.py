"""Central Façade Gateway for the Twin Layer.

Acts as the intelligent gatekeeper and unified interface:
- Authenticates incoming tokens and resolves roles.
- Enforces the permission matrix (Tool × Role).
- Enforces device quarantines (GUARDIAN_RESTRICTED).
- Interacts thread-safely with the LiveCity simulation.
- Applies allow-list projections to strip Ground Truth.
- Wraps all responses in standard envelopes.
"""
import threading
from typing import Any

from city_live.runtime import LiveCity
from city_live.commands import preview_command_effect
from city_live.records import LiveError
from twin_interface.auth import Role, resolve_token
from twin_interface.envelope import ErrorCode, error_envelope, success_envelope, utc_now_iso
from twin_interface.health import readiness_report
from twin_interface.permissions import check_tool_permission
from twin_interface.projections import project_event, project_readings
from twin_observability.event_store import EventStore
from twin_observability.scenario_log import ScenarioLogger
from twin_observability.timeline import format_timeline_item


class TwinFacade:
    """The central unified API gateway for both FastMCP and REST transports."""

    def __init__(
        self,
        city: LiveCity | None = None,
        event_store: EventStore | None = None,
        scenario_logger: ScenarioLogger | None = None,
    ):
        """Initialize the Façade with a LiveCity instance and audit sinks."""
        self._city = city if city is not None else LiveCity()
        self._event_store = event_store
        self._scenario_logger = scenario_logger
        self._lock = threading.RLock()
        self._command_responses: dict[str, dict[str, Any]] = {}
        self._city.set_scenario_event_sink(self._record_scenario_event)

    @property
    def city(self) -> LiveCity:
        """Direct access to the underlying LiveCity instance."""
        return self._city

    @property
    def run_id(self) -> str:
        """Current simulation run ID."""
        return self._city.status()["run_id"]

    def health_report(self, service="twin_facade", *, service_checks=None):
        """Report facade and required LiveCity readiness without mutating state."""
        city = getattr(self, "_city", None)
        optional_components = {
            "event_store": getattr(self, "_event_store", None),
            "scenario_logger": getattr(self, "_scenario_logger", None),
        }
        optional_checks = {}
        configured_checks = {}
        for name, component in optional_components.items():
            if component is None:
                optional_checks[name] = "DEGRADED"
                continue
            try:
                component_health = component.health() if component is not None else None
                configured_checks[name] = (
                    type(component_health) is dict
                    and component_health.get("status") == "UP"
                )
            except Exception:
                configured_checks[name] = False
        return readiness_report(
            service,
            city,
            required_checks={
                "facade": city is not None,
                **(service_checks or {}),
                **configured_checks,
            },
            optional_checks=optional_checks,
        )

    @property
    def event_store(self) -> EventStore | None:
        """Observability ring buffer attached to this gateway, if any."""
        return self._event_store

    @property
    def scenario_logger(self) -> ScenarioLogger | None:
        """Append-only JSONL audit logger attached to this gateway, if any."""
        return self._scenario_logger

    def _record_event(self, event_type: str, payload: dict[str, Any], source: str) -> None:
        """Append one public event to the configured audit sinks."""
        sim_tick = self._city.status()["sim_tick"]
        if self._event_store is not None:
            self._event_store.append(sim_tick, event_type, source, payload)
        if self._scenario_logger is not None:
            if event_type == "ACTUATOR_COMMAND":
                self._scenario_logger.log_command(
                    sim_tick=sim_tick,
                    device_id=payload["device_id"],
                    command=payload["command"],
                    params=payload["params"],
                    outcome=payload["outcome"],
                    request_id=payload["request_id"],
                    source=source,
                )
            elif event_type == "QUARANTINE_ACTION":
                self._scenario_logger.log_quarantine(
                    sim_tick=sim_tick,
                    device_id=payload["device_id"],
                    action=payload["action"],
                    reason=payload.get("reason", ""),
                    source=source,
                )
            else:
                self._scenario_logger.log_event(
                    sim_tick=sim_tick,
                    event_type=event_type,
                    source=source,
                    payload=payload,
                )

    def _record_scenario_event(self, details: dict[str, Any]) -> None:
        self._record_event(
            details['event_type'],
            details,
            source=details['source'],
        )

    # --------------------------------------------------------------------------
    # 1. OBSERVATION METHODS (Read Telemetry & State)
    # --------------------------------------------------------------------------

    def get_sensor_readings(
        self,
        token: str | None,
        zone_id: str | None = None,
        sensor_id: str | None = None,
        sensor_type: str | None = None,
    ) -> dict[str, Any]:
        """Retrieve sanitized sensor telemetry.

        Args:
            token: Caller Bearer token string.
            zone_id: Optional filter by zone ('PRT', 'DTN', 'IND', 'RES', 'HSP').
            sensor_id: Optional filter for a specific sensor ID.
            sensor_type: Optional filter by measurement type ('water_level', etc.).

        Returns:
            Standard envelope containing filtered, projected sensor observations.
        """
        role = resolve_token(token)
        ok, err_msg = check_tool_permission("get_sensor_readings", role)
        if not ok:
            return error_envelope(self.run_id, ErrorCode.NOT_AUTHORIZED, err_msg or "Unauthorized")

        with self._city._condition:
            status = self._city.status()
            sim_tick = status["sim_tick"]

            # Pull observations from PublicReader (live simulation data)
            raw_readings = self._city.public.observations()

        # PublicReader retains a bounded history for the dashboard. The tool
        # contract is a current snapshot, so keep only the newest record per
        # sensor instead of returning every retained sample.
        latest_by_sensor: dict[str, dict[str, Any]] = {}
        for reading in raw_readings:
            reading_sensor_id = reading.get("sensor_id")
            if reading_sensor_id is None:
                continue
            current = latest_by_sensor.get(reading_sensor_id)
            if current is None or reading.get("seq", 0) >= current.get("seq", 0):
                latest_by_sensor[reading_sensor_id] = reading

        # Apply filtering
        filtered = [latest_by_sensor[key] for key in sorted(latest_by_sensor)]

        if sensor_id:
            filtered = [r for r in filtered if r.get("sensor_id") == sensor_id]
            if not filtered:
                return error_envelope(
                    self.run_id,
                    ErrorCode.DEVICE_NOT_FOUND,
                    f"Sensor '{sensor_id}' not found.",
                    details={"sensor_id": sensor_id},
                )

        if zone_id:
            filtered = [r for r in filtered if r.get("zone_id") == zone_id]

        if sensor_type:
            filtered = [r for r in filtered if r.get("type") == sensor_type]

        # Apply quarantine status overrides: quarantined sensors report as OFFLINE
        for item in filtered:
            s_id = item.get("sensor_id")
            if self._city.is_device_isolated(s_id):
                item["status"] = "OFFLINE"
                item["available"] = False

        # Apply allow-list projection (censor ground truth for public roles)
        sanitized = project_readings(filtered, role)

        return success_envelope(
            self.run_id,
            {
                "sim_tick": sim_tick,
                "count": len(sanitized),
                "readings": sanitized,
            },
        )

    def get_city_state(
        self,
        token: str | None,
        zone_id: str | None = None,
    ) -> dict[str, Any]:
        """Retrieve high-level macro situational overview of city infrastructure.

        Args:
            token: Caller Bearer token string.
            zone_id: Optional filter by zone ('PRT', 'DTN', 'IND', 'RES', 'HSP').

        Returns:
            Standard envelope containing active alerts and infrastructure state summary.
        """
        role = resolve_token(token)
        ok, err_msg = check_tool_permission("get_city_state", role)
        if not ok:
            return error_envelope(self.run_id, ErrorCode.NOT_AUTHORIZED, err_msg or "Unauthorized")

        with self._city._condition:
            status = self._city.status()
            diag = self._city.diagnostics()
            sim_tick = status["sim_tick"]

        # Synthesize public infrastructure state per zone
        zones_summary = self._build_zones_summary(diag)

        if zone_id:
            if zone_id not in zones_summary:
                return error_envelope(
                    self.run_id,
                    ErrorCode.DEVICE_NOT_FOUND,
                    f"Zone '{zone_id}' not found.",
                    details={"zone_id": zone_id},
                )
            zones_summary = {zone_id: zones_summary[zone_id]}

        # Compute active incidents / alerts
        incidents = self._compute_active_alerts(diag)

        return success_envelope(
            self.run_id,
            {
                "sim_tick": sim_tick,
                "speed": status.get("speed", 1.0),
                "active_incidents": incidents,
                "quarantined_devices_count": self._city.quarantine_count(),
                "zones": zones_summary,
            },
        )

    def get_events_since(
        self,
        token: str | None,
        seq: int = 0,
    ) -> dict[str, Any]:
        """Retrieve timeline events that occurred after a given sequence number.

        Args:
            token: Caller Bearer token string.
            seq: Sequence cursor (returns events with event_seq > seq).

        Returns:
            Standard envelope with sanitized events list.
        """
        role = resolve_token(token)
        ok, err_msg = check_tool_permission("get_events_since", role)
        if not ok:
            return error_envelope(self.run_id, ErrorCode.NOT_AUTHORIZED, err_msg or "Unauthorized")
        if not isinstance(seq, int) or isinstance(seq, bool) or seq < 0:
            return error_envelope(self.run_id, ErrorCode.INVALID_PARAM, "seq must be a non-negative integer.")

        if self._event_store is not None:
            events = self._event_store.get_events_since(seq, limit=100, role=role)
        else:
            with self._city._condition:
                all_events = self._city.events().get("events", [])

            # Filter events after seq and apply the same recursive anti-leak
            # projection used by the observability EventStore.
            new_events = [
                event for event in all_events
                if event.get("event_seq", 0) > seq or event.get("seq", 0) > seq
            ]
            events = [project_event(event, role) for event in new_events]

        return success_envelope(
            self.run_id,
            {
                "from_seq": seq,
                "count": len(events),
                "events": events,
            },
        )

    def get_timeline(
        self,
        token: str | None,
        seq: int = 0,
    ) -> dict[str, Any]:
        """Retrieve sanitized timeline cards for the live dashboard [Requirement R3].

        Uses the same authentication and event projection as ``get_events_since``.
        """
        events_response = self.get_events_since(token=token, seq=seq)
        if not events_response["ok"]:
            return events_response

        cards = [format_timeline_item(event) for event in events_response["data"]["events"]]
        return success_envelope(
            events_response["run_id"],
            {
                "from_seq": seq,
                "count": len(cards),
                "timeline": cards,
            },
        )

    # --------------------------------------------------------------------------
    # 2. CONTROL METHODS (Actuator Commands)
    # --------------------------------------------------------------------------

    def send_actuator_command(
        self,
        token: str | None,
        device_id: str,
        command: str,
        params: dict[str, Any],
        request_id: str,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Dispatch a physical action to an urban actuator (pump, valve, light, switch).

        Args:
            token: Caller Bearer token string.
            device_id: Target equipment ID (e.g. 'PRT-PMP-02', 'V-12', 'DTN-TLT-01').
            command: Action verb ('start', 'stop', 'set_opening', 'set_phase', etc.).
            params: Parameters dictionary (e.g. {'speed_pct': 80}).
            request_id: Unique client UUID for idempotent execution.
            context: Optional client metadata (e.g. Guardian verdict ID).

        Returns:
            Standard envelope with outcome status ('APPLIED', 'REJECTED', etc.).
        """
        role = resolve_token(token)
        ok, err_msg = check_tool_permission("send_actuator_command", role)
        if not ok:
            return error_envelope(self.run_id, ErrorCode.NOT_AUTHORIZED, err_msg or "Unauthorized")

        if not request_id or not isinstance(request_id, str):
            return error_envelope(self.run_id, ErrorCode.INVALID_PARAM, "request_id must be a non-empty string.")

        scenario_correlation = None
        try:
            execution_context = context if type(context) is dict else {}
            with self._city._condition:
                scenario_correlation = self._city.scenario_command_started(
                    device_id, command, request_id, role, execution_context
                )
                self._city.scenario_command_guard(
                    device_id, role, execution_context, request_id=request_id
                )
                result = self._city.execute_actuator_command(
                    device_id,
                    command,
                    params,
                    command_id=request_id,
                    run_id=execution_context.get("run_id", self.run_id),
                    expected_revision=execution_context.get("expected_revision"),
                    correlation_id=scenario_correlation,
                    scenario_id=execution_context.get("scenario_id"),
                )
                self._city.scenario_command_finished(
                    scenario_correlation,
                    device_id,
                    command,
                    request_id,
                    result=result,
                )

            result_data = {
                "request_id": request_id,
                "device_id": device_id,
                "command": command,
                "status": result["status"],
                "effective_revision": result["effective_revision"],
                "effect_id": result["effect_id"],
                "context": context or {},
                "timestamp": utc_now_iso(),
            }

            # The LiveCity receipt is authoritative; this cache only preserves
            # the facade's response projection and avoids duplicate audit events.
            with self._lock:
                cached = self._command_responses.get(request_id)
                if cached is not None:
                    return success_envelope(self.run_id, cached)
                self._command_responses[request_id] = result_data

            self._record_event(
                "ACTUATOR_COMMAND",
                {
                    "request_id": request_id,
                    "device_id": device_id,
                    "command": command,
                    "params": dict(params),
                    "outcome": "APPLIED",
                },
                source=role or "unknown",
            )

            return success_envelope(self.run_id, result_data)

        except LiveError as exc:
            self._city.scenario_command_finished(
                scenario_correlation,
                device_id,
                command,
                request_id,
                error=exc.code,
            )
            code = {
                "UNSUPPORTED_DEVICE": ErrorCode.DEVICE_NOT_FOUND,
                "INVALID_ARGUMENT": ErrorCode.INVALID_PARAM,
                "UNSUPPORTED_COMMAND": ErrorCode.INVALID_PARAM,
                "INVALID_STATE": ErrorCode.INVALID_STATE,
                "STATE_CONFLICT": ErrorCode.STATE_CONFLICT,
                "DEVICE_UNAVAILABLE": ErrorCode.DEVICE_UNAVAILABLE,
                "GUARDIAN_RESTRICTED": ErrorCode.GUARDIAN_RESTRICTED,
                "IDEMPOTENCY_CONFLICT": ErrorCode.IDEMPOTENCY_CONFLICT,
                "COMMAND_BUDGET_EXHAUSTED": ErrorCode.COMMAND_BUDGET_EXHAUSTED,
                "RECOVERY_REQUIRED": ErrorCode.RECOVERY_REQUIRED,
                "PERSISTENCE_FAILURE": ErrorCode.PERSISTENCE_FAILURE,
                "SCENARIO_GUARDIAN_PENDING": ErrorCode.INVALID_STATE,
                "SCENARIO_BRAIN_UNAVAILABLE": ErrorCode.INVALID_STATE,
                "SCENARIO_GUARDIAN_UNAVAILABLE": ErrorCode.INVALID_STATE,
                "SCENARIO_CONTEXT_STALE": ErrorCode.INVALID_STATE,
            }.get(exc.code, ErrorCode.INTERNAL_ERROR)
            return error_envelope(
                self.run_id,
                code,
                (
                    "Device is isolated by the Guardian."
                    if exc.code == "GUARDIAN_RESTRICTED"
                    else "The prior request has an uncertain outcome and requires operator recovery."
                    if exc.code == "RECOVERY_REQUIRED"
                    else "The command was not committed because durable storage is unavailable."
                    if exc.code == "PERSISTENCE_FAILURE"
                    else "Scenario safety is waiting for the Guardian veto check."
                    if exc.code == "SCENARIO_GUARDIAN_PENDING"
                    else "Brain communication is unavailable in this controlled scenario."
                    if exc.code == "SCENARIO_BRAIN_UNAVAILABLE"
                    else "Guardian communication is unavailable in this controlled scenario."
                    if exc.code == "SCENARIO_GUARDIAN_UNAVAILABLE"
                    else f"Simulation rejected command: {exc.code}."
                ),
                details={"device_id": device_id, "command": command},
            )
        except Exception as exc:
            return error_envelope(
                self.run_id,
                ErrorCode.INTERNAL_ERROR,
                f"Simulation failed to apply effect: {str(exc)}",
            )

    # --------------------------------------------------------------------------
    # 3. SECURITY & QUARANTINE METHODS (Guardian Tools)
    # --------------------------------------------------------------------------

    def isolate_device(
        self,
        token: str | None,
        device_id: str,
        reason: str = "",
    ) -> dict[str, Any]:
        """Place a compromised or drifting device into quarantine.

        Args:
            token: Caller Bearer token string (Guardian or Operator required).
            device_id: Target device ID to quarantine.
            reason: Diagnostic explanation for isolation.

        Returns:
            Standard envelope with isolation confirmation.
        """
        role = resolve_token(token)
        ok, err_msg = check_tool_permission("isolate_device", role)
        if not ok:
            return error_envelope(self.run_id, ErrorCode.NOT_AUTHORIZED, err_msg or "Unauthorized")

        if not device_id or not isinstance(device_id, str):
            return error_envelope(self.run_id, ErrorCode.INVALID_PARAM, "device_id must be a valid string.")

        try:
            with self._city._condition:
                self._city.scenario_guardian_call_guard(
                    device_id, 'isolate_device', role
                )
                quarantine = self._city.quarantine_device(device_id, reason, role)
                self._city.scenario_guardian_action(device_id, 'ISOLATED', reason)
        except LiveError as exc:
            if exc.code == 'SCENARIO_GUARDIAN_UNAVAILABLE':
                return error_envelope(
                    self.run_id,
                    ErrorCode.INVALID_STATE,
                    "Guardian communication is unavailable in this controlled scenario.",
                    details={"device_id": device_id, "operation": "isolate_device"},
                )
            return error_envelope(self.run_id, ErrorCode.INVALID_PARAM, exc.code)

        # Log an event in the simulation event ledger and observability sinks
        with self._city._condition:
            self._city._event("DEVICE_ISOLATED", {"device_id": device_id, "reason": reason, "role": role})
        self._record_event(
            "QUARANTINE_ACTION",
            {"device_id": device_id, "action": "ISOLATED", "reason": reason},
            source=role or "unknown",
        )

        return success_envelope(
            self.run_id,
            {
                "device_id": device_id,
                "status": "ISOLATED",
                "isolated_by": role,
                "reason": quarantine["reason"],
            },
        )

    def restore_device(
        self,
        token: str | None,
        device_id: str,
    ) -> dict[str, Any]:
        """Restore a quarantined device to normal operational status.

        Args:
            token: Caller Bearer token string (Guardian or Operator required).
            device_id: Target device ID to restore.

        Returns:
            Standard envelope with restoration confirmation.
        """
        role = resolve_token(token)
        ok, err_msg = check_tool_permission("restore_device", role)
        if not ok:
            return error_envelope(self.run_id, ErrorCode.NOT_AUTHORIZED, err_msg or "Unauthorized")

        try:
            with self._city._condition:
                self._city.scenario_guardian_call_guard(
                    device_id, 'restore_device', role
                )
                self._city.restore_quarantined_device(device_id)
                self._city.scenario_guardian_action(device_id, 'RESTORED')
        except LiveError as exc:
            if exc.code == 'SCENARIO_GUARDIAN_UNAVAILABLE':
                return error_envelope(
                    self.run_id,
                    ErrorCode.INVALID_STATE,
                    "Guardian communication is unavailable in this controlled scenario.",
                    details={"device_id": device_id, "operation": "restore_device"},
                )
            return error_envelope(
                self.run_id,
                ErrorCode.DEVICE_NOT_FOUND,
                f"Device '{device_id}' is not currently isolated.",
            )

        # Log an event in the simulation event ledger and observability sinks
        with self._city._condition:
            self._city._event("DEVICE_RESTORED", {"device_id": device_id, "role": role})
        self._record_event(
            "QUARANTINE_ACTION",
            {"device_id": device_id, "action": "RESTORED", "reason": ""},
            source=role or "unknown",
        )

        return success_envelope(
            self.run_id,
            {
                "device_id": device_id,
                "status": "RESTORED",
                "restored_by": role,
            },
        )

    def is_device_isolated(self, device_id: str) -> bool:
        """Check whether a device is currently quarantined."""
        return self._city.is_device_isolated(device_id)

    # --------------------------------------------------------------------------
    # 4. INTERNAL TRANSLATION & SYNTHESIS HELPERS
    # --------------------------------------------------------------------------

    def _translate_command_to_effect(
        self, device_id: str, command: str, params: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, str | None]:
        """Compatibility projection of the LiveCity-owned command translator."""
        effect, error = preview_command_effect(device_id, command, params)
        return effect, error

    def _build_zones_summary(self, diag: dict[str, Any]) -> dict[str, Any]:
        """Summarize operational equipment state per zone."""
        valve_state = diag.get("physical_state", {}).get("valve", {})
        valve_fraction = valve_state.get("effective_opening_fraction", 0.5)

        return {
            "PRT": {
                "name": "Port / Harbor Zone",
                "pumps": {"PRT-PMP-01": "STOPPED", "PRT-PMP-02": "STANDBY"},
                "valves": {"V-12": f"OPEN_{int(valve_fraction * 100)}%"},
                "flood_status": "NORMAL",
            },
            "DTN": {
                "name": "Downtown Zone",
                "traffic_signals": {"DTN-TLT-01": "GREEN_NS"},
                "power_feeder": {"F-URBAN": "ENERGIZED"},
            },
            "IND": {
                "name": "Industrial Zone",
                "power_feeder": {"F-IND": "ENERGIZED"},
                "air_scrubbers": "ACTIVE",
            },
            "RES": {
                "name": "Residential Zone",
                "water_pressure": "NORMAL",
                "grid_status": "ONLINE",
            },
            "HSP": {
                "name": "Hospital Critical Zone",
                "power_status": "PRIORITY_FEED_OK",
                "ambulance_access": "CLEAR",
            },
        }

    def _compute_active_alerts(self, diag: dict[str, Any]) -> list[str]:
        """Extract active alerts from the physical state."""
        alerts = []
        water = diag.get("physical_state", {}).get("water", {})
        if water.get("surface_depth_m", 0.0) > 0.3:
            alerts.append("HARBOR_SURGE_WARNING")

        for road_id, road_data in diag.get("traffic", {}).items():
            if not road_data.get("usable", True):
                alerts.append(f"ROAD_BLOCKED_{road_id}")

        return alerts
