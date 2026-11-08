"""Save and restore the trust state, so a restart does not make a quarantined sensor look innocent again.
The file is written atomically (temp file, then rename) with owner-only permissions, and read as untrusted input."""
import json
import os
import threading
from pathlib import Path

MAX_BYTES = 5_000_000


def save_state(engine, path: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(engine.snapshot(), f)
    os.replace(tmp, p)


def load_state(engine, path: str) -> int:
    p = Path(path)
    try:
        if not p.is_file() or p.stat().st_size > MAX_BYTES:
            return 0
        return engine.load_snapshot(json.loads(p.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        engine.log.emit("state_unreadable", note="the saved state could not be read, starting clean")
        return 0


def start_saver(engine, path: str, every_s: float, stop: threading.Event) -> threading.Thread:
    def loop():
        while not stop.wait(every_s):
            try:
                save_state(engine, path)
            except OSError:
                engine.log.emit("state_save_failed", note=f"cannot write {path}")
        try:
            save_state(engine, path)
        except OSError:
            pass
    t = threading.Thread(target=loop, daemon=True)
    t.start()
    return t
