"""Per-scenario append-only JSONL logger with zero ground-truth leakage guarantee."""
from __future__ import annotations

import json
import os
from pathlib import Path
import threading
from typing import Any

from twin_interface.projections import FORBIDDEN_TRUTH_FIELDS, project_event, project_readings
from twin_observability.seq import AtomicSequencer, create_event


class ScenarioLogger:
    """Append-only, crash-resilient JSONL logger for competition audit trails [Requirement R2].

    Writes one line of valid JSON per event to `logs/{scenario}_{run_id}.jsonl`.
    Flushes and fsyncs every write so no buffered entries are lost on abrupt termination.
    Strictly filters out any private ground-truth fields before writing to disk.
    """

    def __init__(
        self,
        scenario: str,
        run_id: str,
        log_dir: str | Path = "logs",
        sequencer: AtomicSequencer | None = None,
    ) -> None:
        if not scenario or not isinstance(scenario, str):
            raise ValueError("Scenario name must be a non-empty string.")
        if not run_id or not isinstance(run_id, str):
            raise ValueError("run_id must be a non-empty string.")

        self.scenario = scenario
        self.run_id = run_id
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = self.log_dir / f"{self.scenario}_{self.run_id}.jsonl"

        self._sequencer = sequencer if sequencer is not None else AtomicSequencer()
        self._lock = threading.Lock()
        self._file = open(self.log_path, mode="a", encoding="utf-8")
        self._closed = False
        self._line_count = 0

    @property
    def line_count(self) -> int:
        """Total number of JSON lines written to this file."""
        with self._lock:
            return self._line_count

    def _write_record(self, record: dict[str, Any]) -> None:
        """Thread-safely write, flush, and fsync a single JSON record to disk."""
        if self._closed:
            raise RuntimeError("Cannot write to a closed ScenarioLogger.")

        line = json.dumps(record, separators=(",", ":"), ensure_ascii=False) + "\n"
        with self._lock:
            self._file.write(line)
            self._file.flush()
            try:
                os.fsync(self._file.fileno())
            except (OSError, AttributeError):
                # Fallback if filesystem does not support fsync (e.g. some virtual mounts)
                pass
            self._line_count += 1

    def log_event(
        self,
        sim_tick: int,
        event_type: str,
        source: str,
        payload: dict[str, Any],
        timestamp: str | None = None,
    ) -> dict[str, Any]:
        """Record an arbitrary event into the audit trail, ensuring 0 ground truth leaks."""
        seq = self._sequencer.next_seq()
        raw_event = create_event(
            seq=seq,
            sim_tick=sim_tick,
            event_type=event_type,
            source=source,
            payload=payload,
            timestamp=timestamp,
        )
        # Apply anti-leak projection to strip actual_value, delta, fault
        sanitized = project_event(raw_event, role="operator")
        self._write_record(sanitized)
        return sanitized

    def log_start(
        self,
        sim_tick: int = 0,
        config: dict[str, Any] | None = None,
        sim_speed: float = 1.0,
    ) -> dict[str, Any]:
        """Record the RUN_START initialization header."""
        payload = {
            "scenario": self.scenario,
            "run_id": self.run_id,
            "sim_speed": sim_speed,
            "config": config or {},
        }
        return self.log_event(sim_tick=sim_tick, event_type="RUN_START", source="system", payload=payload)

    def log_telemetry_pulse(
        self,
        sim_tick: int,
        readings: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Record a periodic telemetry batch (e.g. 2-second pulse) with public readings only."""
        sanitized_readings = project_readings(readings, role="operator")
        payload = {
            "pulse_tick": sim_tick,
            "sensor_count": len(sanitized_readings),
            "readings": sanitized_readings,
        }
        return self.log_event(sim_tick=sim_tick, event_type="TELEMETRY_PULSE", source="core", payload=payload)

    def log_command(
        self,
        sim_tick: int,
        device_id: str,
        command: str,
        params: dict[str, Any],
        outcome: str,
        request_id: str,
        source: str = "brain",
    ) -> dict[str, Any]:
        """Record an actuator control command and its outcome."""
        payload = {
            "device_id": device_id,
            "command": command,
            "params": params,
            "outcome": outcome,
            "request_id": request_id,
        }
        return self.log_event(sim_tick=sim_tick, event_type="ACTUATOR_COMMAND", source=source, payload=payload)

    def log_quarantine(
        self,
        sim_tick: int,
        device_id: str,
        action: str,
        reason: str,
        source: str = "guardian",
    ) -> dict[str, Any]:
        """Record a device quarantine or restoration action."""
        payload = {
            "device_id": device_id,
            "action": action,  # "ISOLATED" or "RESTORED"
            "reason": reason,
        }
        return self.log_event(sim_tick=sim_tick, event_type="QUARANTINE_ACTION", source=source, payload=payload)

    def log_end(
        self,
        sim_tick: int,
        summary: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Record the RUN_END termination summary."""
        payload = {
            "scenario": self.scenario,
            "run_id": self.run_id,
            "total_ticks": sim_tick,
            "summary": summary or {},
        }
        return self.log_event(sim_tick=sim_tick, event_type="RUN_END", source="system", payload=payload)

    def close(self) -> None:
        """Flush and close the open JSONL file handle."""
        with self._lock:
            if not self._closed:
                self._file.flush()
                self._file.close()
                self._closed = True

    def health(self) -> dict[str, str]:
        """Check that the configured audit file remains open for writes."""
        try:
            with self._lock:
                available = not self._closed and not self._file.closed
        except Exception:
            available = False
        return {"status": "UP" if available else "DOWN"}

    def __enter__(self) -> "ScenarioLogger":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()
