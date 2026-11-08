"""Unified production ASGI application for the Person C Twin interfaces."""
from __future__ import annotations

from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qsl, urlparse
from uuid import uuid4

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.middleware import Middleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount
from starlette.types import ASGIApp, Receive, Scope, Send

from city_live.runtime import LiveCity
from twin_interface.auth import (
    CREDENTIAL_FIELD_NAMES,
    DEFAULT_TOKENS,
    DEV_TOKENS_ENV,
    Role,
    contains_credential_field,
    resolve_token,
)
from twin_interface.envelope import ErrorCode, error_envelope
from twin_interface.facade import TwinFacade
from twin_interface.health import liveness_report, readiness_http_status
from twin_interface.mcp_server.server import create_mcp_server
from twin_interface.rest.router import handle_rest_request
from twin_observability.event_store import EventStore
from twin_observability.scenario_log import ScenarioLogger
from mcp.server.transport_security import TransportSecuritySettings

MCP_MOUNT_PATH = "/mcp/"
MCP_PROTOCOL_PATH = "/"
DEFAULT_LOG_DIR = Path("logs")
LOCAL_ALLOWED_HOSTS = ("localhost", "127.0.0.1", "testserver")
class McpBearerAuthMiddleware:
    """Require a valid Bearer credential before dispatching any MCP HTTP request."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        query = scope.get("query_string", b"").decode("latin-1")
        if any(
            name.casefold() in CREDENTIAL_FIELD_NAMES
            for name, _ in parse_qsl(query, keep_blank_values=True)
        ):
            response = Response("Credentials are not accepted in URLs.", status_code=400)
            await response(scope, receive, send)
            return

        authorization_values = [
            value.decode("latin-1")
            for name, value in scope.get("headers", [])
            if name.lower() == b"authorization"
        ]
        if len(authorization_values) != 1:
            response = Response(
                "A valid Bearer credential is required.",
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
            await response(scope, receive, send)
            return
        authorization = authorization_values[0]
        scheme, separator, token = authorization.partition(" ")
        if not separator or scheme.casefold() != "bearer" or resolve_token(token.strip()) is None:
            response = Response(
                "A valid Bearer credential is required.",
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)


def _csv_setting(name: str) -> list[str]:
    return [value.strip() for value in os.environ.get(name, "").split(",") if value.strip()]


def _load_runtime_settings() -> tuple[str, int, list[str], list[str], Path]:
    token_values = {
        role: os.environ.get(f"TWIN_TOKEN_{role.upper()}", "")
        for role in Role.ORDERED
    }
    missing_roles = [
        role for role, token in token_values.items()
        if (
            len(token) < 32
            or len(set(token)) < 16
            or token != token.strip()
        )
    ]
    if missing_roles or os.environ.get(DEV_TOKENS_ENV, "").strip().lower() in {
        "1", "true", "yes", "on",
    }:
        raise RuntimeError(
            "Configure unique, non-development role tokens of at least 32 characters "
            "and 16 distinct characters "
            "for TWIN_TOKEN_BRAIN, TWIN_TOKEN_GUARDIAN, TWIN_TOKEN_OPERATOR, "
            "and TWIN_TOKEN_EVALUATOR before startup."
        )
    if len(set(token_values.values())) != len(Role.ORDERED):
        raise RuntimeError("Each Twin role must use a distinct token.")
    if any(token_values[role] == DEFAULT_TOKENS[role] for role in Role.ORDERED):
        raise RuntimeError("Built-in development tokens cannot be used by the ASGI application.")

    allowed_hosts = _csv_setting("TWIN_ALLOWED_HOSTS")
    if not allowed_hosts:
        raise RuntimeError("TWIN_ALLOWED_HOSTS must list the permitted request hostnames.")
    if any("*" in host or "://" in host or "/" in host for host in allowed_hosts):
        raise RuntimeError("TWIN_ALLOWED_HOSTS must contain exact hostnames without schemes or paths.")

    allowed_origins = _csv_setting("TWIN_ALLOWED_ORIGINS")
    for origin in allowed_origins:
        parsed = urlparse(origin)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.path not in {"", "/"}
            or parsed.params
            or parsed.query
            or parsed.fragment
            or parsed.username
            or parsed.password
        ):
            raise RuntimeError("TWIN_ALLOWED_ORIGINS must contain HTTP(S) origins without paths.")

    try:
        port = int(os.environ.get("PORT", "8000"))
    except ValueError as exc:
        raise RuntimeError("PORT must be an integer from 1 to 65535.") from exc
    if not 1 <= port <= 65535:
        raise RuntimeError("PORT must be an integer from 1 to 65535.")

    run_id = os.environ.get("TWIN_RUN_ID") or f"twin-{uuid4().hex}"
    log_dir = Path(os.environ.get("TWIN_LOG_DIR", str(DEFAULT_LOG_DIR)))
    return run_id, port, allowed_hosts, allowed_origins, log_dir


def _state_database_path(log_dir: Path) -> Path:
    return Path(os.environ.get("TWIN_STATE_DB_PATH", str(log_dir / "twin-state.sqlite3")))


def _build_rest_router() -> APIRouter:
    router = APIRouter()
    bearer_scheme = HTTPBearer(auto_error=False, scheme_name="BearerAuth")

    async def dispatch(
        request: Request,
        _bearer: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    ) -> JSONResponse:
        facade: TwinFacade = request.app.state.facade
        if any(
            name.casefold() in CREDENTIAL_FIELD_NAMES
            for name in request.query_params
        ):
            return JSONResponse(
                error_envelope(facade.run_id, ErrorCode.INVALID_PARAM,
                               "Credentials are not accepted in URLs."),
                status_code=400,
            )

        payload: dict[str, Any] | None = None
        if request.method == "POST":
            try:
                body = await request.body()
                payload = json.loads(body) if body else {}
            except (json.JSONDecodeError, UnicodeDecodeError):
                return JSONResponse(
                    error_envelope(facade.run_id, ErrorCode.INVALID_PARAM, "Request body must be valid JSON."),
                    status_code=400,
                )
            if type(payload) is not dict:
                return JSONResponse(
                    error_envelope(facade.run_id, ErrorCode.INVALID_PARAM, "Request body must be a JSON object."),
                    status_code=400,
                )
            if contains_credential_field(payload):
                return JSONResponse(
                    error_envelope(facade.run_id, ErrorCode.INVALID_PARAM,
                                   "Provide credentials through the Authorization header."),
                    status_code=400,
                )

        authorization_values = request.headers.getlist("authorization")
        authorization = authorization_values[0] if len(authorization_values) == 1 else ""
        scheme, separator, token = authorization.partition(" ")
        role = resolve_token(token.strip()) if separator and scheme.casefold() == "bearer" else None
        if role is None:
            return JSONResponse(
                error_envelope(facade.run_id, ErrorCode.NOT_AUTHORIZED,
                               "A valid Bearer credential is required."),
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )

        query = request.scope.get("query_string", b"").decode("latin-1")
        path = request.url.path + (f"?{query}" if query else "")
        status, response = handle_rest_request(
            facade,
            request.method,
            path,
            headers=request.headers,
            body=payload,
        )
        error = response.get("error")
        if isinstance(error, dict) and error.get("code") == ErrorCode.NOT_AUTHORIZED:
            status = 403
        return JSONResponse(response, status_code=status)

    for path, methods, name in (
        ("/api/v1/sensors", ["GET"], "get_sensor_readings"),
        ("/api/v1/state", ["GET"], "get_city_state"),
        ("/api/v1/events", ["GET"], "get_events_since"),
        ("/api/v1/timeline", ["GET"], "get_timeline"),
        ("/api/v1/commands", ["POST"], "send_actuator_command"),
        ("/api/v1/isolate", ["POST"], "isolate_device"),
        ("/api/v1/restore", ["POST"], "restore_device"),
    ):
        router.add_api_route(
            path,
            dispatch,
            methods=methods,
            name=name,
            operation_id=name,
        )
    return router


def create_app(
    *,
    city_factory: Callable[[str], LiveCity] = LiveCity,
    facade_factory: Callable[..., TwinFacade] = TwinFacade,
    mcp_factory: Callable[..., Any] = create_mcp_server,
) -> FastAPI:
    """Create the ASGI app; runtime services are constructed once per lifespan."""
    configured_hosts = _csv_setting("TWIN_ALLOWED_HOSTS") or list(LOCAL_ALLOWED_HOSTS)
    middleware = [Middleware(TrustedHostMiddleware, allowed_hosts=configured_hosts)]
    configured_origins = _csv_setting("TWIN_ALLOWED_ORIGINS")
    if configured_origins:
        middleware.append(Middleware(
            CORSMiddleware,
            allow_origins=configured_origins,
            allow_methods=["GET", "POST"],
            allow_headers=[
                "Authorization", "Content-Type", "MCP-Protocol-Version",
                "MCP-Session-Id", "Last-Event-ID",
            ],
            expose_headers=["MCP-Session-Id", "MCP-Protocol-Version"],
        ))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        city = None
        logger = None
        mount: Mount | None = None
        try:
            run_id, port, allowed_hosts, allowed_origins, log_dir = _load_runtime_settings()
            if city_factory is LiveCity:
                city = city_factory(run_id, persistence_path=_state_database_path(log_dir))
                run_id = city.status()["run_id"]
            else:
                city = city_factory(run_id)
            logger = ScenarioLogger("twin", run_id, log_dir=log_dir)
            event_store = EventStore()
            facade = facade_factory(city, event_store=event_store, scenario_logger=logger)
            transport_security = TransportSecuritySettings(
                enable_dns_rebinding_protection=True,
                allowed_hosts=[
                    value
                    for host in allowed_hosts
                    for value in (host, f"{host}:*")
                ],
                allowed_origins=allowed_origins,
            )
            mcp = mcp_factory(
                facade,
                host="0.0.0.0",
                port=port,
                streamable_http_path=MCP_PROTOCOL_PATH,
                transport_security=transport_security,
            )
            mcp_asgi = McpBearerAuthMiddleware(mcp.streamable_http_app())
            app.state.city = city
            app.state.facade = facade
            app.state.event_store = event_store
            app.state.scenario_logger = logger
            app.state.mcp_server = mcp
            app.state.mcp_asgi = mcp_asgi
            mount = Mount(MCP_MOUNT_PATH, app=mcp_asgi, name="mcp-streamable-http")
            app.router.routes.append(mount)
            city.start()
            async with mcp.session_manager.run():
                yield
        finally:
            try:
                if city is not None:
                    city.close()
            finally:
                try:
                    if logger is not None:
                        logger.close()
                finally:
                    if mount is not None and mount in app.router.routes:
                        app.router.routes.remove(mount)
                    for name in (
                        "city", "facade", "event_store", "scenario_logger",
                        "mcp_server", "mcp_asgi",
                    ):
                        if hasattr(app.state, name):
                            delattr(app.state, name)

    app = FastAPI(
        title="Person C Twin API",
        version="1.0.0",
        lifespan=lifespan,
        middleware=middleware,
    )
    app.include_router(_build_rest_router())

    @app.get("/health/live", include_in_schema=False)
    async def liveness():
        return liveness_report("twin_app")

    @app.get("/health/ready", include_in_schema=False)
    @app.get("/health", include_in_schema=False)
    async def readiness(request: Request):
        facade = getattr(request.app.state, "facade", None)
        if facade is None:
            report = {"status": "DOWN", "service": "twin_app",
                      "checks": {"facade": {"status": "DOWN"}, "mcp_service": {"status": "DOWN"}}}
        else:
            report = facade.health_report(
                "twin_app",
                service_checks={"mcp_service": getattr(request.app.state, "mcp_server", None) is not None},
            )
        return JSONResponse(report, status_code=readiness_http_status(report))

    return app


app = create_app()


def main() -> None:
    import uvicorn

    try:
        port = int(os.environ.get("PORT", "8000"))
    except ValueError as exc:
        raise SystemExit("PORT must be an integer from 1 to 65535.") from exc
    if not 1 <= port <= 65535:
        raise SystemExit("PORT must be an integer from 1 to 65535.")
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port,
        workers=1,
        proxy_headers=True,
        forwarded_allow_ips=os.environ.get("TWIN_TRUSTED_PROXY_IPS", "127.0.0.1"),
        access_log=False,
    )


if __name__ == "__main__":
    main()
