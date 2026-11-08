"""Security tests: one test per defence, plus the "without the defence, the attack works" proof for the big ones."""
import copy
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from guardian.api import create_app
from guardian.containment import HttpTwinClient
from guardian.engine import GuardianEngine, load_config
from guardian.eventlog import EventLog
from mocks import mock_twin as m
from scenarios.run import Run, first_untrusted, run

H = {"Authorization": "Bearer s3cret", "Content-Type": "application/json"}


def cfg(**sec):
    c = load_config()
    for k, v in sec.items():
        if isinstance(v, dict):
            c["security"][k].update(v)
        else:
            c["security"][k] = v
    return c


def obs(dev="WL-01", t=0.0, value=40.0, zone="canal-north", seq=None, oid=None):
    o = m._obs(dev, "water_level", zone, value, "cm", t, 0)
    return o.model_copy(update={"seq": seq, "observation_id": oid or f"OBS-{dev}-{t}"})


# ---------- 1. authentication fails closed ----------------------------------------------------------------------------
def test_app_refuses_to_start_without_token(monkeypatch):
    monkeypatch.delenv("GUARDIAN_TOKEN", raising=False)
    monkeypatch.delenv("GUARDIAN_DEMO", raising=False)
    with pytest.raises(RuntimeError, match="GUARDIAN_TOKEN"):
        create_app(GuardianEngine(use_obs_time=True))


def test_demo_mode_is_explicit_and_logged(monkeypatch):
    monkeypatch.delenv("GUARDIAN_TOKEN", raising=False)
    log = EventLog()
    c = TestClient(create_app(GuardianEngine(eventlog=log, use_obs_time=True), allow_open=True))
    assert c.get("/devices/x/trust").status_code == 404                     # open, so not 401
    assert any(e["event"] == "auth_disabled" for e in log.events)
    monkeypatch.setenv("GUARDIAN_DEMO", "1")
    create_app(GuardianEngine(use_obs_time=True))                           # env switch also works


def test_token_comparison_rejects_near_misses():
    c = TestClient(create_app(GuardianEngine(use_obs_time=True), token="s3cret"))
    for bad in ("Bearer s3cre", "Bearer s3crett", "bearer s3cret", "s3cret", ""):
        assert c.get("/devices/x/trust", headers={"Authorization": bad}).status_code == 401
    assert c.get("/devices/x/trust", headers=H).status_code == 404


def test_timeline_cannot_be_written_without_the_token():
    from guardian.timeline import TimelineStore
    c = TestClient(create_app(GuardianEngine(use_obs_time=True), token="s3cret", timeline=TimelineStore()))
    ev = {"layer": "brain", "event": "fake", "timestamp": "2026-10-16T10:00:00Z"}
    assert c.post("/timeline/events", json=ev).status_code == 401
    assert c.post("/timeline/events", json=ev, headers=H).status_code == 200


# ---------- 2. oversized and hostile input -------------------------------------------------------------------------
def test_oversized_body_is_refused_with_413():
    c = TestClient(create_app(GuardianEngine(use_obs_time=True), token="s3cret"))
    r = c.post("/ingest/default", content=b"x" * 70_000, headers=H)
    assert r.status_code == 413 and r.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"


def test_oversized_chunked_body_without_length_is_refused_too():
    c = TestClient(create_app(GuardianEngine(use_obs_time=True), token="s3cret"))

    def chunks():
        for _ in range(100):
            yield b"y" * 1024
    r = c.post("/ingest/default", content=chunks(), headers=H)
    assert r.status_code == 413


def test_small_normal_body_still_works():
    c = TestClient(create_app(GuardianEngine(use_obs_time=True), token="s3cret"))
    r = c.post("/observations", content=obs().model_dump_json(), headers=H)
    assert r.status_code == 200


@pytest.mark.parametrize("bad", ["../etc/passwd", "WL 01", "WL-01\n{\"x\":1}", "a" * 65, "", "WL/01"])
def test_hostile_device_ids_are_refused(bad):
    c = TestClient(create_app(GuardianEngine(use_obs_time=True), token="s3cret"))
    body = {"device_id": bad, "value": 40, "timestamp": "2026-10-16T10:00:00Z"}
    r = c.post("/ingest/default", json=body, headers=H)
    assert r.status_code == 422                                              # malformed (missing field or schema error)
    assert r.json()["verdict"]["verdict"] == "UNTRUSTED"
    assert c.post("/observations", json={**body, "observation_id": "o", "sensor_type": "water_level"},
                  headers=H).status_code == 422


# ---------- 3. future timestamp poisoning ----------------------------------------------------------------------------
NOW = datetime(2026, 10, 16, 10, 0, 30, tzinfo=timezone.utc)


def test_future_timestamp_is_refused_and_does_not_poison_the_device():
    e = GuardianEngine(cfg(), use_obs_time=True, clock=lambda: NOW)
    assert e.ingest(obs("WL-03", 0)).verdict == "TRUSTED"
    bad = e.ingest(obs("WL-03", 86400, oid="POISON"))
    assert bad.verdict == "UNTRUSTED" and bad.reason_codes == ["FUTURE_TIMESTAMP"]
    nxt = e.ingest(obs("WL-03", 20))                                         # the next real reading is still fine
    assert nxt.verdict == "TRUSTED" and nxt.trust_score == 1.0


def test_without_the_check_the_future_timestamp_does_poison_the_device():
    e = GuardianEngine(cfg(max_future_skew_s=None), use_obs_time=True, clock=lambda: NOW)
    e.ingest(obs("WL-03", 0))
    e.ingest(obs("WL-03", 86400, oid="POISON"))
    nxt = e.ingest(obs("WL-03", 20))
    assert nxt.verdict == "UNTRUSTED" and "REPLAY_OR_OUT_OF_ORDER" in nxt.reason_codes     # the attack works


def test_small_clock_skew_is_tolerated():
    e = GuardianEngine(cfg(), use_obs_time=True, clock=lambda: NOW)
    assert e.ingest(obs("WL-01", 30 + 4)).verdict == "TRUSTED"               # 4 s ahead, limit is 5 s


def test_t12_scenario(tmp_path):
    r = run("t12", str(tmp_path))
    poison = [v for v in r["verdicts"] if v.observation_id.startswith("OBS-POISON")]
    assert len(poison) == 1 and poison[0].reason_codes == ["FUTURE_TIMESTAMP"]
    assert all(v.verdict == "TRUSTED" for v in r["verdicts"] if v not in poison)       # WL-03 kept working
    assert r["twin"].calls == []


# ---------- 4. message flood ---------------------------------------------------------------------------------------
def test_flood_is_limited_and_the_claimed_device_is_not_punished():
    log = EventLog()
    e = GuardianEngine(cfg(), eventlog=log, use_obs_time=True)
    verdicts = [e.ingest(obs("WL-02", i * 0.005, 40.0 + (i % 7) * 0.1, oid=f"F{i}")) for i in range(100)]
    flooded = [v for v in verdicts if v.reason_codes == ["MESSAGE_FLOOD"]]
    assert len(flooded) == 100 - 20
    t = e.trust_of("WL-02")
    assert t["trust_score"] == 1.0 and not t["quarantined"] and t["refused"] == 80
    assert len([x for x in log.events if x["event"] == "flood_detected"]) == 1        # logged once per episode
    assert e.ingest(obs("WL-02", 5.0, 40.3, oid="later")).verdict == "TRUSTED"              # normal service resumes


def test_flood_limiter_off_when_set_to_null():
    e = GuardianEngine(cfg(rate_limit=None), use_obs_time=True)
    assert not any("MESSAGE_FLOOD" in e.ingest(obs("WL-02", i * 0.5 / 100, 40.0 + (i % 7) * 0.1, oid=f"F{i}")).reason_codes for i in range(60))


def test_t10_scenario(tmp_path):
    r = run("t10", str(tmp_path))
    flood = [v for v in r["verdicts"] if "MESSAGE_FLOOD" in v.reason_codes]
    assert len(flood) > 100 and all(v.device_id == "WL-02" for v in flood)
    assert r["engine"].trust_of("WL-02")["trust_score"] == 1.0
    assert r["twin"].calls == []                                             # nothing quarantined, nobody framed
    assert all(v.verdict == "TRUSTED" for v in r["verdicts"] if v.device_id != "WL-02")
    assert any(e["event"] == "flood_detected" for e in r["engine"].log.events)


# ---------- 5. fake-device injection (Sybil) -----------------------------------------------------------------------
def test_unknown_device_is_refused_in_strict_mode_and_gets_no_state():
    e = GuardianEngine(cfg(registry={"mode": "strict", "allowed_ids": ["WL-01"]}), use_obs_time=True)
    assert e.ingest(obs("WL-01", 0)).verdict == "TRUSTED"
    v = e.ingest(obs("WL-99", 0))
    assert v.reason_codes == ["UNKNOWN_DEVICE"] and v.verdict == "UNTRUSTED"
    assert e.trust_of("WL-99") is None and "WL-99" not in e.devices


def test_prefix_mode():
    e = GuardianEngine(cfg(registry={"mode": "prefix"}), use_obs_time=True)
    assert e.ingest(obs("WL-01", 0)).verdict == "TRUSTED"
    assert e.ingest(obs("EVIL-1", 0)).reason_codes == ["UNKNOWN_DEVICE"]


def test_device_and_zone_caps():
    e = GuardianEngine(cfg(registry={"max_devices": 3}), use_obs_time=True)
    for i in range(3):
        assert e.ingest(obs(f"WL-0{i}", 0, zone=f"z{i}")).verdict == "TRUSTED"
    assert e.ingest(obs("WL-09", 0, zone="z9")).reason_codes == ["UNKNOWN_DEVICE"]
    e = GuardianEngine(cfg(registry={"max_devices_per_zone": 2}), use_obs_time=True)
    for i in range(2):
        e.ingest(obs(f"WL-0{i}", 0))
    assert e.ingest(obs("WL-05", 0)).reason_codes == ["UNKNOWN_DEVICE"]


def test_new_device_cannot_vote_as_a_peer_until_it_has_history():
    e = GuardianEngine(cfg(), use_obs_time=True)
    for d in ("WL-01", "WL-02", "WL-03", "WL-04"):                           # four established sensors
        for t in range(5):
            e.ingest(obs(d, t, 40.0 + t * 0.01, oid=f"{d}-{t}"))
    for d in ("WL-91", "WL-92", "WL-93"):                                    # three brand-new liars
        e.ingest(obs(d, 5, 5.0, oid=f"{d}-5"))
    peers = e._peer_values(obs("WL-01", 5), 5.0 + m.T0.timestamp())
    assert 5.0 not in peers


def test_without_the_registry_fake_devices_discredit_the_real_sensors():
    """The attack T11 defends against: with an open registry, thirty patient fakes outvote five real sensors."""
    e = GuardianEngine(cfg(), use_obs_time=True)
    verdicts = [e.ingest(o) for o in m.fake_device_injection()]
    real_flagged = {v.device_id for v in verdicts if v.device_id in m.WL and v.verdict == "UNTRUSTED"}
    assert real_flagged == set(m.WL)                                         # the attack works against an open registry


def test_a_zone_cap_also_stops_it_even_with_an_open_registry():
    e = GuardianEngine(cfg(registry={"max_devices_per_zone": 8}), use_obs_time=True)
    verdicts = [e.ingest(o) for o in m.fake_device_injection()]
    assert not [v for v in verdicts if v.device_id in m.WL and v.verdict == "UNTRUSTED"]
    assert len(e.devices) == 8


def test_t11_scenario_real_sensors_stay_trusted(tmp_path):
    r = run("t11", str(tmp_path))
    real = [v for v in r["verdicts"] if v.device_id in m.WL]
    assert real and all(v.verdict == "TRUSTED" and v.trust_score == 1.0 for v in real)
    fakes = [v for v in r["verdicts"] if v.device_id in m.FAKE_IDS]
    assert fakes and all(v.reason_codes == ["UNKNOWN_DEVICE"] for v in fakes)
    assert r["twin"].calls == [] and len(r["engine"].devices) == 5


# ---------- 6. memory is bounded ----------------------------------------------------------------------------------------
def test_verdict_store_is_bounded():
    e = GuardianEngine(cfg(max_verdicts=50), use_obs_time=True)
    for i in range(200):
        e.ingest(obs("WL-01", i, oid=f"O{i}"))
    assert len(e.verdicts) == 50 and "O199" in e.verdicts and "O0" not in e.verdicts


def test_containment_retry_queue_is_bounded():
    log = EventLog()
    c = HttpTwinClient("http://twin.local", retries=0, max_pending=5, eventlog=log,
                       client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(503))))
    for i in range(20):
        c.quarantine_device(f"WL-{i:02d}", "x")
    assert len(c.pending) == 5 and any(e["event"] == "containment_queue_full" for e in log.events)
