from fastapi.testclient import TestClient

from guardian.api import create_app
from guardian.engine import GuardianEngine
from mocks import mock_twin as m


def client(token="s3cret"):
    return TestClient(create_app(GuardianEngine(use_obs_time=True), token=token))


H = {"Authorization": "Bearer s3cret"}


def test_post_then_get_verdict():
    c = client()
    o = m._obs("WL-01", "water_level", "z", 40, "cm", 0, 0)
    r = c.post("/observations", content=o.model_dump_json(), headers={**H, "Content-Type": "application/json"})
    assert r.status_code == 200 and r.json()["verdict"] == "TRUSTED"
    g = c.get(f"/verdicts/{o.observation_id}", headers=H)
    assert g.status_code == 200 and g.json()["observation_id"] == o.observation_id


def test_unknown_observation_is_404_with_error_body():
    r = client().get("/verdicts/nope", headers=H)
    assert r.status_code == 404 and r.json()["error"]["code"] == "OBSERVATION_UNKNOWN"


def test_missing_token_is_401():
    assert client().get("/devices/WL-01/trust").status_code == 401


def test_invalid_payload_is_rejected_clearly():
    r = client().post("/observations", json={"observation_id": "x"}, headers=H)
    assert r.status_code == 422
