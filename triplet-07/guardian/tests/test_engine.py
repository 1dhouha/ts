from datetime import timedelta

from mocks import mock_twin as m
from scenarios.run import first_untrusted, run


def test_t1_calm_day_has_no_false_alarms(tmp_path):
    r = run("t1", str(tmp_path))
    assert all(v.verdict == "TRUSTED" for v in r["verdicts"])
    assert r["twin"].calls == []


def test_t2_spoofed_sensor_detected_within_10s_and_quarantined(tmp_path):
    r = run("t2", str(tmp_path))
    v = first_untrusted(r["verdicts"], "WL-03")
    assert v is not None
    assert (v.issued_at - m.T0).total_seconds() - 10 <= 10          # onset at t=10 s
    assert ("quarantine_device", "WL-03") == r["twin"].calls[0][:2]
    # the real flood readings are never treated as an attack
    assert first_untrusted(r["verdicts"], "WL-02") is None
    assert first_untrusted(r["verdicts"], "WL-04") is None
    # a later reading from the quarantined sensor is refused with an explainable reason
    last = [x for x in r["verdicts"] if x.device_id == "WL-03"][-1]
    assert last.verdict == "UNTRUSTED" and "DEVICE_QUARANTINED" in last.reason_codes


def test_t3_frozen_meter_flagged(tmp_path):
    r = run("t3", str(tmp_path))
    v = first_untrusted(r["verdicts"], "PL-01")
    assert v is not None and "FROZEN_VALUE" in v.reason_codes
    assert (v.issued_at - m.T0).total_seconds() - 15 <= 10


def test_t6_replays_rejected_without_hurting_healthy_sensor(tmp_path):
    r = run("t6", str(tmp_path))
    replays = [v for v in r["verdicts"] if v.observation_id.endswith("-R")]
    assert len(replays) == 5 and all(v.verdict == "UNTRUSTED" for v in replays)
    assert all("REPLAY_OR_OUT_OF_ORDER" in v.reason_codes for v in replays)
    assert r["engine"].trust_of("WL-02")["trust_score"] == 1.0
    assert not r["twin"].calls


def test_out_of_range_is_hard_untrusted():
    from guardian.engine import GuardianEngine
    e = GuardianEngine(use_obs_time=True)
    o = m._obs("WL-01", "water_level", "z", 9999, "cm", 0, 0)
    v = e.ingest(o)
    assert v.verdict == "UNTRUSTED" and v.recommended_action == "ISOLATE"


def test_idempotent_verdict():
    from guardian.engine import GuardianEngine
    e = GuardianEngine(use_obs_time=True)
    o = m._obs("WL-01", "water_level", "z", 40, "cm", 0, 0)
    assert e.ingest(o).verdict_id == e.ingest(o).verdict_id


def test_t2b_noisy_spoof_caught_by_cross_sensor_check_alone(tmp_path):
    r = run("t2b", str(tmp_path))
    v = first_untrusted(r["verdicts"], "WL-03")
    assert v is not None and "CROSS_SENSOR_CONTRADICTION" in v.reason_codes
    assert "FROZEN_VALUE" not in v.reason_codes
    assert (v.issued_at - m.T0).total_seconds() - 10 <= 10
    assert first_untrusted(r["verdicts"], "WL-02") is None
    assert r["twin"].calls and r["twin"].calls[0][1] == "WL-03"
