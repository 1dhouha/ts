"""Run the operator dashboard and MCP server against one LiveCity owner."""
from __future__ import annotations

import argparse
import getpass
import os
from pathlib import Path
import sys
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def build_runtime(run_id, *, dev=False):
    if dev:
        os.environ["TWIN_ALLOW_INSECURE_DEV_TOKENS"] = "1"
    from city_live.runtime import LiveCity
    from twin_interface.facade import TwinFacade
    from twin_observability.event_store import EventStore
    from twin_observability.scenario_log import ScenarioLogger

    persistence_path = Path(os.environ.get(
        "TWIN_STATE_DB_PATH", str(ROOT / "logs" / "twin-state.sqlite3"),
    ))
    city = LiveCity(run_id, persistence_path=persistence_path)
    run_id = city.status()["run_id"]
    logger = ScenarioLogger("twin", run_id, log_dir=ROOT / "logs")
    facade = TwinFacade(city, event_store=EventStore(), scenario_logger=logger)
    return city, facade, logger


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dashboard-port", type=int, default=8765)
    parser.add_argument("--mcp-port", type=int, default=8000)
    parser.add_argument("--mcp-transport", choices=("sse", "streamable-http"), default="sse")
    parser.add_argument("--run-id", default="twin-shared-001")
    parser.add_argument("--dev", action="store_true")
    parser.add_argument(
        "--scenario",
        help="Start an existing live scenario in the shared Twin runtime",
    )
    args = parser.parse_args()

    city = facade = logger = bridge = None
    try:
        if not 0 <= args.dashboard_port <= 65535 or not 0 <= args.mcp_port <= 65535:
            raise ValueError("Ports must be 0-65535")
        if sys.stdin.isatty():
            secret = getpass.getpass("Choose a one-launch operator passphrase (16-256 characters): ")
        else:
            secret = sys.stdin.readline(258).rstrip("\r\n")
        from city_dashboard.security import Sessions
        Sessions(secret)
        from city_dashboard.server import DashboardBridge
        from twin_interface.mcp_server.server import create_mcp_server

        city, facade, logger = build_runtime(args.run_id, dev=args.dev)
        logger.log_start(sim_tick=0, config={
            "mode": "shared-dashboard-mcp",
            "dashboard_port": args.dashboard_port,
            "mcp_port": args.mcp_port,
            "mcp_transport": args.mcp_transport,
        })
        if args.scenario:
            scenario_status = city.start_b_scenario(args.scenario)
            print(
                f"Scenario started: {scenario_status['scenario_id']} "
                f"(correlation {scenario_status['correlation_id']})",
                flush=True,
            )
        bridge = DashboardBridge(city, secret, port=args.dashboard_port)
        del secret
        mcp = create_mcp_server(facade)
        mcp.settings.host = "127.0.0.1"
        mcp.settings.port = args.mcp_port
        bridge.start()
        city.start()
        worker = threading.Thread(
            target=lambda: mcp.run(transport=args.mcp_transport),
            name="twin-mcp",
            daemon=True,
        )
        worker.start()
        print(f"Convergence City dashboard: {bridge.url}", flush=True)
        print(f"Twin MCP server ({args.mcp_transport}) on 127.0.0.1:{args.mcp_port}", flush=True)
        print("Dashboard and MCP share one LiveCity owner. Type quit, EOF or Ctrl+C to stop.", flush=True)
        for line in sys.stdin:
            if line.strip().lower() in ("quit", "exit"):
                break
        return 0
    except (KeyboardInterrupt, EOFError):
        return 0
    except OSError:
        print("PORT_UNAVAILABLE: choose a different local port.", file=sys.stderr)
        return 2
    except ValueError:
        print("INVALID_ARGUMENT: ports 0-65535 and passphrase length 16-256 required.", file=sys.stderr)
        return 2
    finally:
        if bridge is not None:
            bridge.close()
        elif city is not None:
            city.close()
        if logger is not None:
            logger.close()
        if city is not None:
            print("Owner stopped.", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
