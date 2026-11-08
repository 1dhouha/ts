"""Live timeline (R3): every layer posts events here, one screen shows cause and effect.
Kept separate from the detection code on purpose: it only reads and writes events, so it could become its own service."""
import os
import threading
import time
from pathlib import Path
from collections import deque
from datetime import datetime, timezone
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse

LAYERS = {"twin", "brain", "guardian"}


class TimelineStore:
    def __init__(self, maxlen: int = 5000):
        self._events: deque = deque(maxlen=maxlen)
        self._n = 0
        self._lock = threading.Lock()
        self.running: Optional[str] = None

    def add(self, rec: dict) -> int:
        with self._lock:
            self._n += 1
            self._events.append({"id": self._n, **rec})
            return self._n

    def since(self, last_id: int = 0, limit: int = 1000) -> list[dict]:
        with self._lock:
            return [e for e in self._events if e["id"] > last_id][:limit]

    def clear(self) -> None:
        with self._lock:
            self._events.clear()


def run_live(sid: str, store: TimelineStore, speed: float = 10.0) -> None:
    """Replay a scenario through a fresh Guardian (and mock Twin/Brain), paced for a human, into the timeline."""
    from guardian.eventlog import EventLog
    from scenarios.run import SCENARIOS, Run

    def sink(rec: dict) -> None:           # show wall-clock time on screen, keep the scenario's own time as sim_time
        store.add({**rec, "sim_time": rec["timestamp"], "timestamp": datetime.now(timezone.utc).isoformat()})

    log_dir = Path(os.getenv("GUARDIAN_LOG_DIR", "logs"))
    log_dir.mkdir(parents=True, exist_ok=True)       # every button press also leaves a log file, like the CLI (R2)
    path = log_dir / f"{sid}-{datetime.now():%Y%m%d-%H%M%S}.log"
    run = Run(sid, EventLog(str(path), sink=sink))
    last = None
    for obs in SCENARIOS[sid]["gen"]():
        if last is not None and obs.timestamp > last and speed > 0:
            time.sleep(1.0 / speed)        # one simulated second every 1/speed seconds
        last = max(last, obs.timestamp) if last else obs.timestamp
        run.feed(obs)
    run.finish()


def register(app: FastAPI, store: TimelineStore, auth, auth_post=None) -> None:
    @app.post("/timeline/events", dependencies=[Depends(auth_post or auth)])
    async def post_event(request: Request):
        try:
            rec = await request.json()
        except ValueError:
            raise HTTPException(422, {"code": "INVALID_JSON", "message": "body must be a JSON object"})
        if not isinstance(rec, dict) or rec.get("layer") not in LAYERS or not rec.get("event"):
            raise HTTPException(422, {"code": "INVALID_EVENT",
                                      "message": "need layer (twin, brain or guardian) and event"})
        rec.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
        return {"id": store.add(rec)}

    @app.get("/timeline/events", dependencies=[Depends(auth)])
    def get_events(since: int = 0, limit: int = 1000):
        ev = store.since(since, limit)
        return {"events": ev, "last_id": ev[-1]["id"] if ev else since, "running": store.running}

    @app.delete("/timeline/events", dependencies=[Depends(auth)])
    def clear_events():
        store.clear()
        return {"cleared": True}

    @app.get("/scenarios", dependencies=[Depends(auth)])
    def list_scenarios():
        try:
            from scenarios.run import SCENARIOS
        except ImportError:
            raise HTTPException(501, {"code": "NO_SCENARIOS", "message": "scenarios package not installed"})
        return [{"id": k, "name": v["name"]} for k, v in SCENARIOS.items()]

    @app.post("/scenarios/{sid}/run", status_code=202, dependencies=[Depends(auth)])
    def run_scenario(sid: str, speed: float = 10.0):
        try:
            from scenarios.run import SCENARIOS
        except ImportError:
            raise HTTPException(501, {"code": "NO_SCENARIOS", "message": "scenarios package not installed"})
        if sid not in SCENARIOS:
            raise HTTPException(404, {"code": "SCENARIO_UNKNOWN", "message": f"unknown scenario {sid}"})
        if store.running:
            raise HTTPException(409, {"code": "SCENARIO_RUNNING", "message": f"{store.running} is still running"})
        store.running = sid

        def work():
            try:
                run_live(sid, store, speed)
            finally:
                store.running = None
        threading.Thread(target=work, daemon=True).start()
        return {"started": sid, "speed": speed}

    @app.get("/timeline", response_class=HTMLResponse)
    def page():
        return PAGE


PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>City timeline</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
:root{--bg:#fff;--fg:#1f2937;--mut:#6b7280;--line:#e5e7eb;--tw:#374151;--gu:#0f766e;--br:#b45309;--bad:#b91c1c}
@media (prefers-color-scheme:dark){:root{--bg:#111827;--fg:#e5e7eb;--mut:#9ca3af;--line:#374151;--tw:#9ca3af;--gu:#2dd4bf;--br:#fbbf24;--bad:#f87171}}
body{margin:0;font:14px/1.4 system-ui,Segoe UI,Calibri,sans-serif;background:var(--bg);color:var(--fg)}
header{display:flex;flex-wrap:wrap;gap:8px;align-items:center;padding:10px 14px;border-bottom:1px solid var(--line);position:sticky;top:0;background:var(--bg)}
h1{font-size:16px;margin:0 12px 0 0} button,select{font:inherit;padding:4px 10px;border:1px solid var(--line);background:transparent;color:var(--fg);border-radius:6px;cursor:pointer}
button:hover{border-color:var(--gu)} .st{margin-left:auto;color:var(--mut)}
table{border-collapse:collapse;width:100%} td{padding:3px 10px;border-bottom:1px solid var(--line);vertical-align:top}
td.t{color:var(--mut);white-space:nowrap;font-variant-numeric:tabular-nums} td.d{color:var(--mut);font-family:Consolas,monospace;font-size:12px}
.chip{display:inline-block;min-width:68px;text-align:center;border-radius:10px;padding:0 8px;font-size:12px;border:1px solid currentColor}
.twin{color:var(--tw)}.guardian{color:var(--gu)}.brain{color:var(--br)} tr.bad td.e{color:var(--bad);font-weight:600}
tr.key td{background:rgba(15,118,110,.08)} tbody.keyonly tr.minor{display:none} button:disabled{opacity:.4;cursor:not-allowed} td.dev{white-space:nowrap}
</style></head><body>
<header><h1>City timeline</h1><span id="btns"></span>
<label>speed <select id="speed"><option>5</option><option selected>10</option><option>30</option></select></label>
<label><input type="checkbox" id="key" checked> key events only</label><button id="clear">Clear</button><span class="st" id="st">connecting...</span></header>
<table><tbody id="rows"></tbody></table>
<script>
const token=new URLSearchParams(location.search).get('token');
const H=token?{'Authorization':'Bearer '+token}:{};
let last=0,n=0;const rows=document.getElementById('rows'),st=document.getElementById('st');
const skip=new Set(['id','timestamp','layer','event','observation_id','device_id','sim_time']);
function esc(s){return String(s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}
function add(e){
  const bad=(e.verdict==='UNTRUSTED')||/quarantin|stale|REJECT|HOLD|rejected/i.test(e.event+(e.action||''));
  const key=['brain_decision','device_quarantined','containment_call','scenario_start','scenario_end'].includes(e.event);
  const det=Object.entries(e).filter(([k])=>!skip.has(k)).map(([k,v])=>k+'='+(typeof v==='object'?JSON.stringify(v):v)).join('  ');
  const minor=e.event==='reading'||(e.event==='verdict'&&e.verdict==='TRUSTED');
  const tr=document.createElement('tr');tr.className=(bad?'bad ':'')+(key?'key ':'')+(minor?'minor':'');
  const tm=(e.timestamp||'').slice(11,23);
  tr.innerHTML='<td class="t">'+esc(tm)+'</td><td><span class="chip '+esc(e.layer)+'">'+esc(e.layer)+'</span></td><td class="e">'+esc(e.event)+
   '</td><td class="dev">'+esc(e.device_id||'')+'</td><td class="d">'+esc(e.observation_id||'')+'</td><td class="d">'+esc(det)+'</td>';
  rows.appendChild(tr);n++;
}
async function poll(){
  try{const r=await fetch('/timeline/events?since='+last,{headers:H});
    if(r.status===401){st.textContent='unauthorized: add ?token=...';return}
    const j=await r.json();j.events.forEach(add);if(j.events.length){last=j.last_id;window.scrollTo(0,document.body.scrollHeight)}
    document.querySelectorAll('#btns button').forEach(b=>b.disabled=!!j.running);
    st.textContent=(j.running?'running '+j.running.toUpperCase()+' (buttons are off until it ends) | ':'')+n+' events';
  }catch(e){st.textContent='no connection'}
}
async function load(){
  const r=await fetch('/scenarios',{headers:H});if(!r.ok)return;const l=await r.json();const b=document.getElementById('btns');
  l.forEach(s=>{const x=document.createElement('button');x.textContent=s.id.toUpperCase();x.title=s.name;
    x.onclick=async()=>{const r=await fetch('/scenarios/'+s.id+'/run?speed='+document.getElementById('speed').value,{method:'POST',headers:H});if(r.status===409)st.textContent='a scenario is still running, wait for it to end'};b.appendChild(x)})
}
document.getElementById('clear').onclick=async()=>{await fetch('/timeline/events',{method:'DELETE',headers:H});rows.innerHTML='';last=0;n=0};
const kb=document.getElementById('key');const sync=()=>rows.className=kb.checked?'keyonly':'';kb.onchange=sync;sync();
load();setInterval(poll,400);poll();
</script></body></html>"""
