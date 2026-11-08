"""Protocol adapter for the Twin team's REST interface (contract twin-interface-0.1).

The Twin only answers requests, so Guardian polls it:  GET /api/v1/sensors  with the Twin-issued `guardian` token
(Authorization header only; the Twin refuses credentials in URLs). Everything the Twin sends is untrusted input:
size-capped, shape-checked, and handed to the format adapter and the engine like any other partner payload.
"""
import re
from typing import Any, Callable, Iterator, Optional

import httpx

from ..models import ID_PATTERN

_ID = re.compile(ID_PATTERN)
OFFLINE_STATUSES = {"OFFLINE", "RESTRICTED", "ISOLATED", "QUARANTINED"}
FAILED_STATUSES = {"FAILED", "DEAD", "FAULT"}


class TwinProtocolError(Exception):
    """The Twin answered, but not in the documented shape."""


class TwinAuthError(TwinProtocolError):
    """The Twin refused our token (401/403)."""


class TwinPoller:
    """Yields one raw reading dict per NEW sample. Duplicate polls of an unchanged sample are skipped (the Twin returns the
    latest value per sensor, and a repeat is not a replay attack). Offline sensors are reported through `on_unavailable`
    and sensors that report FAILED through `on_failed`; neither is judged as a reading."""

    def __init__(self, base_url: str, token: Optional[str], path: str = "/api/v1/sensors", timeout: float = 2.0,
                 client: Optional[httpx.Client] = None, max_bytes: int = 1_000_000,
                 on_unavailable: Optional[Callable[[str, str], None]] = None,
                 on_failed: Optional[Callable[[dict], None]] = None,
                 on_run_change: Optional[Callable[[str, str], None]] = None):
        if not token:
            raise ValueError("a Twin token is required (set the variable named in partners.twin_token_env)")
        self.url = base_url.rstrip("/") + path
        self.token, self.timeout, self.max_bytes = token, timeout, max_bytes
        self.client = client or httpx.Client()
        self.on_unavailable, self.on_failed, self.on_run_change = on_unavailable, on_failed, on_run_change
        self._seen: dict[str, Any] = {}
        self.run_id: Optional[str] = None

    def fetch(self) -> Iterator[dict]:
        r = self.client.get(self.url, headers={"Authorization": f"Bearer {self.token}"}, timeout=self.timeout)
        if r.status_code in (401, 403):
            raise TwinAuthError(f"the Twin refused our token (HTTP {r.status_code})")
        r.raise_for_status()
        if len(r.content) > self.max_bytes:
            raise TwinProtocolError(f"Twin response larger than {self.max_bytes} bytes")
        try:
            body = r.json()
        except ValueError:
            raise TwinProtocolError("Twin response is not JSON")
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(body, dict) or body.get("ok") is not True or not isinstance(data, dict) \
                or not isinstance(data.get("readings"), list):
            raise TwinProtocolError("unexpected envelope: need {ok:true, data:{readings:[...]}}")

        run_id = str(body.get("run_id") or "")[:64]
        if self.run_id is not None and run_id and run_id != self.run_id:     # the Twin restarted: numbering starts over
            self._seen.clear()
            if self.on_run_change:
                self.on_run_change(self.run_id, run_id)
        if run_id:
            self.run_id = run_id
        prefix = (self.run_id or "run")[-12:]

        for row in data["readings"]:
            if not isinstance(row, dict):
                continue
            sid = row.get("sensor_id")
            if not isinstance(sid, str) or not _ID.match(sid):
                continue                                                      # hostile or malformed id: never reaches the engine
            status = str(row.get("status") or "").upper()
            if row.get("available") is False or status in OFFLINE_STATUSES:
                if self.on_unavailable:
                    self.on_unavailable(sid, status or "OFFLINE")
                continue
            marker = row.get("seq", row.get("sim_tick"))
            if status in FAILED_STATUSES or row.get("reported_value") is None:
                if marker is None or self._seen.get(sid) != marker:
                    self._seen[sid] = marker
                    if self.on_failed:
                        self.on_failed({**row, "_obs_id": f"{prefix}-{sid}-{marker}", "_status": status or "FAILED"})
                continue
            if marker is not None:
                if self._seen.get(sid) == marker:
                    continue                                                  # same sample as last poll
                self._seen[sid] = marker
                row = {**row, "observation_id": f"{prefix}-{sid}-{marker}"}
            yield row
