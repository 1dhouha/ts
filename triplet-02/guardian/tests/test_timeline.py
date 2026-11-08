import time

from fastapi.testclient import TestClient

from guardian.api import create_app
from guardian.engine import GuardianEngine
from guardian.timeline import TimelineStore

H = {"Authorization": "Bearer s3cret"}


def client(token="s3cret"):
    return TestClient(create_app(GuardianEngine(use_obs_time=True), token=token, timeline=TimelineStore()))


def test_page_is_served_and_lists_scenarios():
    c = client()
    p = c.get("/timeline")
    assert p.status_code == 200 and "City timeline" in p.text
    assert {s["id"] for s in c.get("/scenarios", headers=H).json()} >= {"t1", "t2", "t9"}


def test_partners_can_post_events_and_read_them_since_an_id():
    c = client()
    a = c.post("/timeline/events", headers=H, json={"layer": "twin", "event": "reading", "observation_id": "OBS-1", "value": 30})
    c.post("/timeline/events", headers=H, json={"layer": "brain", "event": "brain_decision", "action": "OPEN_VALVE"})
    got = c.get(f"/timeline/events?since={a.json()['id']}", headers=H).json()
    assert [e["layer"] for e in got["events"]] == ["brain"] and "timestamp" in got["events"][0]


def test_bad_events_and_missing_token_are_refused_clearly():
    c = client()
    assert c.post("/timeline/events", headers=H, json={"layer": "moon", "event": "x"}).status_code == 422
    assert c.post("/timeline/events", headers=H, content=b"not json").status_code == 422
    assert c.get("/timeline/events").status_code == 401


def test_scenario_button_runs_all_three_layers_into_the_timeline(tmp_path, monkeypatch):
    monkeypatch.setenv("GUARDIAN_LOG_DIR", str(tmp_path))
    c = client()
    assert c.post("/scenarios/t7/run?speed=0", headers=H).status_code == 202
    for _ in range(100):
        ev = c.get("/timeline/events?limit=5000", headers=H).json()["events"]
        if any(e["event"] == "scenario_end" for e in ev):
            break
        time.sleep(0.05)
    layers = {e["layer"] for e in ev}
    assert layers == {"twin", "guardian", "brain"}
    names = [e["event"] for e in ev]
    assert names.index("reading") < names.index("verdict") < names.index("brain_decision")
    assert c.post("/scenarios/nope/run", headers=H).status_code == 404


def test_root_redirects_to_the_timeline_page():
    r = client().get("/", follow_redirects=False)
    assert r.status_code in (302, 307) and r.headers["location"] == "/timeline"


def test_button_run_also_leaves_a_log_file(tmp_path, monkeypatch):
    monkeypatch.setenv("GUARDIAN_LOG_DIR", str(tmp_path))
    c = client()
    c.post("/scenarios/t1/run?speed=0", headers=H)
    for _ in range(100):
        if any(e["event"] == "scenario_end" for e in c.get("/timeline/events?limit=5000", headers=H).json()["events"]):
            break
        time.sleep(0.05)
    assert list(tmp_path.glob("t1-*.log"))


def test_favicon_is_quiet():
    assert client().get("/favicon.ico").status_code == 204
