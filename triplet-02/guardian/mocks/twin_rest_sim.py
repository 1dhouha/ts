"""A stand-in for the Twin team's REST interface (contract twin-interface-0.1), for tests and offline demos.

It answers exactly what their router answers for a `guardian` token:
  GET  /api/v1/sensors   -> {ok, run_id, data:{sim_tick, count, readings:[...]}, error, timestamp}
  POST /api/v1/isolate   {device_id, reason}     POST /api/v1/restore  {device_id}
Quirks copied on purpose from their real logs: every reading carries the SAME wall-clock timestamp, each sensor reports
only every `interval` ticks (between samples the latest value repeats with the same seq), an isolated sensor becomes
OFFLINE with available=false and no value, and credentials in the URL are refused.
Use it with httpx.MockTransport(sim.handler)."""
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

import httpx

INVENTORY = Path(__file__).resolve().parents[1] / "partners" / "twin" / "sensors.json"
WALL = "2026-10-22T21:45:46.889791+00:00"


def inventory() -> list[dict]:
    return json.loads(INVENTORY.read_text(encoding="utf-8"))["sensors"]


class TwinRestSim:
    def __init__(self, token: str = "tg-test-token", run_id: str = "twin-test-run-0001",
                 source: Optional[Callable[[int, dict], Optional[float]]] = None, unit_override: Optional[dict] = None):
        self.token, self.run_id, self.source = token, run_id, source
        self.unit_override = unit_override or {}
        self.sensors = {s["sensor_id"]: s for s in inventory()}
        self.tick = 0
        self._seq = 0
        self.latest: dict[str, dict] = {}
        self.isolated: set[str] = set()
        self.failed: set[str] = set()
        self.calls: list[tuple[str, dict]] = []
        self.sensor_requests = 0

    def _sample(self) -> None:
        for sid, s in self.sensors.items():
            if self.tick % int(s.get("interval", 5)) == 0:
                self._seq += 1
                v = self.source(self.tick, s) if self.source else 0.0
                self.latest[sid] = {"sim_tick": self.tick, "seq": self._seq, "value": v}

    def step(self, n: int = 1) -> None:
        if self.tick == 0 and not self.latest:
            self._sample()                       # like the real Twin, every sensor has a first reading at tick 0
        for _ in range(n):
            self.tick += 1
            self._sample()

    def _reading(self, sid: str) -> Optional[dict]:
        s, last = self.sensors[sid], self.latest.get(sid)
        if last is None:
            return None
        row = {"sensor_id": sid, "zone_id": s["zone_id"], "type": s["type"],
               "unit": self.unit_override.get(s["type"], s["unit"]), "timestamp": WALL, "sim_tick": last["sim_tick"],
               "seq": last["seq"], "backup_ids": list(s["backup_ids"]), "linked_device_ids": list(s["linked_device_ids"])}
        if sid in self.isolated:
            return {**row, "reported_value": None, "status": "OFFLINE", "available": False}
        if sid in self.failed:
            return {**row, "reported_value": None, "status": "FAILED", "available": True}
        return {**row, "reported_value": last["value"], "status": "OK", "available": True}

    def _env(self, data=None, error=None) -> dict:
        return {"ok": error is None, "run_id": self.run_id, "data": data, "error": error,
                "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}

    def _err(self, status: int, code: str, msg: str) -> httpx.Response:
        return httpx.Response(status, json=self._env(error={"code": code, "message": msg}))

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.query and b"token" in request.url.query.lower():
            return self._err(400, "INVALID_PARAM", "Credentials are not accepted in URLs.")
        if request.headers.get("authorization") != f"Bearer {self.token}":
            return self._err(401, "NOT_AUTHORIZED", "A valid Bearer credential is required.")
        path = request.url.path.rstrip("/")
        if request.method == "GET" and path == "/api/v1/sensors":
            self.sensor_requests += 1
            rows = [r for r in (self._reading(sid) for sid in self.sensors) if r]
            return httpx.Response(200, json=self._env({"sim_tick": self.tick, "count": len(rows), "readings": rows}))
        if request.method == "POST" and path in ("/api/v1/isolate", "/api/v1/restore"):
            body = json.loads(request.content or b"{}")
            self.calls.append((path, body))
            dev = body.get("device_id")
            if dev not in self.sensors:
                return self._err(400, "DEVICE_NOT_FOUND", f"Unknown device {dev}")
            if path.endswith("isolate"):
                self.isolated.add(dev)
            elif dev in self.isolated:
                self.isolated.discard(dev)
            else:
                return self._err(400, "DEVICE_NOT_FOUND", f"{dev} was not isolated")
            return httpx.Response(200, json=self._env({"device_id": dev, "status": "RESTRICTED" if dev in self.isolated else "OK"}))
        return self._err(404, "ROUTE_NOT_FOUND", f"{request.method} {path}")


# ---- a believable city: values inside normal limits, a little deterministic noise ----------------------------------
BASE = {"PRT-WLV-01": 40, "PRT-WLV-02": 40, "PRT-FLW-01": 120, "PRT-PRS-01": 4.0, "IND-TMP-01": 60, "IND-TMP-02": 60,
        "IND-PRS-01": 4.0, "IND-FLW-01": 60, "DTN-TRF-01": 30, "DTN-TRF-02": 28, "DTN-WLV-01": 30, "DTN-TMP-01": 20,
        "RES-WLV-01": 150, "RES-PRS-01": 4.0, "RES-FLW-01": 30, "RES-TRF-01": 20}
NOISE = {"water_level": 0.4, "flow": 1.5, "pressure": 0.05, "temperature": 0.3, "traffic_count": 2.0}


def calm_value(tick: int, s: dict) -> float:
    idx = tick // int(s.get("interval", 5))                       # sample number, so consecutive samples differ
    jitter = (((sum(map(ord, s["sensor_id"])) + idx * 3) % 5) - 2) * NOISE[s["type"]]
    return round(BASE[s["sensor_id"]] + jitter, 3)


# ---- scenario generators: readings that travel the REAL path (Twin REST shape -> poller -> twin profile) ----------------
def _through_the_twin_contract(source, ticks: int = 60):
    from datetime import timedelta

    from guardian.adapters.format import FormatAdapter
    from guardian.adapters.twin import TwinPoller
    from guardian.engine import load_config
    from mocks.mock_twin import T0

    cfg = load_config(profile="twin")
    sim = TwinRestSim("tg-scenario-token", source=source)
    poller = TwinPoller("http://twin.local", "tg-scenario-token", client=httpx.Client(transport=httpx.MockTransport(sim.handler)))
    fmt = FormatAdapter(cfg, "twin")
    for tick in range(ticks):
        sim.step()
        for raw in poller.fetch():
            yield fmt.normalize(raw).model_copy(update={"timestamp": T0 + timedelta(seconds=tick + 1)})


def twin_fake_flood(ticks: int = 60):
    """Twin team's T5: from tick 5 the port sensor PRT-WLV-01 reports about 180 cm while its backup PRT-WLV-02 says 40."""
    def source(tick, s):
        if s["sensor_id"] == "PRT-WLV-01" and tick >= 5:
            return 180.0 + (tick % 3) * 0.3
        return calm_value(tick, s)
    return _through_the_twin_contract(source, ticks)


def twin_real_flash_flood(ticks: int = 60):
    """Twin team's SCN-02: a REAL surge, both redundant port sensors climb from 40 to 110 cm together. Not an attack."""
    def source(tick, s):
        if s["sensor_id"] in ("PRT-WLV-01", "PRT-WLV-02") and tick >= 10:
            return min(110.0, 40 + 14 * (tick - 10) / 5) + (tick % 3) * 0.3
        return calm_value(tick, s)
    return _through_the_twin_contract(source, ticks)
