"""Mock City Brain (R1): asks Guardian for a verdict on every reading and only acts on TRUSTED evidence.
No answer in time counts as UNTRUSTED. Lets Guardian be demonstrated alone, in-process or over HTTP."""
from typing import Callable, Optional

import httpx


class GuardianUnavailable(Exception):
    pass


def http_verdict_getter(client, token: Optional[str] = None, retries: int = 1) -> Callable[[str], dict]:
    """GET /verdicts/{id} with a bounded wait and one retry. `client` is an httpx.Client (or a FastAPI TestClient)."""
    headers = {"Authorization": f"Bearer {token}"} if token else {}

    def get(observation_id: str) -> dict:
        for _ in range(retries + 1):
            try:
                r = client.get(f"/verdicts/{observation_id}", headers=headers)
                if r.status_code == 200:
                    return r.json()
            except httpx.HTTPError:
                pass
        raise GuardianUnavailable(observation_id)
    return get


class MockBrain:
    def __init__(self, get_verdict: Callable[[str], dict], log=None, flood_level: float = 120.0, fresh_s: float = 5.0):
        self.get_verdict, self.log = get_verdict, log
        self.flood_level, self.fresh_s = flood_level, fresh_s
        self.latest: dict[str, tuple[float, float]] = {}      # device -> (time, value) of last TRUSTED reading
        self.excluded: set[str] = set()
        self.in_outage = False
        self.valve_open_at = None
        self.decisions: list[dict] = []

    def _decide(self, ts, action, **fields):
        rec = {"action": action, **fields}
        self.decisions.append({"timestamp": ts, **rec})
        if self.log:
            self.log.emit("brain_decision", ts, layer="brain", **rec)

    def observe(self, obs) -> None:
        ts = obs.timestamp
        try:
            v = self.get_verdict(obs.observation_id)
        except GuardianUnavailable:
            if not self.in_outage:
                self.in_outage = True
                self._decide(ts, "HOLD", reason="GUARDIAN_UNAVAILABLE", note="no verdict in time counts as UNTRUSTED")
            return
        if self.in_outage:
            self.in_outage = False
            self._decide(ts, "RESUME", reason="GUARDIAN_BACK")
        if v["verdict"] != "TRUSTED":
            if set(v["reason_codes"]) & {"REPLAY_OR_OUT_OF_ORDER", "MALFORMED_PAYLOAD"}:
                # a problem with this MESSAGE, not with the sensor: ignore the message, keep the device's evidence
                self._decide(ts, "IGNORE_MESSAGE", device_id=obs.device_id, reason_codes=v["reason_codes"]) \
                    if ("msg", obs.device_id) not in self.excluded else None
                self.excluded.add(("msg", obs.device_id))
                return
            self.latest.pop(obs.device_id, None)
            if obs.device_id not in self.excluded:
                self.excluded.add(obs.device_id)
                self._decide(ts, "EXCLUDE_READING", device_id=obs.device_id, reason_codes=v["reason_codes"])
            return
        t = ts.timestamp()
        self.latest[obs.device_id] = (t, obs.value)
        high = sorted(d for d, (tt, val) in self.latest.items() if val >= self.flood_level and t - tt <= self.fresh_s)
        if len(high) >= 2 and self.valve_open_at is None:
            self.valve_open_at = ts
            self._decide(ts, "OPEN_VALVE", evidence=high, note="two independent trusted readings above the flood level")

    def consider(self, device_id: str, trust_lookup: Callable[[str], Optional[dict]], ts=None) -> bool:
        """Brain wants to use a device (for example a sensor to confirm an incident): ask Guardian first."""
        t = trust_lookup(device_id)
        ok = bool(t) and not t["quarantined"] and not t.get("stale", False)
        self._decide(ts, "USE_DEVICE" if ok else "REJECT_DEVICE", device_id=device_id,
                     reason=None if ok else "QUARANTINED_OR_UNKNOWN")
        return ok
