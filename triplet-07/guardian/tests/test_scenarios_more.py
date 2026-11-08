import httpx
from fastapi.testclient import TestClient

from guardian.api import create_app
from guardian.engine import GuardianEngine
from mocks import mock_twin as m
from mocks.mock_brain import MockBrain, http_verdict_getter
from scenarios.run import first_untrusted, run


def events(r, name):
    return [e for e in r["engine"].log.events if e["event"] == name]


def test_t4_camera_dropout_is_flagged_stale_but_never_quarantined(tmp_path):
    r = run("t4", str(tmp_path))
    t = r["engine"].trust_of("CAM-01")
    assert t["stale"] is True and t["quarantined"] is False and t["trust_score"] == 1.0
    assert events(r, "device_stale")[0]["device_id"] == "CAM-01"
    assert r["twin"].calls == [] and all(v.verdict == "TRUSTED" for v in r["verdicts"])


def test_stale_device_recovers_when_readings_resume():
    e = GuardianEngine(use_obs_time=True)
    e.ingest(m._obs("CAM-01", "traffic_camera", "z", 30, "%", 0, 0))
    e.check_availability(m.T0.replace(second=30))
    assert e.trust_of("CAM-01")["stale"] is True
    e.ingest(m._obs("CAM-01", "traffic_camera", "z", 31, "%", 31, 31))
    assert e.trust_of("CAM-01")["stale"] is False


def test_t5_compromised_air_quality_monitor_caught_within_10s(tmp_path):
    r = run("t5", str(tmp_path))
    v = first_untrusted(r["verdicts"], "AQ-03")
    assert v is not None and "CROSS_SENSOR_CONTRADICTION" in v.reason_codes
    assert (v.issued_at - m.T0).total_seconds() - 10 <= 10
    assert r["twin"].calls and r["twin"].calls[0][1] == "AQ-03" and len(r["twin"].calls) == 1
    for d in ("AQ-01", "AQ-02", "AQ-04"):
        assert first_untrusted(r["verdicts"], d) is None


def test_t7_brain_refuses_quarantined_device_and_uses_a_healthy_one(tmp_path):
    r = run("t7", str(tmp_path))
    acts = [(d["action"], d.get("device_id")) for d in r["brain"].decisions]
    assert ("REJECT_DEVICE", "WL-03") in acts and ("USE_DEVICE", "WL-02") in acts
    assert any(a == "OPEN_VALVE" for a, _ in acts)


def test_t8_brain_holds_while_guardian_is_unreachable_then_resumes(tmp_path):
    r = run("t8", str(tmp_path))
    b = r["brain"]
    acts = [d["action"] for d in b.decisions]
    assert "HOLD" in acts and "RESUME" in acts and acts.index("HOLD") < acts.index("RESUME") < acts.index("OPEN_VALVE")
    assert (b.valve_open_at - m.T0).total_seconds() >= 35        # nothing happens during the outage (20 to 35 s)


def test_t9_real_incident_handled_while_attack_is_contained(tmp_path):
    r = run("t9", str(tmp_path))
    assert r["brain"].valve_open_at is not None
    assert r["twin"].calls and r["twin"].calls[0][1] == "WL-03"
    replays = [v for v in r["verdicts"] if v.observation_id.endswith("-R")]
    assert len(replays) == 5 and all(v.verdict == "UNTRUSTED" for v in replays)
    assert r["engine"].trust_of("WL-02")["trust_score"] == 1.0
    assert "WL-02" not in [d.get("device_id") for d in r["brain"].decisions if d["action"] == "EXCLUDE_READING"]


def test_mock_brain_over_http_uses_the_real_verdict_api_and_holds_on_unknown_id():
    eng = GuardianEngine(use_obs_time=True)
    c = TestClient(create_app(eng, token="s3cret"))
    brain = MockBrain(http_verdict_getter(c, "s3cret", retries=0))
    o = m._obs("WL-01", "water_level", "z", 200, "cm", 0, 0)
    c.post("/observations", content=o.model_dump_json(), headers={"Authorization": "Bearer s3cret", "Content-Type": "application/json"})
    brain.observe(o)                                              # verdict exists: processed, no hold
    assert brain.decisions == []
    brain.observe(m._obs("WL-02", "water_level", "z", 200, "cm", 1, 1))   # never sent to Guardian: 404 -> unavailable
    assert brain.decisions[0]["action"] == "HOLD"


def test_mock_brain_treats_a_server_error_as_untrusted():
    def h(req):
        return httpx.Response(503)
    client = httpx.Client(base_url="http://guardian.local", transport=httpx.MockTransport(h))
    brain = MockBrain(http_verdict_getter(client, retries=1))
    brain.observe(m._obs("WL-01", "water_level", "z", 200, "cm", 0, 0))
    assert brain.decisions[0]["reason"] == "GUARDIAN_UNAVAILABLE"
