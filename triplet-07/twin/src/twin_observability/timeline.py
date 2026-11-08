"""Live event timeline formatter and streaming utilities [Requirement R3]."""
from __future__ import annotations

import json
from typing import Any


def format_timeline_item(event: dict[str, Any]) -> dict[str, Any]:
    """Transform a raw or sanitized event into a visual presentation card for the dashboard.

    Assigns human-readable titles, UI badges, and alert levels (INFO, WARNING, CRITICAL, ACTION).
    """
    event_type = event.get("event_type") or event.get("type") or "UNKNOWN"
    source = event.get("source", "system")
    payload = event.get("payload") or event.get("details") or {}
    if not isinstance(payload, dict):
        payload = {"value": payload}
    sim_tick = event.get("sim_tick", 0)
    seq = event.get("seq", 0)
    timestamp = event.get("timestamp", "")

    badge = "INFO"
    level = "INFO"
    title = event_type
    summary = ""

    if event_type == "RUN_START":
        badge = "SYSTEM"
        level = "INFO"
        title = "Simulation Started"
        summary = f"Scenario '{payload.get('scenario')}' initialized (Run ID: {payload.get('run_id')})."

    elif event_type == "RUN_END":
        badge = "SYSTEM"
        level = "INFO"
        title = "Simulation Completed"
        summary = f"Scenario concluded at tick {payload.get('total_ticks')}."

    elif event_type == "TELEMETRY_PULSE":
        badge = "TELEMETRY"
        level = "INFO"
        title = "Telemetry Heartbeat"
        count = payload.get("sensor_count", len(payload.get("readings", [])))
        summary = f"Sample pulse delivered: {count} active sensors reporting at tick {sim_tick}."

    elif event_type == "ACTUATOR_COMMAND":
        badge = "ACTION"
        level = "INFO"
        device = payload.get("device_id", "unknown")
        cmd = payload.get("command", "unknown")
        outcome = payload.get("outcome", "APPLIED")
        title = f"Actuator {cmd.upper()}"
        summary = f"Brain dispatched command '{cmd}' to {device} (Outcome: {outcome})."

    elif event_type == "QUARANTINE_ACTION":
        action = payload.get("action", "ISOLATED")
        device = payload.get("device_id", "unknown")
        reason = payload.get("reason", "UNSPECIFIED")
        if action == "ISOLATED":
            badge = "QUARANTINE"
            level = "CRITICAL"
            title = "Device Quarantined"
            summary = f"Guardian isolated suspicious device {device} (Reason: {reason})."
        else:
            badge = "RESTORED"
            level = "INFO"
            title = "Device Restored"
            summary = f"Guardian restored device {device} back to normal operation."

    elif event_type == "ALERT":
        badge = "ALERT"
        level = payload.get("level", "WARNING")
        title = payload.get("title", "System Alert")
        summary = payload.get("description", "An anomaly threshold was reached.")

    else:
        summary = f"Event from {source}: {json.dumps(payload, separators=(',', ':'))}"

    return {
        "seq": seq,
        "sim_tick": sim_tick,
        "timestamp": timestamp,
        "source": source,
        "badge": badge,
        "level": level,
        "title": title,
        "summary": summary,
        "raw_event": event,
    }


def format_sse_message(event_data: dict[str, Any], event_name: str = "timeline_event") -> str:
    """Format a timeline dictionary as a standard Server-Sent Events (SSE) packet."""
    json_payload = json.dumps(event_data, separators=(",", ":"), ensure_ascii=False)
    return f"event: {event_name}\ndata: {json_payload}\n\n"
