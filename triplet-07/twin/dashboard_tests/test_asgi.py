"""HTTP-level checks for the dashboard routes hosted by the shared ASGI app."""
from pathlib import Path
from unittest.mock import patch
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from city_dashboard.asgi import create_dashboard_router
from city_live.runtime import LiveCity


class DashboardAsgiTests(unittest.TestCase):
    def setUp(self):
        self.secret = "dashboard-asgi-tests-passphrase!"
        self.city = LiveCity("dashboard-asgi-test")
        self.app = FastAPI()
        self.app.include_router(create_dashboard_router(self.city, self.secret))
        self.client = TestClient(self.app, base_url="http://testserver")
        self.origin = "http://testserver"

    def tearDown(self):
        self.client.close()
        self.city.close()

    def login(self):
        response = self.client.post(
            "/api/session", json={"secret": self.secret}, headers={"Origin": self.origin}
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response

    def test_dashboard_assets_report_and_authenticated_operator_flow(self):
        for path in ("/", "/styles.css", "/app.js", "/api.js", "/map-layout.json", "/city-map.svg"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers["cache-control"], "no-store")

        report = self.client.get("/api/report/frame")
        self.assertEqual(report.status_code, 200)
        self.assertEqual(report.json()["data"]["visibility"], "REPORTED_ONLY")
        self.assertEqual(self.client.get("/api/operator/frame").status_code, 401)

        self.login()
        frame = self.client.get("/api/operator/frame").json()["data"]
        self.assertEqual(frame["visibility"], "PRIVATE_OPERATOR_ONLY")
        step = self.client.post(
            "/api/operator/command",
            json={"command_id": "asgi-step", "run_id": frame["run_id"],
                  "expected_revision": frame["revision"], "kind": "step", "args": {}},
            headers={"Origin": self.origin},
        )
        self.assertEqual(step.status_code, 200, step.text)
        self.assertEqual(step.json()["data"]["state"]["sim_tick"], 1)

        with patch.object(self.city, "operator_request", return_value={
            "result": {"status": "FAILED", "error": "INVALID_ARGUMENT"},
        }):
            failed = self.client.post(
                "/api/operator/command", json={}, headers={"Origin": self.origin}
            )
        self.assertEqual(failed.status_code, 400)
        logout = self.client.post("/api/logout", json={}, headers={"Origin": self.origin})
        self.assertEqual(logout.status_code, 200)
        self.assertEqual(self.client.get("/api/operator/frame").status_code, 401)

    def test_dashboard_rejects_invalid_origins_payloads_and_limits(self):
        self.assertEqual(self.client.get("/api/report/frame?extra=1").status_code, 400)
        self.assertEqual(
            self.client.post("/api/session", json={"secret": self.secret},
                             headers={"Origin": "https://attacker.example"}).status_code,
            403,
        )
        self.assertEqual(
            self.client.post("/api/session", json={"secret": self.secret},
                             headers={"Origin": self.origin, "Sec-Fetch-Site": "cross-site"}).status_code,
            403,
        )
        self.assertEqual(self.client.post("/api/session", content="[]",
                                          headers={"Origin": self.origin,
                                                   "Content-Type": "application/json"}).status_code, 400)
        self.assertEqual(self.client.post("/api/session", content="{bad",
                                          headers={"Origin": self.origin,
                                                   "Content-Type": "application/json"}).status_code, 400)
        self.assertEqual(self.client.post("/api/session", json={"secret": self.secret, "role": "operator"},
                                          headers={"Origin": self.origin}).status_code, 400)
        self.assertEqual(self.client.post("/api/session", json={},
                                          headers={"Origin": self.origin}).status_code, 400)
        self.assertEqual(self.client.post("/api/session", json={"secret": "wrong"},
                                          headers={"Origin": self.origin}).status_code, 401)
        self.assertEqual(self.client.post("/api/session", content="x" * 4097,
                                          headers={"Origin": self.origin,
                                                   "Content-Type": "application/json"}).status_code, 413)
        self.assertEqual(self.client.post("/api/session", content="{}",
                                          headers={"Origin": self.origin,
                                                   "Content-Type": "text/plain"}).status_code, 415)
        self.assertEqual(self.client.post("/api/logout", json={},
                                          headers={"Origin": self.origin}).status_code, 401)
        self.assertEqual(self.client.post("/api/operator/command", json={},
                                          headers={"Origin": self.origin}).status_code, 401)

        with patch("city_dashboard.asgi.RateLimit.allow", return_value=False):
            self.assertEqual(self.client.get("/api/report/frame").status_code, 429)
        with patch("city_dashboard.asgi.WEB", Path("missing-dashboard-assets")):
            self.assertEqual(self.client.get("/styles.css").status_code, 404)

    def test_secure_cookie_for_https_and_closed_city_error(self):
        secure_app = FastAPI()
        secure_app.include_router(create_dashboard_router(self.city, self.secret, secure_cookies=True))
        with TestClient(secure_app, base_url="https://testserver") as client:
            login = client.post("/api/session", json={"secret": self.secret},
                                headers={"Origin": "https://testserver"})
            self.assertEqual(login.status_code, 200)
            self.assertIn("secure", login.headers["set-cookie"].casefold())

        self.city.close()
        closed = self.client.get("/api/report/frame")
        self.assertNotEqual(closed.status_code, 500)


if __name__ == "__main__":
    unittest.main()
