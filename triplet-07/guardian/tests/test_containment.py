import threading

import httpx

from guardian.adapters.format import FormatAdapter
from guardian.adapters.protocol import HttpPollAdapter
from guardian.containment import HttpTwinClient
from guardian.engine import GuardianEngine, load_config
from guardian.eventlog import EventLog
from guardian.pipeline import poll_loop
from mocks import mock_twin as m


def client_with(handler, **kw):
    return HttpTwinClient("http://twin.local", token="ctok", paths={"quarantine_device": "/tools/quarantine_device"},
                          client=httpx.Client(transport=httpx.MockTransport(handler)), **kw)


def test_quarantine_posts_to_configured_path_with_separate_token():
    seen = []

    def h(req):
        seen.append((req.url.path, req.headers["authorization"], req.read()))
        return httpx.Response(200, json={"ok": True})
    c = client_with(h)
    assert c.quarantine_device("WL-03", "CROSS_SENSOR_CONTRADICTION") is True
    assert seen[0][0] == "/tools/quarantine_device" and seen[0][1] == "Bearer ctok"
    assert b"WL-03" in seen[0][2]


def test_server_error_is_retried_then_queued_and_flushed_later():
    state = {"up": False, "n": 0}

    def h(req):
        state["n"] += 1
        return httpx.Response(200 if state["up"] else 503)
    c = client_with(h, retries=1)
    assert c.quarantine_device("WL-03", "x") is False
    assert state["n"] == 2 and len(c.pending) == 1          # one try + one retry, then queued
    state["up"] = True
    assert c.flush_pending() == 1 and c.pending == []


def test_client_error_is_not_retried():
    n = {"n": 0}

    def h(req):
        n["n"] += 1
        return httpx.Response(404)
    c = client_with(h, retries=3)
    assert c.quarantine_device("WL-03", "x") is False and n["n"] == 1


def test_twin_down_never_raises_into_the_pipeline():
    def h(req):
        raise httpx.ConnectError("twin is down")
    c = client_with(h)
    assert c.quarantine_device("WL-03", "x") is False and len(c.pending) == 1


def test_flood_scenario_makes_exactly_one_quarantine_call_and_logs_it():
    posts = []

    def h(req):
        posts.append(req.read())
        return httpx.Response(200)
    log = EventLog()
    c = client_with(h, eventlog=log)
    eng = GuardianEngine(on_contain=lambda d, w: c.quarantine_device(d, w), eventlog=log, use_obs_time=True)
    for o in m.flood_spoof_noisy():
        eng.ingest(o)
    assert len(posts) == 1 and b"WL-03" in posts[0]
    assert any(e["event"] == "containment_call" and e["ok"] for e in log.events)


def test_containment_failure_still_leaves_the_device_quarantined_for_city_brain():
    def h(req):
        return httpx.Response(503)
    c = client_with(h, retries=0)
    eng = GuardianEngine(on_contain=lambda d, w: c.quarantine_device(d, w), use_obs_time=True)
    last = None
    for o in m.flood_spoof_noisy():
        v = eng.ingest(o)
        if o.device_id == "WL-03":
            last = v
    assert last.verdict == "UNTRUSTED" and last.recommended_action == "QUARANTINE" and len(c.pending) == 1


def test_pull_mode_poll_loop_feeds_the_same_core():
    stop = threading.Event()

    def h(req):
        stop.set()                                           # one round is enough for the test
        return httpx.Response(200, json=[{"device_id": "WL-01", "value": 40, "timestamp": "2026-10-16T10:00:00Z"}])
    src = HttpPollAdapter("http://twin.local/readings", client=httpx.Client(transport=httpx.MockTransport(h)))
    eng = GuardianEngine(use_obs_time=True)
    poll_loop(src, FormatAdapter(load_config()), eng, 0.01, stop)
    assert eng.trust_of("WL-01")["trust_score"] == 1.0 and len(eng.verdicts) == 1
