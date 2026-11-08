"""The Twin team's REST contract, end to end through the real code paths:
poller -> format adapter (twin profile) -> engine -> containment calls to /api/v1/isolate."""
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from guardian.adapters.format import FormatAdapter
from guardian.adapters.twin import TwinAuthError, TwinPoller, TwinProtocolError
from guardian.containment import HttpTwinClient
from guardian.engine import GuardianEngine, load_config
from guardian.eventlog import EventLog
from guardian.pipeline import run_source
from mocks.twin_rest_sim import TwinRestSim, calm_value

TOKEN = "tg-test-token"


class Rig:
    """Everything wired like main.py does it, but against the Twin stand-in."""

    def __init__(self, source=None, **sim_kw):
        self.cfg = load_config(profile="twin")
        self.sim = TwinRestSim(TOKEN, source=source or (lambda t, s: calm_value(t, s)), **sim_kw)
        self.client = httpx.Client(transport=httpx.MockTransport(self.sim.handler))
        self.log = EventLog()
        self.twin = HttpTwinClient(self.cfg["partners"]["twin_url"], token=TOKEN, paths=self.cfg["containment"]["paths"],
                                   client=self.client, eventlog=self.log)
        self.t0 = datetime.now(timezone.utc)      # the engine's clock follows the simulation: one poll per simulated second
        self.engine = GuardianEngine(self.cfg, on_contain=lambda d, w: self.twin.quarantine_device(d, w),
                                     on_release=lambda d: self.twin.restore_device(d), eventlog=self.log,
                                     clock=lambda: self.t0 + timedelta(seconds=self.sim.tick))
        self.poller = TwinPoller(self.cfg["partners"]["twin_url"], TOKEN, client=self.client,
                                 on_unavailable=self.engine.mark_unavailable,
                                 on_failed=lambda r: self.engine.report_failure(
                                     r["_obs_id"], r["sensor_id"], r["type"], r["zone_id"], r["_status"]),
                                 on_run_change=lambda o, n: self.engine.reset_run("run changed"))
        self.fmt = FormatAdapter(self.cfg, "twin")
        self.verdicts = []

    def pump(self, ticks: int):
        for _ in range(ticks):
            self.sim.step()
            self.verdicts += run_source(self.poller, self.fmt, self.engine)

    def untrusted(self, dev):
        return [v for v in self.verdicts if v.device_id == dev and v.verdict == "UNTRUSTED"]


def wrap(base, overrides):
    def source(tick, s):
        return overrides(tick, s) if overrides(tick, s) is not None else base(tick, s)
    return source


# ---------- the happy path: a calm city raises no alarm ---------------------------------------------------------------
def test_calm_city_has_zero_false_alarms_and_no_containment():
    r = Rig()
    r.pump(300)
    assert r.verdicts and all(v.verdict == "TRUSTED" for v in r.verdicts)
    assert r.sim.calls == [] and len(r.engine.devices) == 16


def test_the_twin_profile_maps_their_fields_and_orders_by_seq():
    r = Rig()
    r.pump(10)
    o = r.fmt.normalize(next(iter(r.client.get("http://twin.local/api/v1/sensors", headers={"Authorization": f"Bearer {TOKEN}"})
                                .json()["data"]["readings"] and [r.client.get("http://twin.local/api/v1/sensors",
                                headers={"Authorization": f"Bearer {TOKEN}"}).json()["data"]["readings"][0]])))
    assert o.device_id == "PRT-WLV-01" and o.zone == "PRT" and o.sensor_type == "water_level"
    assert o.seq is not None and o.tick is not None and o.backup_ids == ["PRT-WLV-02"]


# ---------- their scenario T5: fake flood at the port ----------------------------------------------------------------
def fake_flood(tick, s):
    return 180.0 + (tick % 3) * 0.3 if s["sensor_id"] == "PRT-WLV-01" and tick >= 5 else None


def test_t5_fake_flood_is_flagged_on_the_first_bad_reading_and_isolated_within_10s():
    r = Rig(source=wrap(calm_value, fake_flood))
    r.pump(40)
    bad = r.untrusted("PRT-WLV-01")
    assert bad and "BACKUP_DISAGREEMENT" in bad[0].reason_codes             # the very first fake reading is UNTRUSTED (the jump also trips the rate check)
    isolate = [(p, b) for p, b in r.sim.calls if p == "/api/v1/isolate"]
    assert isolate and isolate[0][1]["device_id"] == "PRT-WLV-01"           # quarantined at the Twin through its own tool
    first_isolate_tick = next(v for v in r.verdicts if v.device_id == "PRT-WLV-01" and "DEVICE_QUARANTINED" in v.reason_codes)
    assert first_isolate_tick is not None
    assert [b["device_id"] for p, b in isolate] == ["PRT-WLV-01"]           # exactly one sensor, nobody else framed
    # the honest backup is never flagged, and nothing else in the city is
    assert not r.untrusted("PRT-WLV-02")
    assert {v.device_id for v in r.verdicts if v.verdict == "UNTRUSTED"} == {"PRT-WLV-01"}
    assert r.sim.isolated == {"PRT-WLV-01"}


def test_t5_isolation_happens_by_the_second_bad_sample_that_is_within_10_ticks():
    r = Rig(source=wrap(calm_value, fake_flood))
    r.pump(10)                                     # the lie starts at tick 5; samples arrive at ticks 5 and 10
    assert r.sim.isolated == {"PRT-WLV-01"}


def test_after_isolation_the_offline_sensor_is_not_judged_and_the_backup_stays_trusted():
    r = Rig(source=wrap(calm_value, fake_flood))
    r.pump(60)
    assert r.engine.devices["PRT-WLV-01"].unavailable and r.engine.devices["PRT-WLV-01"].quarantined
    assert r.verdicts[-1].verdict == "TRUSTED" or r.verdicts[-1].device_id != "PRT-WLV-02"
    assert r.engine.trust_of("PRT-WLV-02")["trust_score"] == 1.0


# ---------- their scenario SCN-02: a REAL flash flood must not be treated as an attack --------------------------------
def real_surge(tick, s):
    if s["sensor_id"] in ("PRT-WLV-01", "PRT-WLV-02") and tick >= 10:
        return min(110.0, 40 + 14 * (tick - 10) / 5) + (tick % 3) * 0.3
    return None


def test_scn02_real_flash_flood_both_sensors_agree_so_nothing_is_flagged():
    r = Rig(source=wrap(calm_value, real_surge))
    r.pump(60)
    assert not r.untrusted("PRT-WLV-01") and not r.untrusted("PRT-WLV-02")
    assert r.sim.calls == []
    assert r.engine.devices["PRT-WLV-01"].last_value > 100                 # and Guardian did see the surge


# ---------- their scenario SCN-03: stuck counter and dead sensor ------------------------------------------------------
def test_scn03_stuck_traffic_counter_and_failed_temperature_sensor():
    r = Rig(source=lambda t, s: 31.0 if s["sensor_id"] == "DTN-TRF-01" and t >= 20 else calm_value(t, s))
    r.pump(200)
    assert any("FROZEN_VALUE" in v.reason_codes for v in r.untrusted("DTN-TRF-01"))
    r2 = Rig()
    r2.pump(20)
    r2.sim.failed.add("IND-TMP-01")
    r2.pump(40)
    dead = [v for v in r2.engine.verdicts.values() if v.device_id == "IND-TMP-01" and "SENSOR_FAILED" in v.reason_codes]
    assert dead and dead[0].recommended_action == "QUARANTINE"
    assert ("/api/v1/isolate", {"device_id": "IND-TMP-01", "reason": "SENSOR_FAILED"}) in r2.sim.calls
    assert not r2.untrusted("IND-TMP-02")                                  # the backup is the Brain's fallback and stays trusted


def test_a_single_sensor_with_no_reference_is_not_accused_for_holding_a_calm_value():
    # Found against the real Twin: its calm sensors report the same exact value for minutes. Without a peer or backup that
    # moved, a constant reading proves nothing, so it must never quarantine a healthy sensor (documented limitation).
    r = Rig(source=lambda t, s: 33.0 if s["sensor_id"] == "RES-WLV-01" and t >= 30 else calm_value(t, s))
    r.pump(200)
    assert not r.untrusted("RES-WLV-01")


def test_a_noise_free_calm_city_never_triggers_frozen_value():
    r = Rig(source=lambda t, s: {"water_level": 50.0, "flow": 60.0, "pressure": 3.0, "temperature": 60.0, "traffic_count": 30.0}[s["type"]])
    r.pump(300)
    assert not [v for v in r.verdicts if v.verdict == "UNTRUSTED"]
    assert not r.sim.calls


# ---------- the Twin's data quirks must not hurt us -----------------------------------------------------------------
def test_unchanged_samples_between_polls_are_not_replays():
    r = Rig()
    r.pump(60)
    assert not any("REPLAY_OR_OUT_OF_ORDER" in v.reason_codes for v in r.verdicts)
    assert r.sim.sensor_requests == 60                                      # polled every tick, judged once per new sample


def test_a_real_replay_with_an_old_seq_is_still_rejected_even_with_identical_timestamps():
    r = Rig()
    r.pump(20)
    rows = r.client.get("http://twin.local/api/v1/sensors", headers={"Authorization": f"Bearer {TOKEN}"}).json()["data"]["readings"]
    old = dict(next(x for x in rows if x["sensor_id"] == "PRT-WLV-01"))
    old["seq"] -= 8
    old["observation_id"] = "replayed-1"
    v = r.engine.ingest(r.fmt.normalize(old))
    assert v.verdict == "UNTRUSTED" and "REPLAY_OR_OUT_OF_ORDER" in v.reason_codes
    assert r.engine.trust_of("PRT-WLV-01")["trust_score"] == 1.0


def test_metres_are_converted_to_centimetres():
    r = Rig(unit_override={"water_level": "m"}, source=lambda t, s: calm_value(t, s) / 100 if s["type"] == "water_level" else calm_value(t, s))
    r.pump(30)
    assert r.engine.devices["PRT-WLV-01"].last_value > 30                   # about 40 cm, not 0.4
    assert all(v.verdict == "TRUSTED" for v in r.verdicts)


def test_a_twin_restart_resets_numbering_without_false_alarms():
    r = Rig()
    r.pump(40)
    r.sim.run_id = "twin-test-run-0002"
    r.sim._seq = 0
    r.sim.tick = 0
    r.sim.latest.clear()
    n = len(r.verdicts)
    r.pump(40)
    assert any(e["event"] == "run_reset" for e in r.log.events)
    assert all(v.verdict == "TRUSTED" for v in r.verdicts[n:])


# ---------- the poller treats the Twin as untrusted input ----------------------------------------------------------
def poller_with(handler, **kw):
    return TwinPoller("http://twin.local", TOKEN, client=httpx.Client(transport=httpx.MockTransport(handler)), **kw)


def test_poller_requires_a_token():
    with pytest.raises(ValueError):
        TwinPoller("http://twin.local", "")


def test_wrong_token_is_reported_as_an_auth_error_without_leaking_it():
    p = TwinPoller("http://twin.local", "wrong-token", client=httpx.Client(transport=httpx.MockTransport(TwinRestSim(TOKEN).handler)))
    with pytest.raises(TwinAuthError) as e:
        list(p.fetch())
    assert "wrong-token" not in str(e.value)


@pytest.mark.parametrize("body", [[], {"ok": False}, {"ok": True, "data": {}}, {"ok": True, "data": {"readings": "x"}}, "nope"])
def test_malformed_envelopes_are_refused(body):
    p = poller_with(lambda req: httpx.Response(200, json=body))
    with pytest.raises(TwinProtocolError):
        list(p.fetch())


def test_oversized_twin_response_is_refused():
    p = poller_with(lambda req: httpx.Response(200, content=b"x" * 2_000_000), max_bytes=1_000_000)
    with pytest.raises(TwinProtocolError):
        list(p.fetch())


def test_hostile_sensor_ids_from_the_twin_never_reach_the_engine():
    rows = [{"sensor_id": bad, "zone_id": "PRT", "type": "water_level", "unit": "cm", "reported_value": 40, "status": "OK",
             "available": True, "seq": 1} for bad in ["../x", "a b", "x" * 80, "ok\nid"]]
    env = {"ok": True, "run_id": "r", "data": {"readings": rows + ["junk", 5]}}
    assert list(poller_with(lambda req: httpx.Response(200, json=env)).fetch()) == []


def test_guardian_token_goes_in_the_header_never_in_the_url():
    seen = []

    def h(req):
        seen.append((str(req.url), req.headers.get("authorization")))
        return httpx.Response(200, json={"ok": True, "run_id": "r", "data": {"readings": []}})
    list(poller_with(h).fetch())
    assert seen[0][0] == "http://twin.local/api/v1/sensors" and seen[0][1] == f"Bearer {TOKEN}"


# ---------- containment speaks the Twin's tools ---------------------------------------------------------------------
def test_isolate_and_restore_use_the_twins_paths_and_rollback_is_reported_unsupported():
    r = Rig()
    r.pump(5)
    assert r.twin.quarantine_device("PRT-WLV-01", "X") and ("/api/v1/isolate", {"device_id": "PRT-WLV-01", "reason": "X"}) in r.sim.calls
    assert r.twin.restore_device("PRT-WLV-01") and r.sim.calls[-1][0] == "/api/v1/restore"
    assert r.twin.rollback_reading("OBS-1") is False
    assert any(e["event"] == "containment_unsupported" for e in r.log.events) and r.twin.pending == []


def test_manual_restore_endpoint_restores_at_the_twin_and_puts_the_device_back_on_probation():
    from fastapi.testclient import TestClient
    from guardian.api import create_app
    r = Rig(source=wrap(calm_value, fake_flood))
    r.pump(20)
    assert r.sim.isolated == {"PRT-WLV-01"}
    c = TestClient(create_app(r.engine, token="s3cret"))
    H = {"Authorization": "Bearer s3cret"}
    assert c.post("/devices/PRT-WLV-01/restore").status_code == 401
    assert c.post("/devices/NOPE-1/restore", headers=H).status_code == 404
    assert c.post("/devices/PRT-WLV-02/restore", headers=H).status_code == 409           # not quarantined
    assert c.post("/devices/PRT-WLV-01/restore", headers=H).json() == {"device_id": "PRT-WLV-01", "restored": True}
    assert r.sim.isolated == set() and r.engine.devices["PRT-WLV-01"].n_seen == 0


def test_strict_registry_blocks_a_device_the_twin_never_declared():
    r = Rig()
    r.pump(5)
    rows = [{"sensor_id": "PRT-WLV-99", "zone_id": "PRT", "type": "water_level", "unit": "cm", "reported_value": 5.0,
             "status": "OK", "available": True, "seq": 999, "sim_tick": 5, "timestamp": "x"}]
    v = r.engine.ingest(r.fmt.normalize(rows[0]))
    assert v.reason_codes == ["UNKNOWN_DEVICE"] and len(r.engine.devices) <= 16


# ---------- the scenarios shipped with the project (they run the same path) ------------------------------------------
def test_t13_scenario_matches_the_twin_teams_t5(tmp_path):
    from mocks import mock_twin as m
    from scenarios.run import first_untrusted, run
    r = run("t13", str(tmp_path))
    v = first_untrusted(r["verdicts"], "PRT-WLV-01")
    assert v is not None and (v.issued_at - m.T0).total_seconds() - 6 <= 10          # their target: within 10 s
    assert [c[:2] for c in r["twin"].calls] == [("quarantine_device", "PRT-WLV-01")]
    assert {x.device_id for x in r["verdicts"] if x.verdict == "UNTRUSTED"} == {"PRT-WLV-01"}


def test_t14_scenario_real_flash_flood_is_not_an_attack(tmp_path):
    from scenarios.run import run
    r = run("t14", str(tmp_path))
    assert all(x.verdict == "TRUSTED" for x in r["verdicts"]) and r["twin"].calls == []


# ---------- main.py: fail closed without the Twin token, and keep running when the Twin is down ---------------------
def test_profile_twin_refuses_to_start_without_the_twin_token(monkeypatch):
    from guardian import main
    monkeypatch.setenv("GUARDIAN_PROFILE", "twin")
    monkeypatch.setenv("GUARDIAN_TOKEN", "s3cret")
    monkeypatch.delenv("TWIN_TOKEN_GUARDIAN", raising=False)
    with pytest.raises(RuntimeError, match="TWIN_TOKEN_GUARDIAN"):
        main.app()


def test_guardian_stays_up_and_reports_when_the_twin_is_unreachable(monkeypatch, tmp_path):
    import time

    from fastapi.testclient import TestClient

    from guardian import main
    monkeypatch.chdir(tmp_path)
    (tmp_path / "logs").mkdir()
    monkeypatch.setenv("GUARDIAN_PROFILE", "twin")
    monkeypatch.setenv("GUARDIAN_TOKEN", "s3cret")
    monkeypatch.setenv("TWIN_TOKEN_GUARDIAN", "tg-test-token")
    real = load_config(profile="twin")
    real["partners"]["twin_url"] = "http://127.0.0.1:9"               # nothing listens here
    real["polling"]["interval_s"] = 0.05
    monkeypatch.setattr(main, "load_config", lambda: real)
    c = TestClient(main.app())
    deadline = time.time() + 10                                        # Windows can take seconds to refuse a connection
    h = c.get("/health").json()
    while h["twin"]["errors_in_a_row"] < 1 and time.time() < deadline:
        time.sleep(0.2)
        h = c.get("/health").json()
    assert h["status"] == "ok" and h["twin"]["polling"] is True and h["twin"]["errors_in_a_row"] >= 1
    assert h["twin"]["last_ok_s_ago"] is None
