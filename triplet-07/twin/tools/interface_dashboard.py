#!/usr/bin/env python3
"""FastAPI dashboard to test Twin Interface (Facade + REST + Auth + MCP style)"""
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
os.environ.setdefault("TWIN_ALLOW_INSECURE_DEV_TOKENS", "1")

from fastapi import FastAPI, Request, Header
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from typing import Optional
import uvicorn

from city_live.runtime import LiveCity
from twin_interface.facade import TwinFacade
from twin_interface.rest.router import handle_rest_request

app = FastAPI(title="Twin Interface Test Dashboard", version="1.0.0")

city = LiveCity("dashboard-interface")
facade = TwinFacade(city)
# Tick simulation to get real data
for _ in range(50):
    try:
        city.step()
    except Exception:
        break

templates_dir = Path(__file__).resolve().parent / "templates"
templates_dir.mkdir(exist_ok=True)
templates = Jinja2Templates(directory=str(templates_dir))


@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    return templates.TemplateResponse("dashboard.html", {"request": request})


@app.get("/api/health")
@app.get("/api/health/ready")
async def health():
    status, resp = handle_rest_request(facade, "GET", "/health/ready")
    return JSONResponse(content=resp, status_code=status)


@app.get("/api/health/live")
async def liveness():
    status, resp = handle_rest_request(facade, "GET", "/health/live")
    return JSONResponse(content=resp, status_code=status)


@app.get("/api/sensors")
async def get_sensors(
    zone_id: Optional[str] = None,
    sensor_id: Optional[str] = None,
    sensor_type: Optional[str] = None,
    authorization: Optional[str] = Header(None)
):
    headers = {"Authorization": authorization} if authorization else {}
    query = []
    if zone_id: query.append(f"zone_id={zone_id}")
    if sensor_id: query.append(f"sensor_id={sensor_id}")
    if sensor_type: query.append(f"sensor_type={sensor_type}")
    path = "/api/v1/sensors" + ("?" + "&".join(query) if query else "")
    status, resp = handle_rest_request(facade, "GET", path, headers=headers)
    return JSONResponse(content=resp, status_code=status)


@app.get("/api/state")
async def get_state(
    zone_id: Optional[str] = None,
    authorization: Optional[str] = Header(None)
):
    headers = {"Authorization": authorization} if authorization else {}
    path = f"/api/v1/state?zone_id={zone_id}" if zone_id else "/api/v1/state"
    status, resp = handle_rest_request(facade, "GET", path, headers=headers)
    return JSONResponse(content=resp, status_code=status)


@app.get("/api/events")
async def get_events(
    seq: int = 0,
    authorization: Optional[str] = Header(None)
):
    headers = {"Authorization": authorization} if authorization else {}
    path = f"/api/v1/events?seq={seq}"
    status, resp = handle_rest_request(facade, "GET", path, headers=headers)
    return JSONResponse(content=resp, status_code=status)


@app.post("/api/commands")
async def send_command(request: Request, authorization: Optional[str] = Header(None)):
    body = await request.json()
    headers = {"Authorization": authorization} if authorization else {}
    status, resp = handle_rest_request(facade, "POST", "/api/v1/commands", headers=headers, body=body)
    return JSONResponse(content=resp, status_code=status)


@app.post("/api/isolate")
async def isolate(request: Request, authorization: Optional[str] = Header(None)):
    body = await request.json()
    headers = {"Authorization": authorization} if authorization else {}
    status, resp = handle_rest_request(facade, "POST", "/api/v1/isolate", headers=headers, body=body)
    return JSONResponse(content=resp, status_code=status)


@app.post("/api/restore")
async def restore(request: Request, authorization: Optional[str] = Header(None)):
    body = await request.json()
    headers = {"Authorization": authorization} if authorization else {}
    status, resp = handle_rest_request(facade, "POST", "/api/v1/restore", headers=headers, body=body)
    return JSONResponse(content=resp, status_code=status)


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8080)
