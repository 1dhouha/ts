"""ASGI routes for the operator dashboard, sharing the host application's city."""
import json

from fastapi import APIRouter, Request
from starlette.responses import Response

from city_live.records import LiveError, exact_keys
from .frames import capture_operator_frame, read_reported_frame
from .security import RateLimit, Sessions
from .server import ASSETS, ERROR_STATUS, WEB, command_error_code


def create_dashboard_router(owner, secret, *, secure_cookies=False):
    """Create same-origin dashboard endpoints bound to an existing LiveCity."""
    sessions = Sessions(secret)
    rate = RateLimit()
    router = APIRouter()

    def reply(value, status=200, *, mime='application/json', headers=None):
        content = value if isinstance(value, bytes) else json.dumps(value, allow_nan=False)
        security = {
            'Cache-Control': 'no-store',
            'Referrer-Policy': 'no-referrer',
            'X-Content-Type-Options': 'nosniff',
            'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'",
        }
        security.update(headers or {})
        return Response(content, status_code=status, media_type=mime, headers=security)

    def error(status, code):
        return reply({'ok': False, 'error': {'code': code}}, status)

    async def guard(request, *, post=False):
        hosts = request.headers.getlist('host')
        origins = request.headers.getlist('origin')
        origin = f'{request.url.scheme}://{hosts[0]}' if len(hosts) == 1 else ''
        if (len(hosts) != 1 or len(origins) > 1 or
                (origins and origins != [origin]) or
                (post and origins != [origin]) or
                request.headers.get('sec-fetch-site') not in (None, 'same-origin', 'none')):
            return error(403, 'ORIGIN_REJECTED')
        if not rate.allow():
            return error(429, 'RATE_LIMITED')
        if request.url.query or len(request.url.path) > 128:
            return error(400, 'INVALID_ARGUMENT')
        if post:
            lengths = request.headers.getlist('content-length')
            if (request.headers.get('transfer-encoding') or len(lengths) != 1 or
                    not lengths[0].isascii() or not lengths[0].isdigit()):
                return error(400, 'INVALID_ARGUMENT')
            try:
                length = int(lengths[0])
            except ValueError:
                return error(400, 'INVALID_ARGUMENT')
            if length > 4096:
                return error(413, 'BODY_TOO_LARGE')
            if request.headers.get('content-type') != 'application/json':
                return error(415, 'CONTENT_TYPE')
        return None

    async def body(request):
        raw = await request.body()
        if len(raw) != int(request.headers['content-length']):
            raise LiveError('INVALID_ARGUMENT')
        value = json.loads(raw, object_pairs_hook=_strict_object)
        if type(value) is not dict:
            raise LiveError('INVALID_ARGUMENT')
        return value

    @router.get('/api/report/frame', include_in_schema=False)
    async def report_frame(request: Request):
        rejected = await guard(request)
        if rejected:
            return rejected
        return reply({'ok': True, 'data': read_reported_frame(owner.public)})

    @router.get('/api/operator/frame', include_in_schema=False)
    async def operator_frame(request: Request):
        rejected = await guard(request)
        if rejected:
            return rejected
        try:
            sessions.require(request.headers.get('cookie'))
            return reply({'ok': True, 'data': capture_operator_frame(owner)})
        except LiveError as exc:
            return error(ERROR_STATUS.get(exc.code, 401), exc.code)

    @router.post('/api/session', include_in_schema=False)
    async def login(request: Request):
        rejected = await guard(request, post=True)
        if rejected:
            return rejected
        try:
            payload = await body(request)
            exact_keys(payload, ('secret',))
            token = sessions.login(payload['secret'])
            secure = '; Secure' if secure_cookies or request.url.scheme == 'https' else ''
            cookie = f'city_session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=3600{secure}'
            return reply({'ok': True, 'data': {}}, headers={'Set-Cookie': cookie})
        except LiveError as exc:
            status = ERROR_STATUS.get(exc.code, 401)
            return error(status, exc.code)
        except (ValueError, TypeError, UnicodeError, RecursionError, KeyError):
            return error(400, 'INVALID_ARGUMENT')

    @router.post('/api/logout', include_in_schema=False)
    async def logout(request: Request):
        rejected = await guard(request, post=True)
        if rejected:
            return rejected
        try:
            payload = await body(request)
            exact_keys(payload, ())
            sessions.logout(request.headers.get('cookie'))
            secure = '; Secure' if secure_cookies or request.url.scheme == 'https' else ''
            cookie = f'city_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0{secure}'
            return reply({'ok': True, 'data': {}}, headers={'Set-Cookie': cookie})
        except LiveError as exc:
            return error(ERROR_STATUS.get(exc.code, 401), exc.code)
        except (ValueError, TypeError, UnicodeError, RecursionError):
            return error(400, 'INVALID_ARGUMENT')

    @router.post('/api/operator/command', include_in_schema=False)
    async def operator_command(request: Request):
        rejected = await guard(request, post=True)
        if rejected:
            return rejected
        try:
            sessions.require(request.headers.get('cookie'))
            payload = await body(request)
            result = owner.operator_request(payload)
            command_result = result.get('result')
            if type(command_result) is dict and command_result.get('status') == 'FAILED':
                code = command_error_code(command_result['error'])
                return error(ERROR_STATUS.get(code, 400), code)
            return reply({'ok': True, 'data': result})
        except LiveError as exc:
            code = command_error_code(exc.code)
            return error(ERROR_STATUS.get(code, 401), code)
        except (ValueError, TypeError, UnicodeError, RecursionError):
            return error(400, 'INVALID_ARGUMENT')

    for path, (name, mime) in ASSETS.items():
        async def asset(request: Request, *, filename=name, content_type=mime):
            rejected = await guard(request)
            if rejected:
                return rejected
            try:
                return reply((WEB / filename).read_bytes(), mime=content_type)
            except OSError:
                return error(404, 'NOT_FOUND')
        router.add_api_route(path, asset, methods=['GET'], include_in_schema=False)

    return router


def _strict_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('duplicate key')
        value[key] = item
    return value
