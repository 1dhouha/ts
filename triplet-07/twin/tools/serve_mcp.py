"""Serve the FastMCP Twin interface for partner agents.

Examples::

    python tools/serve_mcp.py --dev                 # SSE on http://127.0.0.1:8000/sse
    python tools/serve_mcp.py --transport stdio     # for an MCP stdio client
    TWIN_TOKEN_BRAIN=... python tools/serve_mcp.py  # production tokens

``--dev`` enables the documented local-development tokens. Never use it in
production.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--transport", choices=("sse", "stdio", "streamable-http"), default="sse")
    parser.add_argument("--run-id", default="mcp-live-001")
    parser.add_argument("--dev", action="store_true", help="enable local development tokens")
    args = parser.parse_args()

    if args.dev:
        os.environ["TWIN_ALLOW_INSECURE_DEV_TOKENS"] = "1"

    from city_live.runtime import LiveCity
    from twin_interface.auth import missing_token_roles
    from twin_interface.facade import TwinFacade
    from twin_interface.mcp_server.server import create_mcp_server
    from twin_observability.event_store import EventStore
    from twin_observability.scenario_log import ScenarioLogger

    missing = missing_token_roles()
    if missing:
        print(
            "Missing tokens for roles: " + ", ".join(missing) + ". "
            "Set TWIN_TOKEN_<ROLE> or pass --dev for local development.",
            file=sys.stderr,
        )
        return 2

    persistence_path = Path(os.environ.get(
        "TWIN_STATE_DB_PATH", str(ROOT / "logs" / "twin-state.sqlite3"),
    ))
    city = LiveCity(args.run_id, persistence_path=persistence_path)
    active_run_id = city.status()["run_id"]
    logger = ScenarioLogger("mcp", active_run_id, log_dir=ROOT / "logs")
    facade = TwinFacade(city, event_store=EventStore(), scenario_logger=logger)
    logger.log_start(sim_tick=0, config={"transport": args.transport})

    print(f"Twin MCP server starting ({args.transport}) for run '{active_run_id}'.")
    print(f"Audit log: {logger.log_path}")
    try:
        create_mcp_server(facade).run(transport=args.transport)
    except KeyboardInterrupt:
        pass
    finally:
        city.close()
        logger.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
