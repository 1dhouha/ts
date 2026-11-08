from fastapi.testclient import TestClient

from guardian.api import create_app
from guardian.engine import GuardianEngine, load_config
from guardian.timeline import TimelineStore


def _client(monkeypatch):
    monkeypatch.setenv("GUARDIAN_DEMO", "1")
    return TestClient(create_app(GuardianEngine(load_config()), token=None, timeline=TimelineStore()))


def test_city_page_and_map_are_served(monkeypatch):
    c = _client(monkeypatch)
    page = c.get("/city")
    assert page.status_code == 200 and "GUARDIAN CORTEX" in page.text
    svg = c.get("/city/map.svg")
    assert svg.status_code == 200 and svg.headers["content-type"].startswith("image/svg+xml")


def test_every_scenario_device_has_a_map_position():
    """A sensor with no position would be invisible on the map."""
    import re
    from scenarios.run import SCENARIOS
    from guardian.eventlog import EventLog
    import io
    html = open("src/guardian/static/city.html", encoding="utf-8").read()
    known = set(re.findall(r"'([A-Z]{2,3}-[A-Z0-9-]+)':\[\d+", html))
    for sid in ("t1", "t3", "t5", "t13"):
        ids = {o.device_id for o in SCENARIOS[sid]["gen"]()}
        assert ids <= known, (sid, ids - known)
