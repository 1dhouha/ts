import hmac
import os
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, RedirectResponse, Response

from pydantic import BaseModel

from .adapters.format import FormatAdapter, MalformedPayload
from .engine import GuardianEngine
from .models import Observation, Verdict


class VerdictRequest(BaseModel):
    observation_id: str


class _TooLarge(Exception):
    pass


class BodyLimit:
    """Refuse oversized request bodies (413) before anything parses them, including bodies sent without a length."""

    def __init__(self, app, max_bytes: int):
        self.app, self.max = app, max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        started = False

        async def too_large():
            body = b'{"error":{"code":"PAYLOAD_TOO_LARGE","message":"request body exceeds the size limit"}}'
            await send({"type": "http.response.start", "status": 413,
                        "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
            await send({"type": "http.response.body", "body": body})

        for k, v in scope["headers"]:
            if k == b"content-length" and v.isdigit() and int(v) > self.max:
                return await too_large()
        seen = 0

        async def limited_receive():
            nonlocal seen
            msg = await receive()
            if msg["type"] == "http.request":
                seen += len(msg.get("body", b""))
                if seen > self.max:
                    raise _TooLarge()
            return msg

        async def tracking_send(msg):
            nonlocal started
            started = started or msg["type"] == "http.response.start"
            await send(msg)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except _TooLarge:
            if not started:
                await too_large()


def create_app(engine: GuardianEngine, token: Optional[str] = None, timeline=None,
               allow_open: Optional[bool] = None, extra_health=None) -> FastAPI:
    """Auth fails CLOSED: with no token the app refuses to start, unless demo mode is explicitly on
    (allow_open=True or GUARDIAN_DEMO=1). A forgotten variable must never leave the API open."""
    token = token if token is not None else os.getenv(engine.cfg["auth"]["token_env"], "")
    if allow_open is None:
        allow_open = os.getenv("GUARDIAN_DEMO", "") not in ("", "0", "false")
    if not token and not allow_open:
        raise RuntimeError(
            f"{engine.cfg['auth']['token_env']} is not set. Set it to a long random value, "
            "or set GUARDIAN_DEMO=1 for a local demo with NO authentication.")
    if not token:
        engine.log.emit("auth_disabled", note="demo mode: the API accepts unauthenticated requests")
    app = FastAPI(title="Guardian", version="0.2")
    app.add_middleware(BodyLimit, max_bytes=engine.cfg.get("security", {}).get("max_body_bytes", 65536))

    # Separate callers, separate rights. GUARDIAN_TOKEN can do everything (admin). Optional narrower tokens:
    #   GUARDIAN_TOKEN_BRAIN reads verdicts and trust only, GUARDIAN_TOKEN_TWIN sends readings only.
    # Revoking one caller = removing its variable and restarting. A token must be unique and at least 16 characters.
    #   GUARDIAN_TOKEN_TIMELINE writes timeline events only (so a partner can appear on the shared screen without the admin token).
    scoped = {"read": os.getenv("GUARDIAN_TOKEN_BRAIN", ""), "ingest": os.getenv("GUARDIAN_TOKEN_TWIN", ""),
              "timeline": os.getenv("GUARDIAN_TOKEN_TIMELINE", "")}
    for scope, tok in scoped.items():
        if tok and (len(tok) < 16 or tok == token):
            raise RuntimeError(f"the {scope} token must be at least 16 characters and different from GUARDIAN_TOKEN")
    given = [t for t in scoped.values() if t]
    if len(given) != len(set(given)):
        raise RuntimeError("GUARDIAN_TOKEN_BRAIN, GUARDIAN_TOKEN_TWIN and GUARDIAN_TOKEN_TIMELINE must all be different")

    def _ok(authorization: Optional[str], tok: str) -> bool:
        return bool(tok) and hmac.compare_digest((authorization or "").encode(), f"Bearer {tok}".encode())   # constant time

    def make_auth(scope: Optional[str]):
        def dep(authorization: Optional[str] = Header(default=None)):
            if not token:
                return                                  # demo mode only
            if _ok(authorization, token) or (scope and _ok(authorization, scoped[scope])):
                return
            raise HTTPException(status_code=401, detail={"code": "UNAUTHORIZED", "message": "missing or bad bearer token"})
        return dep

    auth = make_auth(None)              # admin only
    auth_read, auth_ingest = make_auth("read"), make_auth("ingest")

    @app.exception_handler(HTTPException)
    async def _err(_, exc: HTTPException):
        d = exc.detail if isinstance(exc.detail, dict) else {"code": "ERROR", "message": str(exc.detail)}
        return JSONResponse(status_code=exc.status_code, content={"error": d})

    @app.post("/observations", response_model=Verdict, dependencies=[Depends(auth_ingest)])
    def ingest(obs: Observation):
        return engine.ingest(obs)

    def _lookup(observation_id: str) -> Verdict:
        v = engine.verdicts.get(observation_id)
        if v is None:
            raise HTTPException(404, {"code": "OBSERVATION_UNKNOWN", "message": f"no verdict for {observation_id}"})
        return v

    @app.get("/verdicts/{observation_id}", response_model=Verdict, dependencies=[Depends(auth_read)])
    def get_verdict(observation_id: str):
        return _lookup(observation_id)

    @app.post("/trust/verdict", response_model=Verdict, dependencies=[Depends(auth_read)])
    def post_verdict(req: VerdictRequest):
        """Same lookup as GET /verdicts/{id}, for partners that prefer a request body."""
        return _lookup(req.observation_id)

    @app.post("/ingest/{partner}", response_model=Verdict, dependencies=[Depends(auth_ingest)])
    async def ingest_raw(partner: str, request: Request):
        """Any partner format (JSON, XML, JSON string holding XML). Profile = adapters.partners.<partner>."""
        adapter = FormatAdapter(engine.cfg, partner)
        try:
            return engine.ingest(adapter.normalize(await request.body()))
        except MalformedPayload as e:
            v = engine.reject(e.reason, e.detail, e.observation_id, e.device_id)
            return JSONResponse(status_code=422, content={
                "error": {"code": e.reason, "message": e.detail}, "verdict": jsonable_encoder(v)})

    @app.get("/devices/{device_id}/trust", dependencies=[Depends(auth_read)])
    def device_trust(device_id: str):
        t = engine.trust_of(device_id)
        if t is None:
            raise HTTPException(404, {"code": "DEVICE_UNKNOWN", "message": f"unknown device {device_id}"})
        return t

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon():
        return Response(status_code=204)

    @app.post("/devices/{device_id}/restore", dependencies=[Depends(auth)])
    def restore_device(device_id: str):
        """Bring a quarantined device back (it starts on probation again) and ask the partner to restore it too."""
        if engine.trust_of(device_id) is None:
            raise HTTPException(404, {"code": "DEVICE_UNKNOWN", "message": f"unknown device {device_id}"})
        if not engine.restore_device(device_id):
            raise HTTPException(409, {"code": "DEVICE_NOT_QUARANTINED", "message": f"{device_id} is not quarantined"})
        return {"device_id": device_id, "restored": True}

    @app.get("/health")
    def health():
        out = {"status": "ok", "devices": len(engine.devices), "verdicts": len(engine.verdicts)}
        if extra_health:
            out["twin"] = extra_health()
        return out

    if timeline is not None:
        from .timeline import register
        register(app, timeline, auth, auth_post=make_auth("timeline"))
        from .city import register as register_city
        register_city(app)

        @app.get("/", include_in_schema=False)
        def root():
            return RedirectResponse("/timeline")      # opening plain http://localhost:8002 lands on the live timeline

    return app
