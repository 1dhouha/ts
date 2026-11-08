"""v0.4: slow drift, trust state that survives a restart, per-caller tokens."""
import json
import os
import random

from fastapi.testclient import TestClient

from guardian.api import create_app
from guardian.engine import GuardianEngine
from guardian.state import load_state, save_state
from mocks import mock_twin as m


def feed(eng, ticks, value_of, sensors=("WL-01", "WL-02", "WL-03", "WL-04", "WL-05")):
    """Five sensors in one zone, one reading per second each; value_of(tick, sensor) -> value."""
    rnd = random.Random(7)
    out = []
    for t in range(ticks):
        for s in sensors:
            v = value_of(t, s) + rnd.uniform(-0.4, 0.4)
            out.append(eng.ingest(m._obs(s, "water_level", "z", v, "cm", t, t)))
    return out


def test_slow_drift_is_caught_while_each_step_is_tiny():
    # WL-03 creeps up 0.5 cm per second: no range, rate or cross-sensor check fires for a long time
    eng = GuardianEngine(use_obs_time=True)
    vs = feed(eng, 120, lambda t, s: 40 + (0.5 * t if s == "WL-03" else 0))
    first = next(v for v in vs if v.device_id == "WL-03" and v.verdict == "UNTRUSTED")
    assert "SLOW_DRIFT" in first.reason_codes
    assert eng.trust_of("WL-03")["quarantined"]
    assert all(not eng.trust_of(s)["quarantined"] for s in ("WL-01", "WL-02", "WL-04", "WL-05"))


def test_without_drift_check_the_same_attack_goes_unnoticed_for_longer():
    cfg_off = None
    eng_on, eng_off = GuardianEngine(use_obs_time=True), GuardianEngine(use_obs_time=True)
    eng_off.cfg["drift"]["enabled"] = False
    f = lambda t, s: 40 + (0.5 * t if s == "WL-03" else 0)
    on = next(v for v in feed(eng_on, 120, f) if v.device_id == "WL-03" and v.verdict == "UNTRUSTED")
    off = next(v for v in feed(eng_off, 120, f) if v.device_id == "WL-03" and v.verdict == "UNTRUSTED")
    assert on.issued_at < off.issued_at


def test_a_real_surge_that_moves_every_sensor_is_not_drift():
    eng = GuardianEngine(use_obs_time=True)
    vs = feed(eng, 120, lambda t, s: 40 + 0.5 * t)       # everybody rises together
    assert all(v.verdict == "TRUSTED" for v in vs if "IMPLAUSIBLE_RATE" not in v.reason_codes)
    assert not any("SLOW_DRIFT" in v.reason_codes for v in vs)


def test_trust_state_survives_a_restart(tmp_path):
    eng = GuardianEngine(use_obs_time=True)
    feed(eng, 60, lambda t, s: 40 + (3.0 * t if s == "WL-03" else 0))
    assert eng.trust_of("WL-03")["quarantined"]
    path = str(tmp_path / "state.json")
    save_state(eng, path)
    if os.name != "nt":                                   # Windows has no POSIX permission bits
        assert oct((tmp_path / "state.json").stat().st_mode & 0o777) == "0o600"
    fresh = GuardianEngine(use_obs_time=True)
    assert load_state(fresh, path) == 5
    assert fresh.trust_of("WL-03")["quarantined"]
    # a fresh stream is accepted (ordering state is not restored), and the quarantined sensor stays distrusted
    v = fresh.ingest(m._obs("WL-03", "water_level", "z", 40, "cm", 0, 0))
    assert v.verdict == "UNTRUSTED" and "DEVICE_QUARANTINED" in v.reason_codes
    assert fresh.ingest(m._obs("WL-01", "water_level", "z", 40, "cm", 0, 0)).verdict == "TRUSTED"


def test_a_hostile_state_file_is_rejected_or_skipped(tmp_path):
    p = tmp_path / "state.json"
    for junk in ["not json", "[]", json.dumps({"version": 2}), json.dumps({"version": 1, "devices": []})]:
        p.write_text(junk)
        assert load_state(GuardianEngine(use_obs_time=True), str(p)) == 0
    p.write_text(json.dumps({"version": 1, "devices": {
        "../etc/passwd": {"sensor_type": "x", "zone": "z", "trust": 1, "n_seen": 1},
        "WL-09": {"sensor_type": "water_level", "zone": "z", "trust": 7, "n_seen": 1},
        "WL-08": {"sensor_type": "water_level", "zone": "z", "trust": 0.5, "n_seen": 1, "clean": ["x"]},
        "WL-07": {"sensor_type": "water_level", "zone": "z", "trust": 0.5, "n_seen": 3, "clean": [40.0]}}}))
    eng = GuardianEngine(use_obs_time=True)
    assert load_state(eng, str(p)) == 1 and list(eng.devices) == ["WL-07"]


def test_a_missing_state_file_is_fine(tmp_path):
    assert load_state(GuardianEngine(use_obs_time=True), str(tmp_path / "none.json")) == 0


# ---- per-caller tokens ------------------------------------------------------------------------------------------
def app(monkeypatch, brain="", twin=""):
    monkeypatch.setenv("GUARDIAN_TOKEN_BRAIN", brain)
    monkeypatch.setenv("GUARDIAN_TOKEN_TWIN", twin)
    return TestClient(create_app(GuardianEngine(use_obs_time=True), token="admin-token-0123456789abcdef"))


def hdr(t):
    return {"Authorization": f"Bearer {t}", "Content-Type": "application/json"}


def test_brain_token_can_read_but_not_send_or_restore(monkeypatch):
    c = app(monkeypatch, brain="brain-token-0123456789", twin="twin-token-0123456789a")
    o = m._obs("WL-01", "water_level", "z", 40, "cm", 0, 0)
    assert c.post("/observations", content=o.model_dump_json(), headers=hdr("twin-token-0123456789a")).status_code == 200
    assert c.get(f"/verdicts/{o.observation_id}", headers=hdr("brain-token-0123456789")).status_code == 200
    assert c.get("/devices/WL-01/trust", headers=hdr("brain-token-0123456789")).status_code == 200
    assert c.post("/observations", content=o.model_dump_json(), headers=hdr("brain-token-0123456789")).status_code == 401
    assert c.post("/devices/WL-01/restore", headers=hdr("brain-token-0123456789")).status_code == 401
    assert c.get("/timeline/events", headers=hdr("brain-token-0123456789")).status_code in (401, 404)


def test_twin_token_cannot_read_verdicts_or_restore(monkeypatch):
    c = app(monkeypatch, brain="brain-token-0123456789", twin="twin-token-0123456789a")
    t = hdr("twin-token-0123456789a")
    assert c.get("/verdicts/x", headers=t).status_code == 401
    assert c.post("/devices/WL-01/restore", headers=t).status_code == 401


def test_admin_token_still_does_everything(monkeypatch):
    c = app(monkeypatch, brain="brain-token-0123456789", twin="twin-token-0123456789a")
    o = m._obs("WL-01", "water_level", "z", 40, "cm", 0, 0)
    a = hdr("admin-token-0123456789abcdef")
    assert c.post("/observations", content=o.model_dump_json(), headers=a).status_code == 200
    assert c.get(f"/verdicts/{o.observation_id}", headers=a).status_code == 200


def test_weak_or_duplicate_scoped_tokens_refuse_to_start(monkeypatch):
    import pytest
    for brain, twin in [("short", ""), ("admin-token-0123456789abcdef", ""), ("same-token-0123456789a", "same-token-0123456789a")]:
        monkeypatch.setenv("GUARDIAN_TOKEN_BRAIN", brain)
        monkeypatch.setenv("GUARDIAN_TOKEN_TWIN", twin)
        with pytest.raises(RuntimeError):
            create_app(GuardianEngine(use_obs_time=True), token="admin-token-0123456789abcdef")


def test_timeline_token_can_post_events_only(monkeypatch):
    from guardian.timeline import TimelineStore
    monkeypatch.setenv("GUARDIAN_TOKEN_BRAIN", "")
    monkeypatch.setenv("GUARDIAN_TOKEN_TWIN", "")
    monkeypatch.setenv("GUARDIAN_TOKEN_TIMELINE", "timeline-token-0123456789")
    c = TestClient(create_app(GuardianEngine(use_obs_time=True), token="admin-token-0123456789abcdef", timeline=TimelineStore()))
    t = hdr("timeline-token-0123456789")
    ev = '{"layer":"brain","event":"brain_decision","observation_id":"x"}'
    assert c.post("/timeline/events", content=ev, headers=t).status_code == 200
    assert c.get("/timeline/events", headers=t).status_code == 401           # cannot read the events back
    assert c.delete("/timeline/events", headers=t).status_code == 401        # cannot clear them
    assert c.post("/observations", content="{}", headers=t).status_code == 401
    assert c.post("/devices/WL-01/restore", headers=t).status_code == 401


def test_all_scoped_tokens_must_differ(monkeypatch):
    import pytest
    monkeypatch.setenv("GUARDIAN_TOKEN_BRAIN", "same-token-0123456789a")
    monkeypatch.setenv("GUARDIAN_TOKEN_TWIN", "")
    monkeypatch.setenv("GUARDIAN_TOKEN_TIMELINE", "same-token-0123456789a")
    with pytest.raises(RuntimeError):
        create_app(GuardianEngine(use_obs_time=True), token="admin-token-0123456789abcdef")
