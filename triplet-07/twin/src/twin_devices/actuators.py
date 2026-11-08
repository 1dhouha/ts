"""Honest actuator command lifecycle with effects returned to the caller."""

from __future__ import annotations

from copy import deepcopy
import math
from typing import Any

from .registry import DeviceRegistry


class ActuatorEngine:
    """Dispatch commands and expose their state without changing city_core."""

    _FAULTS = frozenset({"FAIL_TO_START", "JAMMED"})

    def __init__(self, registry: DeviceRegistry) -> None:
        self.registry = registry
        self._faults: dict[str, tuple[str, dict[str, Any]]] = {}
        self._commands: dict[str, dict[str, Any]] = {}

    def inject_fault(
        self, actuator_id: str, fault_type: str, params: dict[str, Any]
    ) -> None:
        device = self.registry.get_device(actuator_id)
        if device.get("kind") != "actuator":
            raise ValueError(f"Device is not an actuator: {actuator_id}")
        if not isinstance(params, dict):
            raise TypeError("fault parameters must be a mapping")
        normalized = fault_type.upper()
        if normalized not in self._FAULTS:
            raise ValueError(f"Unsupported actuator fault: {fault_type}")
        self._faults[actuator_id] = (normalized, dict(params))

    def clear_fault(self, actuator_id: str) -> None:
        self._faults.pop(actuator_id, None)

    def dispatch_command(
        self,
        device_id: str,
        command: str,
        params: dict[str, Any],
        request_id: str,
    ) -> dict[str, Any]:
        if not isinstance(params, dict):
            raise TypeError("command params must be a mapping")
        if not isinstance(command, str) or not command:
            raise ValueError("command must be a non-empty string")
        if not isinstance(request_id, str) or not request_id:
            raise ValueError("request_id must be a non-empty string")
        if request_id in self._commands:
            raise ValueError(f"Duplicate command/request ID: {request_id}")

        try:
            device = self.registry.get_device(device_id)
        except KeyError:
            return self._failed(
                request_id, device_id, command, "DEVICE_UNAVAILABLE"
            )
        if device.get("kind") != "actuator":
            return self._failed(
                request_id, device_id, command, "DEVICE_UNAVAILABLE"
            )
        if device["restricted"]:
            return self._failed(
                request_id, device_id, command, "GUARDIAN_RESTRICTED"
            )
        if not device["available"]:
            return self._failed(
                request_id, device_id, command, "DEVICE_UNAVAILABLE"
            )

        duration = int(params.get("duration_ticks", 1))
        if duration < 1:
            raise ValueError("duration_ticks must be at least 1")
        fault = self._faults.get(device_id)
        jammed_progress: float | None = None
        if fault is not None and fault[0] == "JAMMED":
            raw_progress = fault[1].get(
                "progress_pct", params.get("progress_pct", 0.0)
            )
            jammed_progress = float(raw_progress)
            if not math.isfinite(jammed_progress):
                raise ValueError("JAMMED progress_pct must be finite")

        record: dict[str, Any] = {
            "command_id": request_id,
            "request_id": request_id,
            "device_id": device_id,
            "command": command,
            "params": deepcopy(params),
            "duration_ticks": duration,
            "elapsed_ticks": 0,
            "status": "PENDING",
            "status_history": ["PENDING"],
            "progress_pct": 0.0,
            "error": None,
        }
        self._commands[request_id] = record

        if fault is not None and fault[0] == "FAIL_TO_START":
            self.registry.set_available(device_id, False)
            self._transition(record, "FAILED")
            record["error"] = "DEVICE_UNAVAILABLE"
            return self._public_command(record)
        if fault is not None and fault[0] == "JAMMED":
            assert jammed_progress is not None
            record["progress_pct"] = round(
                max(0.0, min(100.0, jammed_progress)), 2
            )
            self._transition(record, "PARTIAL")
            return self._public_command(record)

        # PENDING is an auditable initial state; accepted commands enter RUNNING.
        self._transition(record, "RUNNING")
        return self._public_command(record)

    def tick_progress(self) -> list[dict[str, Any]]:
        effects: list[dict[str, Any]] = []
        for record in self._commands.values():
            if record["status"] != "RUNNING":
                continue
            record["elapsed_ticks"] += 1
            progress = min(
                100.0,
                100.0 * record["elapsed_ticks"] / record["duration_ticks"],
            )
            record["progress_pct"] = round(progress, 2)
            if record["elapsed_ticks"] >= record["duration_ticks"]:
                self._transition(record, "COMPLETED")
                effects.append(
                    {
                        "command_id": record["command_id"],
                        "device_id": record["device_id"],
                        "command": record["command"],
                        "params": deepcopy(record["params"]),
                    }
                )
        return effects

    def get_status(self, command_id: str) -> dict[str, Any]:
        record = self._commands.get(command_id)
        if record is None:
            raise KeyError(f"Unknown command: {command_id}")
        return self._public_command(record)

    def _failed(
        self, request_id: str, device_id: str, command: str, error: str
    ) -> dict[str, Any]:
        record = {
            "command_id": request_id,
            "request_id": request_id,
            "device_id": device_id,
            "command": command,
            "status": "PENDING",
            "status_history": ["PENDING"],
            "progress_pct": 0.0,
            "error": None,
        }
        self._commands[request_id] = record
        self._transition(record, "FAILED")
        record["error"] = error
        return self._public_command(record)

    @staticmethod
    def _transition(record: dict[str, Any], status: str) -> None:
        record["status"] = status
        record["status_history"].append(status)

    @staticmethod
    def _public_command(record: dict[str, Any]) -> dict[str, Any]:
        return {
            key: deepcopy(value)
            for key, value in record.items()
            if key not in {"params", "duration_ticks", "elapsed_ticks"}
        }
