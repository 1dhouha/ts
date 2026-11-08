"""Bounded loopback HTTP bridge. Explicit authenticated operator allowlist; no generic RPC."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
from shutil import copyfileobj
from threading import BoundedSemaphore, Thread

from city_live.records import LiveError, exact_keys, integer
from .frames import capture_operator_frame, read_reported_frame
from .security import RateLimit, Sessions
from twin_interface.health import liveness_report, readiness_http_status, readiness_report

WEB = Path(__file__).with_name('web')
ASSETS = {'/': ('index.html', 'text/html; charset=utf-8'),
          **{f'/{name}': (name, 'text/javascript; charset=utf-8')
             for name in ('api.js', 'store.js', 'app.js', 'map.js', 'panels.js', 'controls.js', 'history.js', 'sensors.js', 'timeline.js')},
          '/city3d-loader.js': ('city3d-loader.js', 'text/javascript; charset=utf-8'),
          '/styles.css': ('styles.css', 'text/css; charset=utf-8')}
ASSETS.update({'/map-layout.json': ('map-layout.json', 'application/json'),
               '/city-map.svg': ('city-map.svg', 'image/svg+xml'),
               '/redesign.css': ('redesign.css', 'text/css; charset=utf-8'),
               '/3d/assets/city3d-viewer.js': ('3d/assets/city3d-viewer.js', 'text/javascript; charset=utf-8'),
               '/3d/assets/city3d.css': ('3d/assets/city3d.css', 'text/css; charset=utf-8'),
               '/3d/models/city-cs-challenge.glb': ('3d/models/city-cs-challenge.glb', 'model/gltf-binary')})
ERROR_STATUS = {'NOT_AUTHORIZED': 401, 'OLD_RUN': 409, 'HISTORY_GAP': 409,
                'CURSOR_INVALID': 400, 'CLOSED': 503, 'RATE_LIMITED': 429,
                'SESSION_LIMIT': 429, 'STATE_CONFLICT': 409, 'IDENTITY_CONFLICT': 409,
                'INVALID_STATE': 409, 'DEVICE_UNAVAILABLE': 409,
                'SCENARIO_ACTIVE': 409,
                'IDEMPOTENCY_CONFLICT': 409,
                'RECOVERY_REQUIRED': 409, 'PERSISTENCE_FAILURE': 503,
                'DEVICE_NOT_FOUND': 404, 'GUARDIAN_RESTRICTED': 403,
                'INVALID_ARGUMENT': 400, 'UNSUPPORTED_COMMAND': 400,
                'UNSUPPORTED_DEVICE': 404, 'COMMAND_BUDGET_EXHAUSTED': 429}


def command_error_code(code):
    return {
        'UNSUPPORTED_DEVICE': 'DEVICE_NOT_FOUND',
        'INVALID_ARGUMENT': 'INVALID_PARAM',
        'UNSUPPORTED_COMMAND': 'INVALID_PARAM',
    }.get(code, code)


def strict_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise LiveError('INVALID_ARGUMENT')
        value = {**value, key: item}
    return value


class ReportFeed:
    """The report route's dependency has no owner or diagnostic capability."""
    def __init__(self, public_reader):
        self._reader = public_reader

    def frame(self):
        return read_reported_frame(self._reader)


class BoundedServer(ThreadingHTTPServer):
    daemon_threads = False
    block_on_close = True
    allow_reuse_address = False
    request_queue_size = 8

    def __init__(self, address, handler):
        self._slots = BoundedSemaphore(8)
        super().__init__(address, handler)

    def get_request(self):
        conn, address = super().get_request()
        conn.settimeout(2)
        return conn, address

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()

    def handle_error(self, request, client_address):
        # Do not put private payloads, credentials or request lines into logs.
        pass


class DashboardBridge:
    def __init__(self, owner, secret, *, port=8765):
        integer(port, 0, 65535)
        self._owner = owner
        self._reports = ReportFeed(owner.public)
        self._sessions = Sessions(secret)
        self._rate = RateLimit()
        self._http = BoundedServer(('127.0.0.1', port), self._handler())
        self.port = self._http.server_port
        self.url = f'http://127.0.0.1:{self.port}'
        self._thread = None
        self._closed = False

    def _handler(self):
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            server_version = 'LocalCity'
            sys_version = ''

            def log_message(self, *_args):
                pass

            def send_error(self, code, message=None, explain=None):
                self._reply(code, {'ok': False, 'error': {'code': 'HTTP_REJECTED'}})

            def _reply(self, code, value, *, mime='application/json', cookie=None):
                body = value if isinstance(value, bytes) else json.dumps(value, allow_nan=False).encode('utf-8')
                self.send_response(code)
                self.send_header('Content-Type', mime)
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Referrer-Policy', 'no-referrer')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self' blob:; img-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")
                self.send_header('Connection', 'close')
                if cookie:
                    self.send_header('Set-Cookie', cookie)
                self.end_headers()
                self.close_connection = True
                self.wfile.write(body)

            def _reply_file(self, path, mime):
                self.send_response(200)
                self.send_header('Content-Type', mime)
                self.send_header('Content-Length', str(path.stat().st_size))
                self.send_header('Cache-Control', 'public, max-age=3600')
                self.send_header('Referrer-Policy', 'no-referrer')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self' blob:; img-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")
                self.send_header('Connection', 'close')
                self.end_headers()
                self.close_connection = True
                with path.open('rb') as asset:
                    copyfileobj(asset, self.wfile, length=64 * 1024)

            def _error(self, code, name):
                self._reply(code, {'ok': False, 'error': {'code': name}})

            def _guard(self):
                hosts = self.headers.get_all('Host', [])
                origins = self.headers.get_all('Origin', [])
                if (hosts != [bridge.url[7:]] or len(origins) > 1 or
                    (origins and origins != [bridge.url]) or
                    self.headers.get('Sec-Fetch-Site') not in (None, 'same-origin', 'none') or
                    (self.command == 'POST' and origins != [bridge.url])):
                    self._error(403, 'ORIGIN_REJECTED')
                    return False
                if not bridge._rate.allow():
                    self._error(429, 'RATE_LIMITED')
                    return False
                if '?' in self.path or len(self.path) > 128:
                    self._error(400, 'INVALID_ARGUMENT')
                    return False
                return True

            def do_GET(self):
                self._dispatch()

            def do_POST(self):
                self._dispatch()

            def do_PUT(self):
                self._error(405, 'METHOD_NOT_ALLOWED')

            do_DELETE = do_PATCH = do_OPTIONS = do_HEAD = do_PUT

            def _body(self):
                lengths = self.headers.get_all('Content-Length', [])
                if self.headers.get('Transfer-Encoding') or len(lengths) != 1 or not lengths[0].isdigit():
                    raise LiveError('INVALID_ARGUMENT')
                length = int(lengths[0])
                if length > 4096:
                    self._error(413, 'BODY_TOO_LARGE')
                    return None
                if self.headers.get('Content-Type') != 'application/json':
                    self._error(415, 'CONTENT_TYPE')
                    return None
                raw = self.rfile.read(length)
                if len(raw) != length:
                    raise LiveError('INVALID_ARGUMENT')
                value = json.loads(raw, object_pairs_hook=strict_object)
                if type(value) is not dict:
                    raise LiveError('INVALID_ARGUMENT')
                return value

            def _dispatch(self):
                try:
                    if not self._guard():
                        return
                    body = self._body() if self.command == 'POST' else None
                    if self.command == 'POST' and body is None:
                        return
                    self._route(body)
                except LiveError as error:
                    code = command_error_code(error.code)
                    self._error(ERROR_STATUS.get(code, 400), code)
                except (ValueError, UnicodeError, RecursionError):
                    self._error(400, 'INVALID_ARGUMENT')
                except (OSError, TimeoutError):
                    self.close_connection = True
                except Exception:
                    self._error(500, 'INTERNAL_ERROR')

            def _route(self, body):
                if self.command == 'GET' and self.path in ASSETS:
                    name, mime = ASSETS[self.path]
                    path = WEB / name
                    if self.path == '/3d/models/city-cs-challenge.glb':
                        self._reply_file(path, mime)
                    else:
                        self._reply(200, path.read_bytes(), mime=mime)
                elif self.command == 'GET' and self.path in (
                    '/health', '/health/live', '/health/ready'
                ):
                    if self.path == '/health/live':
                        report = liveness_report('dashboard')
                        status = 200
                    else:
                        report = bridge.health_report()
                        status = readiness_http_status(report)
                    self._reply(status, report)
                elif self.command == 'GET' and self.path == '/api/report/frame':
                    self._reply(200, {'ok': True, 'data': bridge._reports.frame()})
                elif self.command == 'GET' and self.path == '/api/operator/frame':
                    bridge._sessions.require(self.headers.get('Cookie'))
                    self._reply(200, {'ok': True, 'data': capture_operator_frame(bridge._owner)})
                elif self.command == 'POST' and self.path == '/api/operator/command':
                    bridge._sessions.require(self.headers.get('Cookie'))
                    result = bridge._owner.operator_request(body)
                    command_result = result.get('result')
                    if (type(command_result) is dict and
                            command_result.get('status') == 'FAILED'):
                        code = command_error_code(command_result['error'])
                        self._error(ERROR_STATUS.get(code, 400), code)
                    else:
                        self._reply(200, {'ok': True, 'data': result})
                elif self.command == 'POST' and self.path == '/api/operator/scenario':
                    bridge._sessions.require(self.headers.get('Cookie'))
                    exact_keys(body, ('scenario_id',))
                    result = bridge._owner.start_b_scenario(body['scenario_id'])
                    self._reply(200, {'ok': True, 'data': result})
                elif self.command == 'POST' and self.path in ('/api/session', '/api/logout'):
                    self._session(body)
                else:
                    self._error(404, 'NOT_FOUND')

            def _session(self, body):
                secure = (
                    '; Secure'
                    if os.environ.get('TWIN_DASHBOARD_COOKIE_SECURE', '').strip().lower()
                    in ('1', 'true', 'yes', 'on')
                    or self.headers.get('X-Forwarded-Proto', '').casefold() == 'https'
                    else ''
                )
                if self.path == '/api/session':
                    exact_keys(body, ('secret',))
                    token = bridge._sessions.login(body['secret'])
                    cookie = f'city_session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=3600{secure}'
                else:
                    exact_keys(body, ())
                    bridge._sessions.logout(self.headers.get('Cookie'))
                    cookie = f'city_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0{secure}'
                self._reply(200, {'ok': True, 'data': {}}, cookie=cookie)

        return Handler

    @property
    def is_alive(self):
        return self._thread is not None and self._thread.is_alive()

    def health_report(self):
        return readiness_report(
            'dashboard',
            self._owner,
            required_checks={
                'dashboard_bridge': not self._closed and self.is_alive,
            },
        )

    def start(self):
        if self._closed or self._thread is not None:
            raise LiveError('INVALID_STATE')
        self._thread = Thread(target=self._http.serve_forever, kwargs={'poll_interval': .05}, name='city-dashboard-http')
        self._thread.start()

    def close(self, *, close_owner=True):
        if self._closed:
            return
        self._closed = True
        try:
            if self._thread is not None:
                self._http.shutdown()
                self._thread.join(timeout=5)
        finally:
            try:
                self._http.server_close()
            finally:
                if close_owner:
                    self._owner.close()
