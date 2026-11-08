"""Protocol adapter -> Format adapter -> engine. One place where the three meet."""
from typing import Any

from .adapters.format import FormatAdapter, MalformedPayload
from .engine import GuardianEngine
from .models import Verdict


def process_raw(raw: Any, fmt: FormatAdapter, engine: GuardianEngine) -> Verdict:
    try:
        return engine.ingest(fmt.normalize(raw))
    except MalformedPayload as e:
        return engine.reject(e.reason, e.detail, e.observation_id, e.device_id)


def run_source(source, fmt: FormatAdapter, engine: GuardianEngine) -> list[Verdict]:
    return [process_raw(raw, fmt, engine) for raw in source.fetch()]


def poll_loop(source, fmt: FormatAdapter, engine: GuardianEngine, interval_s: float, stop, twin=None) -> None:
    """Pull mode: same core as push mode, only the way data arrives differs.
    Also retries containment calls that failed earlier. `stop` is a threading.Event."""
    while not stop.is_set():
        try:
            run_source(source, fmt, engine)
        except Exception as e:                     # a flaky partner must never kill the loop
            engine.log.emit("poll_error", error=str(e))
        if twin is not None and hasattr(twin, "flush_pending"):
            twin.flush_pending()
        stop.wait(interval_s)
