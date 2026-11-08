"""In-memory bounded ring buffer event store with role-based projections."""
from __future__ import annotations

from collections import deque
import threading
from typing import Any

from twin_interface.projections import project_event
from twin_observability.seq import AtomicSequencer, create_event

DEFAULT_RING_CAPACITY = 10_000


class EventStore:
    """Bounded, thread-safe in-memory ring buffer holding up to `capacity` events.

    Provides O(1) appending, eviction of oldest events when the buffer is full,
    cursor-based querying (`get_events_since`), and role-based projection filtering.
    """

    def __init__(self, capacity: int = DEFAULT_RING_CAPACITY, sequencer: AtomicSequencer | None = None) -> None:
        if capacity <= 0:
            raise ValueError("Capacity must be a positive integer.")
        self._capacity = capacity
        self._lock = threading.RLock()
        self._sequencer = sequencer if sequencer is not None else AtomicSequencer()
        self._events: deque[dict[str, Any]] = deque(maxlen=capacity)
        self._total_recorded = 0
        self._oldest_evicted_seq: int | None = None

    @property
    def capacity(self) -> int:
        """Maximum number of events retained in memory."""
        return self._capacity

    @property
    def total_recorded(self) -> int:
        """Total number of events appended since initialization."""
        with self._lock:
            return self._total_recorded

    def append(
        self,
        sim_tick: int,
        event_type: str,
        source: str,
        payload: dict[str, Any],
        timestamp: str | None = None,
    ) -> dict[str, Any]:
        """Atomically sequence and append a new event into the ring buffer."""
        seq = self._sequencer.next_seq()
        event = create_event(
            seq=seq,
            sim_tick=sim_tick,
            event_type=event_type,
            source=source,
            payload=payload,
            timestamp=timestamp,
        )
        with self._lock:
            if len(self._events) == self._capacity:
                # The oldest event will be evicted
                evicted = self._events[0]
                self._oldest_evicted_seq = evicted.get("seq", 0)
            self._events.append(event)
            self._total_recorded += 1
        return event

    def append_raw(self, event: dict[str, Any]) -> dict[str, Any]:
        """Append an externally formed event dictionary into the ring buffer."""
        if not isinstance(event, dict) or "seq" not in event:
            raise ValueError("Event must be a dictionary containing at least 'seq'.")
        with self._lock:
            if len(self._events) == self._capacity:
                evicted = self._events[0]
                self._oldest_evicted_seq = evicted.get("seq", 0)
            self._events.append(dict(event))
            self._total_recorded += 1
        return event

    def get_events_since(
        self,
        cursor_seq: int = 0,
        limit: int = 100,
        role: str = "operator",
    ) -> list[dict[str, Any]]:
        """Retrieve events with seq > cursor_seq, projected by caller role."""
        if cursor_seq < 0:
            raise ValueError("cursor_seq cannot be negative.")
        if limit <= 0:
            raise ValueError("limit must be greater than zero.")

        with self._lock:
            matched: list[dict[str, Any]] = []
            for ev in self._events:
                if ev.get("seq", 0) > cursor_seq:
                    matched.append(project_event(ev, role))
                    if len(matched) >= limit:
                        break
            return matched

    def get_latest(self, limit: int = 50, role: str = "operator") -> list[dict[str, Any]]:
        """Retrieve the most recent events, up to `limit`, projected by role."""
        if limit <= 0:
            raise ValueError("limit must be greater than zero.")

        with self._lock:
            count = min(limit, len(self._events))
            # Slice the last `count` events
            events_slice = list(self._events)[-count:]
            return [project_event(ev, role) for ev in events_slice]

    def has_history_gap(self, cursor_seq: int) -> bool:
        """Check if the requested cursor_seq is older than the oldest retained event."""
        with self._lock:
            if not self._events:
                return False
            oldest_seq = self._events[0].get("seq", 0)
            return cursor_seq < oldest_seq

    def clear(self) -> None:
        """Clear the ring buffer and reset sequence counters."""
        with self._lock:
            self._events.clear()
            self._total_recorded = 0
            self._oldest_evicted_seq = None
            self._sequencer.reset(1)

    def stats(self) -> dict[str, Any]:
        """Return memory statistics of the ring buffer."""
        with self._lock:
            return {
                "capacity": self._capacity,
                "current_size": len(self._events),
                "total_recorded": self._total_recorded,
                "current_seq": self._sequencer.current_seq(),
                "oldest_retained_seq": self._events[0].get("seq") if self._events else None,
                "newest_retained_seq": self._events[-1].get("seq") if self._events else None,
            }

    def health(self) -> dict[str, str]:
        """Check that the in-memory ring buffer is structurally usable."""
        try:
            with self._lock:
                current_seq = self._sequencer.current_seq()
                available = (
                    isinstance(self._events, deque)
                    and self._capacity > 0
                    and self._events.maxlen == self._capacity
                    and type(current_seq) is int
                    and current_seq >= 1
                )
        except Exception:
            available = False
        return {"status": "UP" if available else "DOWN"}
