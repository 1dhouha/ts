"""Containment goes through the Twin's tools. Swap LoggingTwinClient for a real MCP/HTTP client."""
from typing import Protocol


class TwinClient(Protocol):
    def quarantine_device(self, device_id: str, reason: str) -> None: ...
    def isolate_sensor(self, device_id: str, reason: str) -> None: ...
    def rollback_reading(self, observation_id: str) -> None: ...
    def restore_device(self, device_id: str) -> None: ...


class LoggingTwinClient:
    """Stand-in used by the mock Twin and tests: records calls instead of making them."""

    def __init__(self):
        self.calls: list[tuple[str, str, str]] = []

    def quarantine_device(self, device_id, reason):
        self.calls.append(("quarantine_device", device_id, reason))

    def isolate_sensor(self, device_id, reason):
        self.calls.append(("isolate_sensor", device_id, reason))

    def restore_device(self, device_id):
        self.calls.append(("restore_device", device_id, ""))

    def rollback_reading(self, observation_id):
        self.calls.append(("rollback_reading", observation_id, ""))


class HttpTwinClient:
    """Containment through the Twin's tools over HTTP/JSON.
    Never raises into the pipeline: a failed call is logged and queued, and retried by flush_pending().
    The verdict stays UNTRUSTED / QUARANTINE either way, so City Brain is protected even if the Twin is down."""

    def __init__(self, base_url, token=None, paths=None, timeout=2.0, retries=1, client=None, eventlog=None,
                 max_pending=200):
        import httpx
        self._httpx = httpx
        self.base_url = base_url.rstrip("/")
        self.token, self.timeout, self.retries = token, timeout, retries
        self.paths = paths or {}
        self.client = client or httpx.Client()
        self.log = eventlog
        self.pending: list[tuple[str, dict]] = []
        self.max_pending = max_pending

    @classmethod
    def from_config(cls, cfg: dict, eventlog=None, client=None):
        import os
        c = cfg.get("containment", {})
        return cls(cfg["partners"]["twin_url"], token=os.getenv(c.get("token_env", ""), "") or None,
                   paths=c.get("paths", {}), timeout=c.get("timeout_s", 2.0), retries=c.get("retries", 1),
                   client=client, eventlog=eventlog,
                   max_pending=cfg.get("security", {}).get("max_pending_containment", 200))

    def _post(self, tool: str, payload: dict) -> bool:
        url = self.base_url + self.paths.get(tool, f"/tools/{tool}")
        if "reason" in payload:
            payload = {**payload, "reason": str(payload["reason"])[:200]}      # codes only, never unbounded text
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        for _ in range(self.retries + 1):
            try:
                r = self.client.post(url, json=payload, headers=headers, timeout=self.timeout)
                if r.status_code < 400:
                    return True
                if r.status_code < 500:        # a 4xx will not fix itself: do not retry
                    return False
            except self._httpx.HTTPError:
                pass
        return False

    def _call(self, tool: str, payload: dict) -> bool:
        if self.paths.get(tool, "x") in (None, ""):        # the partner has no such tool (the Twin has no rollback): say so, never queue
            if self.log:
                self.log.emit("containment_unsupported", tool=tool, **payload)
            return False
        ok = self._post(tool, payload)
        if not ok:
            if len(self.pending) >= self.max_pending:      # a dead Twin must not grow our memory without limit
                dropped = self.pending.pop(0)
                if self.log:
                    self.log.emit("containment_queue_full", dropped_tool=dropped[0])
            self.pending.append((tool, payload))
        if self.log:
            self.log.emit("containment_call", tool=tool, ok=ok, **payload)
        return ok

    def flush_pending(self) -> int:
        todo, self.pending = self.pending, []
        done = 0
        for tool, payload in todo:
            if self._post(tool, payload):
                done += 1
                if self.log:
                    self.log.emit("containment_retry_ok", tool=tool, **payload)
            else:
                self.pending.append((tool, payload))
        return done

    def quarantine_device(self, device_id, reason):
        return self._call("quarantine_device", {"device_id": device_id, "reason": reason})

    def isolate_sensor(self, device_id, reason):
        return self._call("isolate_sensor", {"device_id": device_id, "reason": reason})

    def restore_device(self, device_id):
        return self._call("restore_device", {"device_id": device_id})

    def rollback_reading(self, observation_id):
        return self._call("rollback_reading", {"observation_id": observation_id})
