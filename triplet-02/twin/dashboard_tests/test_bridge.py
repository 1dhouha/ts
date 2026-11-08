import http.client
import json
import socket
import unittest

from city_live.runtime import LiveCity


class BridgeTests(unittest.TestCase):
    def setUp(self):
        from city_dashboard.server import DashboardBridge
        self.city = LiveCity('bridge')
        self.bridge = DashboardBridge(self.city, 'test-only-passphrase-983!', port=0)
        self.bridge.start()
        self.addCleanup(self.bridge.close)
        self.origin = self.bridge.url
        self.host = self.origin.removeprefix('http://')

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.bridge.port, timeout=3)
        self.addCleanup(conn.close)
        h = {'Origin': self.origin, **(headers or {})}
        if isinstance(body, dict):
            body = json.dumps(body)
            h.setdefault('Content-Type', 'application/json')
        conn.request(method, path, body, h)
        response = conn.getresponse()
        return response.status, dict(response.getheaders()), response.read()

    def login(self):
        status, headers, _ = self.request('POST', '/api/session', {'secret': 'test-only-passphrase-983!'})
        self.assertEqual(status, 200)
        self.assertIn('HttpOnly', headers['Set-Cookie'])
        self.assertIn('SameSite=Strict', headers['Set-Cookie'])
        return headers['Set-Cookie'].split(';')[0]

    def test_bridge_requires_authorization_and_never_accepts_role_toggle(self):
        self.assertEqual(self.request('GET', '/api/operator/frame')[0], 401)
        self.assertEqual(self.request('GET', '/api/operator/frame?role=operator')[0], 400)
        self.assertEqual(self.request('POST', '/api/session', {'secret': 'wrong'})[0], 401)
        cookie = self.login()
        status, headers, body = self.request('GET', '/api/operator/frame', headers={'Cookie': cookie})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)['data']['run_id'], 'bridge')
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertNotIn('Access-Control-Allow-Origin', headers)
        self.assertIn("frame-ancestors 'none'", headers['Content-Security-Policy'])
        self.assertEqual(self.request('POST', '/api/logout', {}, {'Cookie': cookie})[0], 200)
        self.assertEqual(self.request('GET', '/api/operator/frame', headers={'Cookie': cookie})[0], 401)

    def test_operator_scenario_route_uses_the_shared_city_and_exact_schema(self):
        cookie = self.login()
        headers = {'Cookie': cookie}

        self.assertEqual(
            self.request(
                'POST',
                '/api/operator/scenario',
                {'scenario_id': 'scn-02', 'role': 'operator'},
                headers,
            )[0],
            400,
        )
        self.assertEqual(
            self.request(
                'POST',
                '/api/operator/scenario',
                {'scenario_id': 'unknown'},
                headers,
            )[0],
            400,
        )
        status, _, body = self.request(
            'POST',
            '/api/operator/scenario',
            {'scenario_id': 'scn-02'},
            headers,
        )
        self.assertEqual(status, 200)
        scenario = json.loads(body)['data']
        self.assertEqual(scenario['scenario_id'], 'SCN-02')
        self.assertTrue(scenario['correlation_id'])
        self.assertEqual(self.city.capture_dashboard_frame()['scenario'], scenario)
        self.assertEqual(
            self.request(
                'POST',
                '/api/operator/scenario',
                {'scenario_id': 'scn-03'},
                headers,
            )[0],
            409,
        )
        self.city.reset('bridge-scn06')
        status, _, body = self.request(
            'POST',
            '/api/operator/scenario',
            {'scenario_id': 'scn-06'},
            headers,
        )
        self.assertEqual(status, 200)
        scenario = json.loads(body)['data']
        self.assertEqual(scenario['scenario_id'], 'SCN-06')
        self.assertTrue(scenario['correlation_id'])
        self.assertEqual(self.city.capture_dashboard_frame()['scenario'], scenario)

    def test_exact_origin_host_and_fetch_site(self):
        for headers in ({'Origin': 'https://evil.test'}, {'Host': 'evil.test'},
                        {'Host': 'localhost:' + str(self.bridge.port)},
                        {'Sec-Fetch-Site': 'cross-site'}, {'Origin': 'null'}):
            self.assertEqual(self.request('GET', '/api/report/frame', headers=headers)[0], 403)
        # A script outside this origin cannot establish a session without Origin.
        self.assertEqual(self.request('POST', '/api/session', {}, {'Origin': ''})[0], 403)

    def test_report_route_is_safe_even_with_operator_cookie(self):
        cookie = self.login()
        self.city.sensor_fault('DTN-WLV-01', 'FALSE_READING', {'value': 180})
        for _ in range(10): self.city.step()
        _, _, data = self.request('GET', '/api/report/frame', headers={'Cookie': cookie})
        frame = json.loads(data)['data']
        self.assertEqual(frame['visibility'], 'REPORTED_ONLY')
        for word in ('actual_value', 'delta', 'fault', 'diagnostics', 'revision', 'FALSE_READING'):
            self.assertNotIn(word, data.decode())
        r = next(r for r in frame['records'] if r['sensor_id'] == 'DTN-WLV-01')
        self.assertEqual(r['reported_value'], 180)
        self.assertEqual(self.request('POST', '/api/operator/command', {'method': 'reset'})[0], 401)

    def test_static_allowlist_bad_json_and_body_bounds(self):
        for path in ('/', '/api.js', '/store.js', '/app.js', '/styles.css'):
            self.assertEqual(self.request('GET', path)[0], 200)
        for path in ('/../runtime.py', '/%2e%2e/AGENTS.md', '/server.py'):
            self.assertEqual(self.request('GET', path)[0], 404)
        self.assertEqual(self.request('POST', '/api/session', '{bad', {'Content-Type': 'application/json'})[0], 400)
        self.assertEqual(self.request('POST', '/api/session', 'x' * 4097)[0], 413)
        self.assertEqual(self.request('POST', '/api/session', '{}', {'Content-Type': 'text/plain'})[0], 415)
        self.assertEqual(self.request('POST', '/api/session', {'secret': 'x', 'role': 'operator'})[0], 400)
        self.assertEqual(self.request('PUT', '/api/session')[0], 405)

    def test_multiple_clients_read_one_owner_and_close_releases_port(self):
        a = self.login()
        b = self.login()
        self.city.step()
        for cookie in (a, b):
            status, _, data = self.request('GET', '/api/operator/frame', headers={'Cookie': cookie})
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(data)['data']['sim_tick'], 1)
        port = self.bridge.port
        self.bridge.close()
        self.assertEqual(self.city.status()['state'], 'CLOSED')
        self.assertFalse(self.bridge.is_alive)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', port))

    def test_port_conflict_does_not_start_another_owner(self):
        from city_dashboard.server import DashboardBridge
        other = LiveCity('other')
        self.addCleanup(other.close)
        with self.assertRaises(OSError):
            DashboardBridge(other, 'test-only-passphrase-983!', port=self.bridge.port)
        self.assertEqual(other.status()['state'], 'READY')

    def test_closed_owner_returns_safe_error_and_restart_invalidates_cookie(self):
        from city_dashboard.server import DashboardBridge
        cookie = self.login()
        self.city.close()
        status, _, body = self.request('GET', '/api/operator/frame', headers={'Cookie': cookie})
        self.assertEqual(status, 503)
        self.assertEqual(json.loads(body), {'ok': False, 'error': {'code': 'CLOSED'}})
        other = LiveCity('restart')
        replacement = DashboardBridge(other, 'different-launch-passphrase!', port=0)
        self.addCleanup(replacement.close)
        replacement.start()
        conn = http.client.HTTPConnection('127.0.0.1', replacement.port, timeout=3)
        try:
            conn.request('GET', '/api/operator/frame', headers={'Cookie': cookie, 'Origin': replacement.url})
            response = conn.getresponse()
            self.assertEqual(response.status, 401)
            response.read()
        finally:
            conn.close()

    def test_raw_repeated_headers_invalid_lengths_and_framing_reject(self):
        def send(extra, body=b'{}', path='/api/session'):
            with socket.create_connection(('127.0.0.1', self.bridge.port),timeout=3) as sock:
                message=(f'POST {path} HTTP/1.1\r\nHost: {self.host}\r\nOrigin: {self.origin}\r\n'
                         'Content-Type: application/json\r\n'+extra+'\r\n').encode()+body
                sock.sendall(message)
                return sock.recv(2048).split(b'\r\n')[0]
        for headers, expected in [
            ('Host: evil.test\r\nContent-Length: 2\r\n',b'403'),
            ('Origin: https://evil.test\r\nContent-Length: 2\r\n',b'403'),
            ('Content-Length: 2\r\nContent-Length: 2\r\n',b'400'),
            ('Transfer-Encoding: chunked\r\nContent-Length: 2\r\n',b'400'),
            ('Content-Length: -1\r\n',b'400')]:
            self.assertIn(expected,send(headers))
        for value in (b'null',b'[]',b'"text"',b'\xff'):
            self.assertIn(b'400',send(f'Content-Length: {len(value)}\r\n',value))
