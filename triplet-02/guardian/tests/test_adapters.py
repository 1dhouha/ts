import csv
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from guardian.adapters.format import FormatAdapter, MalformedPayload
from guardian.adapters.protocol import CsvReplayAdapter, HttpPollAdapter
from guardian.api import create_app
from guardian.engine import GuardianEngine, load_config
from guardian.pipeline import run_source
from mocks import mock_twin as m

H = {"Authorization": "Bearer s3cret"}


def fmt(partner="default"):
    return FormatAdapter(load_config(), partner)


def test_camelcase_json_is_mapped_and_converted_to_cm():
    o = fmt("camelcase_demo").normalize(
        '{"sensorId":"WL-03","reading":0.3,"time":"2026-10-16T10:02:00Z","area":"canal-north"}')
    assert (o.device_id, o.value, o.unit, o.sensor_type, o.zone) == ("WL-03", 30.0, "cm", "water_level", "canal-north")
    assert o.observation_id.startswith("OBS-WL-03-")


def test_conversion_only_applies_to_configured_type():
    o = fmt("camelcase_demo").normalize({"sensorId": "PL-01", "reading": 50, "time": "2026-10-16T10:02:00Z"})
    assert o.sensor_type == "power_load" and o.value == 50.0


def test_xml_payload():
    x = "<observation><sensor_id>WL-01</sensor_id><value>41</value><timestamp>2026-10-16T10:02:00Z</timestamp></observation>"
    o = fmt().normalize(x)
    assert (o.device_id, o.value) == ("WL-01", 41.0)


def test_json_string_containing_xml():
    x = "<observation device_id='WL-01'><value>41</value><timestamp>2026-10-16T10:02:00Z</timestamp></observation>"
    assert fmt().normalize(json.dumps(x)).value == 41.0


def test_xml_entity_attack_is_refused():
    bad = '<?xml version="1.0"?><!DOCTYPE d [<!ENTITY x "boom">]><o><device_id>&x;</device_id><value>1</value><timestamp>2026-10-16T10:02:00Z</timestamp></o>'
    with pytest.raises(MalformedPayload) as e:
        fmt().normalize(bad)
    assert e.value.reason == "XML_ENTITY_FORBIDDEN"


def test_epoch_seconds_and_millis():
    a = fmt().normalize({"device_id": "WL-01", "value": 1, "timestamp": 1760608920})
    b = fmt().normalize({"device_id": "WL-01", "value": 1, "timestamp": 1760608920000})
    assert a.timestamp == b.timestamp


@pytest.mark.parametrize("raw,reason", [
    ({"device_id": "WL-01", "timestamp": "2026-10-16T10:02:00Z"}, "MISSING_FIELD"),
    ({"device_id": "WL-01", "value": "abc", "timestamp": "2026-10-16T10:02:00Z"}, "INVALID_VALUE"),
    ({"device_id": "WL-01", "value": 1, "timestamp": "yesterday"}, "INVALID_TIMESTAMP"),
    ("not json or xml", "UNSUPPORTED_FORMAT"),
    ("   ", "EMPTY_PAYLOAD"),
])
def test_unusable_payloads_raise_with_clear_reason(raw, reason):
    with pytest.raises(MalformedPayload) as e:
        fmt().normalize(raw)
    assert e.value.reason == reason


def test_malformed_payload_is_untrusted_and_does_not_punish_the_named_device():
    c = TestClient(create_app(GuardianEngine(use_obs_time=True), token="s3cret"))
    good = m._obs("WL-01", "water_level", "z", 40, "cm", 0, 0)
    c.post("/observations", content=good.model_dump_json(), headers={**H, "Content-Type": "application/json"})
    r = c.post("/ingest/default", content=b'{"device_id":"WL-01","timestamp":"2026-10-16T10:02:00Z"}', headers=H)
    assert r.status_code == 422
    body = r.json()
    assert body["error"]["code"] == "MISSING_FIELD"
    assert body["verdict"]["verdict"] == "UNTRUSTED" and "MALFORMED_PAYLOAD" in body["verdict"]["reason_codes"]
    assert c.get("/devices/WL-01/trust", headers=H).json()["trust_score"] == 1.0


def test_ingest_route_accepts_partner_format_and_returns_verdict():
    c = TestClient(create_app(GuardianEngine(use_obs_time=True), token="s3cret"))
    r = c.post("/ingest/camelcase_demo", headers=H,
               content=b'{"sensorId":"WL-03","reading":0.3,"time":"2026-10-16T10:02:00Z"}')
    assert r.status_code == 200 and r.json()["device_id"] == "WL-03" and r.json()["verdict"] == "TRUSTED"


def test_post_trust_verdict_is_an_alias_of_get_verdict():
    c = TestClient(create_app(GuardianEngine(use_obs_time=True), token="s3cret"))
    o = m._obs("WL-01", "water_level", "z", 40, "cm", 0, 0)
    c.post("/observations", content=o.model_dump_json(), headers={**H, "Content-Type": "application/json"})
    a = c.get(f"/verdicts/{o.observation_id}", headers=H).json()
    b = c.post("/trust/verdict", json={"observation_id": o.observation_id}, headers=H).json()
    assert a == b


def test_csv_replay_goes_through_the_whole_pipeline(tmp_path):
    p = tmp_path / "replay.csv"
    with open(p, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["device_id", "value", "timestamp", "zone", "seq"])
        for t in range(10):
            for d in ("WL-01", "WL-02", "WL-03", "WL-04"):
                w.writerow([d, 40 + (t % 3) * 0.5, f"2026-10-16T10:00:{t:02d}Z", "canal-north", t])
    eng = GuardianEngine(use_obs_time=True)
    out = run_source(CsvReplayAdapter(str(p)), fmt(), eng)
    assert len(out) == 40 and all(v.verdict == "TRUSTED" for v in out)


def test_http_poll_adapter_with_mock_transport():
    def handler(req):
        assert req.headers["authorization"] == "Bearer tok"
        return httpx.Response(200, json=[
            {"device_id": "WL-01", "value": 40, "timestamp": "2026-10-16T10:00:00Z"},
            {"device_id": "WL-02", "value": 41, "timestamp": "2026-10-16T10:00:00Z"}])
    src = HttpPollAdapter("http://twin.local/readings", token="tok",
                          client=httpx.Client(transport=httpx.MockTransport(handler)))
    eng = GuardianEngine(use_obs_time=True)
    out = run_source(src, fmt(), eng)
    assert [v.device_id for v in out] == ["WL-01", "WL-02"]
