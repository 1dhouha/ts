"""uvicorn --factory guardian.main:app --port 8002
Profile: GUARDIAN_PROFILE=twin loads config.twin.yaml on top of config.yaml (Twin REST contract, strict registry)."""
import os
import threading
import time

from .adapters.format import FormatAdapter
from .adapters.protocol import HttpPollAdapter
from .adapters.twin import TwinPoller
from .api import create_app
from .containment import HttpTwinClient
from .engine import GuardianEngine, load_config
from .eventlog import EventLog
from .pipeline import poll_loop
from .state import load_state, start_saver
from .timeline import TimelineStore


class PollStatus:
    """What /health shows about the Twin link: is polling on, when did it last succeed, how many failures in a row."""

    def __init__(self, source):
        self.source, self.last_ok, self.errors_in_a_row, self.enabled = source, None, 0, True

    def fetch(self):
        try:
            yield from self.source.fetch()
            self.last_ok, self.errors_in_a_row = time.time(), 0
        except Exception:
            self.errors_in_a_row += 1
            raise

    def report(self) -> dict:
        age = None if self.last_ok is None else round(time.time() - self.last_ok, 1)
        return {"polling": True, "last_ok_s_ago": age, "errors_in_a_row": self.errors_in_a_row,
                "run_id": getattr(self.source, "run_id", None)}


def app():
    cfg = load_config()
    store = TimelineStore()
    log = EventLog("logs/guardian-live.log", sink=store.add)
    twin = HttpTwinClient.from_config(cfg, eventlog=log)
    engine = GuardianEngine(cfg, on_contain=lambda dev, why: twin.quarantine_device(dev, why),
                            on_release=lambda dev: twin.restore_device(dev), eventlog=log)
    state_path = os.getenv("GUARDIAN_STATE_PATH", (cfg.get("state") or {}).get("path", ""))
    if state_path:                                  # trust survives a restart; ordering state does not (a fresh stream must be accepted)
        load_state(engine, state_path)
        start_saver(engine, state_path, float((cfg.get("state") or {}).get("save_every_s", 5)), threading.Event())
    poll = cfg.get("polling", {})
    status = None
    if poll.get("mode") == "twin":                  # pull mode against the Twin team's REST interface
        token = os.getenv(cfg["partners"].get("twin_token_env", "TWIN_TOKEN_GUARDIAN"), "")
        if not token:                               # fail closed, like our own API: no Twin token, no start
            raise RuntimeError(f"{cfg['partners'].get('twin_token_env', 'TWIN_TOKEN_GUARDIAN')} is not set: "
                               "Guardian needs the guardian-role token issued by the Twin team.")
        src = TwinPoller(
            cfg["partners"]["twin_url"], token, path=poll.get("path", "/api/v1/sensors"),
            on_unavailable=engine.mark_unavailable,
            on_failed=lambda r: engine.report_failure(r["_obs_id"], r["sensor_id"], str(r.get("type", "unknown")),
                                                      str(r.get("zone_id") or "default"), r["_status"]),
            on_run_change=lambda old, new: engine.reset_run(f"Twin run changed {old} -> {new}"))
        status = PollStatus(src)
        stop = threading.Event()
        threading.Thread(target=poll_loop, daemon=True,
                         args=(status, FormatAdapter(cfg, "twin"), engine, poll.get("interval_s", 1.0), stop, twin)).start()
    elif poll.get("url"):                           # generic pull mode, in addition to push
        stop = threading.Event()
        src = HttpPollAdapter(poll["url"])
        threading.Thread(target=poll_loop, daemon=True,
                         args=(src, FormatAdapter(cfg), engine, poll.get("interval_s", 1.0), stop, twin)).start()
    return create_app(engine, timeline=store, extra_health=status.report if status else None)
