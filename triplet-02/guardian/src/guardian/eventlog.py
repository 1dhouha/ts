import json
from datetime import datetime, timezone
from typing import Callable, Optional


class EventLog:
    """One JSON line per event: feeds the shared timeline (R3) and the per-run log file (R2).
    `sink` receives every record (for example the live timeline store)."""

    def __init__(self, path: Optional[str] = None, layer: str = "guardian",
                 sink: Optional[Callable[[dict], None]] = None):
        self.path, self.layer, self.sink = path, layer, sink
        self.events: list[dict] = []

    def emit(self, event: str, ts: Optional[datetime] = None, layer: Optional[str] = None, **fields) -> dict:
        ts = ts or datetime.now(timezone.utc)
        rec = {"timestamp": ts.isoformat(), "layer": layer or self.layer, "event": event, **fields}
        self.events.append(rec)
        if self.path:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec) + "\n")
        if self.sink:
            self.sink(rec)
        return rec
