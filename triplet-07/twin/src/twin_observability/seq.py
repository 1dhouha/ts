"""Thread-safe monotonic sequence generator and structured event models."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import threading
from typing import Any


def utc_now_iso() -> str:
    """Return the current UTC timestamp formatted as ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


class AtomicSequencer:
    """Thread-safe strictly monotonic sequence number generator.

    Guarantees unique, sequentially ascending integer identifiers across concurrent
    simulation threads, FastMCP tasks, and REST request handlers without race conditions.
    """

    def __init__(self, start: int = 1) -> None:
        if start < 0:
            raise ValueError("Initial sequence number cannot be negative.")
        self._lock = threading.Lock()
        self._current: int = start

    def next_seq(self) -> int:
        """Atomically increment and return the next unique sequence number."""
        with self._lock:
            seq = self._current
            self._current += 1
            return seq

    def current_seq(self) -> int:
        """Return the current sequence number without incrementing."""
        with self._lock:
            return self._current

    def reset(self, start: int = 1) -> None:
        """Thread-safely reset the sequence counter to a specified value."""
        if start < 0:
            raise ValueError("Sequence reset value cannot be negative.")
        with self._lock:
            self._current = start


@dataclass(frozen=True)
class EventRecord:
    """Immutable structured record representing an event occurring in the Twin Layer."""

    seq: int
    sim_tick: int
    event_type: str
    source: str
    payload: dict[str, Any]
    timestamp: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        """Convert the event record to a plain JSON-serializable dictionary."""
        return {
            "seq": self.seq,
            "sim_tick": self.sim_tick,
            "timestamp": self.timestamp,
            "event_type": self.event_type,
            "source": self.source,
            "payload": dict(self.payload),
        }


def create_event(
    seq: int,
    sim_tick: int,
    event_type: str,
    source: str,
    payload: dict[str, Any],
    timestamp: str | None = None,
) -> dict[str, Any]:
    """Convenience factory to construct a validated event dictionary."""
    if seq < 0:
        raise ValueError("Sequence number cannot be negative.")
    if sim_tick < 0:
        raise ValueError("Simulation tick cannot be negative.")
    if not event_type:
        raise ValueError("event_type cannot be empty.")
    if not source:
        raise ValueError("source cannot be empty.")

    ts = timestamp if timestamp is not None else utc_now_iso()
    return EventRecord(
        seq=seq,
        sim_tick=sim_tick,
        event_type=event_type,
        source=source,
        payload=payload,
        timestamp=ts,
    ).to_dict()
