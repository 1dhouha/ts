"""FastMCP Tool Server for City Brain Convergence Twin Layer.

Exposes semantic tools for external LLM agents (Brain, Guardian, Evaluator)
over the standard Model Context Protocol (MCP).
All tools pass through the TwinFacade to ensure strict security, permissions,
quarantine checks, and anti-leak projections.
Streamable HTTP callers authenticate with an Authorization: Bearer header;
tool credentials are read from the MCP request context and are not tool inputs.
"""
from typing import Any
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse

from twin_interface.facade import TwinFacade
from twin_interface.health import liveness_report, readiness_http_status


def _request_bearer_token(ctx: Context) -> str | None:
    """Read the caller credential from the current MCP HTTP request."""
    try:
        request = ctx.request_context.request
    except ValueError:
        return None
    if request is None:
        return None
    authorization = request.headers.get("authorization", "")
    scheme, separator, token = authorization.partition(" ")
    if not separator or scheme.lower() != "bearer":
        return None
    return token.strip() or None


def create_mcp_server(
    facade: TwinFacade | None = None,
    *,
    host: str = "127.0.0.1",
    port: int = 8000,
    streamable_http_path: str = "/mcp",
    transport_security: TransportSecuritySettings | None = None,
) -> FastMCP:
    """Create and configure the FastMCP tool server instance bound to the TwinFacade.

    Args:
        facade: Optional initialized TwinFacade instance. If None, a new one is created.

    Returns:
        Configured FastMCP application instance with all tools registered.

    The host, port, HTTP path, and optional transport-security policy are
    explicit configuration points for ASGI mounting and standalone serving.
    """
    fac = facade if facade is not None else TwinFacade()
    mcp = FastMCP(
        "City-Brain-Twin",
        host=host,
        port=port,
        streamable_http_path=streamable_http_path,
        transport_security=transport_security,
    )

    @mcp.custom_route("/health/live", methods=["GET"], include_in_schema=False)
    async def liveness(_request: Request):
        return JSONResponse(liveness_report("mcp"))

    @mcp.custom_route("/health/ready", methods=["GET"], include_in_schema=False)
    async def readiness(_request: Request):
        report = fac.health_report("mcp", service_checks={"mcp_service": True})
        return JSONResponse(
            report,
            status_code=readiness_http_status(report),
        )

    @mcp.tool()
    def get_service_health() -> dict[str, Any]:
        """Report MCP liveness and readiness without changing simulation state.

        Layer guidance: Any layer can call this before starting a workflow or
        after a connection error. Continue only when readiness reports healthy;
        this is service health, not a city-state or sensor query.
        """
        report = fac.health_report("mcp", service_checks={"mcp_service": True})
        return {
            "liveness": liveness_report("mcp"),
            "readiness": report,
        }

    # --------------------------------------------------------------------------
    # 1. OBSERVATION TOOLS (Read Telemetry & State)
    # --------------------------------------------------------------------------

    @mcp.tool()
    def get_sensor_readings(
        ctx: Context,
        zone_id: str | None = None,
        sensor_id: str | None = None,
        sensor_type: str | None = None,
    ) -> dict[str, Any]:
        """Retrieve public sensor telemetry and operational status across the city.

        Returns only public/projected observations. Secret Ground Truth variables
        (actual_value, delta, fault models) are strictly filtered and omitted.

        Args:
            zone_id: Optional zone filter ('PRT', 'DTN', 'IND', 'RES', 'HSP').
                     Omit to retrieve sensors across the entire city.
            sensor_id: Optional filter for a specific equipment ID (e.g. 'PRT-WLV-01').
            sensor_type: Optional filter by measurement type:
                         - 'water_level' (cm)
                         - 'traffic_counter' (veh/min)
                         - 'air_quality' (ug/m3 PM2.5)
                         - 'power_load' (kW)

        Returns:
            Envelope containing sim_tick, count, and array of public sensor readings:
            {
                "ok": true,
                "run_id": "live-001",
                "data": {
                    "sim_tick": 45,
                    "count": 4,
                    "readings": [
                        {
                            "sensor_id": "PRT-WLV-01",
                            "zone_id": "PRT",
                            "type": "water_level",
                            "unit": "cm",
                            "reported_value": 78.5,
                            "status": "OK",
                            "backup_ids": ["PRT-WLV-02"],
                            "timestamp": "2026-10-23T12:00:00Z"
                        }
                    ]
                }
            }

        Layer guidance: Brain and Evaluator should use these readings as the
        measured inputs to decisions and assessments. Guardian can compare a
        sensor with its backup readings to identify suspicious drift. Use
        get_city_state for a concise equipment overview instead.
        """
        return fac.get_sensor_readings(
            token=_request_bearer_token(ctx),
            zone_id=zone_id,
            sensor_id=sensor_id,
            sensor_type=sensor_type,
        )

    @mcp.tool()
    def get_city_state(
        ctx: Context,
        zone_id: str | None = None,
    ) -> dict[str, Any]:
        """Retrieve high-level macro situational awareness of urban infrastructure and alerts.

        Use this tool to get an overarching operational picture before planning actuator dispatches.
        Provides pump operation states, valve positions, traffic light phases, electrical grid status,
        and active incident alerts.

        Args:
            zone_id: Optional zone filter ('PRT', 'DTN', 'IND', 'RES', 'HSP').
                     Omit to receive high-level status for all zones.

        Returns:
            Envelope containing active alerts, simulation speed, and zone equipment breakdowns.

        Layer guidance: Brain should call this before choosing an intervention
        to understand current infrastructure and active alerts. Evaluator can
        use it to assess the resulting city state; Guardian can use it to locate
        affected equipment before isolating a device.
        """
        return fac.get_city_state(token=_request_bearer_token(ctx), zone_id=zone_id)

    @mcp.tool()
    def get_events_since(
        ctx: Context,
        seq: int = 0,
    ) -> dict[str, Any]:
        """Retrieve timeline events that occurred after a given sequence number.

        Use this tool to poll for newly triggered alarms, completed actions, or status changes
        without re-downloading the full sensor catalog.

        Args:
            seq: Sequence number cursor. Only events with event_seq > seq will be returned.

        Returns:
            Envelope containing from_seq, count, and array of chronological event objects.

        Layer guidance: Evaluator and monitoring layers should retain the
        greatest event_seq returned and pass it as seq on the next poll. Brain
        and Guardian can poll for new alerts or command/security outcomes without
        fetching the full state again.
        """
        return fac.get_events_since(token=_request_bearer_token(ctx), seq=seq)

    # --------------------------------------------------------------------------
    # 2. CONTROL TOOLS (Actuator Commands - Brain & Operator Only)
    # --------------------------------------------------------------------------

    @mcp.tool()
    def send_actuator_command(
        device_id: str,
        command: str,
        params: dict[str, Any],
        request_id: str,
        ctx: Context,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Apply a simulated actuator command to the virtual city state.

        Permission: Restricted to Role 'brain' and 'operator'. Guardian calls are rejected.
        Idempotency: Re-transmitting the same request_id and request returns the original
        outcome; reusing it with different command data or execution context is rejected
        with IDEMPOTENCY_CONFLICT.
        Quarantine: If the device is isolated by the Guardian, the command will be rejected
                    with error code GUARDIAN_RESTRICTED.

        Supported equipment & commands:
        - Water Pumps ('PRT-PMP-01', 'PRT-PMP-02', 'P-03'):
            * command='start', params={'speed_pct': 0-100}
            * command='stop', params={}
            * command='set_speed', params={'speed_pct': 0-100}
        - Water Valves ('V-12', 'IND-VLV-01', 'DTN-VLV-01'):
            * command='set_opening', params={'open_pct': 0.0-100.0}
        - Traffic Signals ('DTN-TLT-01', 'DTN-TLT-02'):
            * command='set_mode', params={'mode': 'normal'|'priority'|'stop'}
            * command='set_priority_corridor', params={}
            * command='set_phase', params={'phase': 'all_red'}
              Directional green_ns/green_ew and flashing phases are unsupported:
              city_core models aggregate normal/priority/stop capacity modes only.
        - Electrical Feeder Switches ('F-ESS', 'F-URBAN', 'F-IND', 'GRD'):
            * command='open' (disconnect power), params={}
            * command='close' (energize power), params={}
        - Emergency Dispatch ('AMB-01', 'AMB-02', 'POL-01'):
            * command='dispatch', params={'destination_node_id': 'J-D1', 'route_road_ids': ['R-LOW'], 'mission_ref': 'optional-id'}
            * command='release', params={'mission_ref': 'required-id'}

        Pump speed is simulated as a fraction of rated flow and motor demand.
        There is no physical actuator communication.

        Args:
            device_id: Target equipment ID.
            command: Action verb matching equipment type.
            params: Dictionary of parameters required by the command.
            request_id: Unique client UUID for idempotent execution.
            context: Optional metadata dictionary (e.g. Guardian verdict ID).

        Returns:
            Envelope with command outcome:
            {
                "ok": true,
                "data": {
                    "request_id": "req-123",
                    "device_id": "PRT-PMP-02",
                    "command": "start",
                    "status": "APPLIED",
                    "effective_revision": 16
                }
            }

        Layer guidance: Brain should call this after reviewing state and
        selecting an intervention. Operator may use it for an authorized manual
        action. Use a new request_id for a new action and reuse the same ID only
        when retrying the identical request. Check the returned status and
        effective_revision before assuming the simulated state changed.
        """
        return fac.send_actuator_command(
            token=_request_bearer_token(ctx),
            device_id=device_id,
            command=command,
            params=params,
            request_id=request_id,
            context=context,
        )

    # --------------------------------------------------------------------------
    # 3. SECURITY & QUARANTINE TOOLS (Guardian & Operator Only)
    # --------------------------------------------------------------------------

    @mcp.tool()
    def isolate_device(
        device_id: str,
        ctx: Context,
        reason: str = "",
    ) -> dict[str, Any]:
        """Place a suspected compromised, drifting, or attacked device into quarantine.

        Permission: Restricted to Role 'guardian' and 'operator'. Brain calls are rejected.
        Effect: The device is marked OFFLINE. All future actuator commands sent to this device
                will be rejected with error GUARDIAN_RESTRICTED until it is restored.

        Args:
            device_id: Equipment ID to quarantine (e.g. 'PRT-WLV-01', 'V-12').
            reason: Diagnostic explanation (e.g. 'Sensor drift > 50cm compared to backup').

        Returns:
            Envelope confirming isolation:
            {
                "ok": true,
                "data": {
                    "device_id": "PRT-WLV-01",
                    "status": "ISOLATED",
                    "isolated_by": "guardian",
                    "reason": "..."
                }
            }

        Layer guidance: Guardian should call this when its security analysis
        finds a device compromised or untrustworthy, and include the evidence in
        reason. Operator may also quarantine a device. Brain should treat an
        isolated device as unavailable and select another safe response.
        """
        return fac.isolate_device(
            token=_request_bearer_token(ctx),
            device_id=device_id,
            reason=reason,
        )

    @mcp.tool()
    def restore_device(
        device_id: str,
        ctx: Context,
    ) -> dict[str, Any]:
        """Restore a quarantined device back to active operational status.

        Permission: Restricted to Role 'guardian' and 'operator'.

        Args:
            device_id: Equipment ID to remove from quarantine.

        Returns:
            Envelope confirming restoration:
            {
                "ok": true,
                "data": {
                    "device_id": "PRT-WLV-01",
                    "status": "RESTORED"
                }
            }

        Layer guidance: Guardian or Operator should call this only after the
        device is considered safe to return to service. Brain should then
        refresh city state or sensor readings before relying on the device.
        """
        return fac.restore_device(
            token=_request_bearer_token(ctx),
            device_id=device_id,
        )

    return mcp
