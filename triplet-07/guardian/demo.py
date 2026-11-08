"""Start the Guardian with its live timeline:  python demo.py   (then open http://localhost:8002/timeline)"""
import os
import sys
import threading
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

import uvicorn  # noqa: E402

if __name__ == "__main__":
    os.environ.setdefault("GUARDIAN_DEMO", "1")      # local demo: no token needed. Set GUARDIAN_TOKEN to turn auth on.
    port = 8002
    threading.Timer(1.5, lambda: webbrowser.open(f"http://localhost:{port}/timeline")).start()
    uvicorn.run("guardian.main:app", factory=True, host="127.0.0.1", port=port, access_log=False)   # no per-request noise in the terminal
