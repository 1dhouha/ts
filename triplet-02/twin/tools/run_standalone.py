"""One-command standalone proof of the Twin Layer [Requirement R1].

Boots the LiveCity simulation, the public Façade, the observability sinks and
the two autonomous partner mocks, then drives a short incident:

1. A false water-level reading is injected into ``PRT-WLV-01``.
2. The Guardian mock detects the discrepancy against ``PRT-WLV-02`` and
   quarantines the drifting sensor through the public Façade.
3. Heavy rain raises the harbour above 80 cm.
4. The Brain mock detects the flood and dispatches the linked pump.

Run with::

    python tools/run_standalone.py
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

os.environ.setdefault("TWIN_ALLOW_INSECURE_DEV_TOKENS", "1")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from city_live.runtime import LiveCity
from mocks.brain_mock.mock_brain import BrainMock
from mocks.guardian_mock.mock_guardian import GuardianMock
from twin_interface.facade import TwinFacade
from twin_observability.event_store import EventStore
from twin_observability.scenario_log import ScenarioLogger

FAULTY_SENSOR = "PRT-WLV-01"
BACKUP_SENSOR = "PRT-WLV-02"


def _banner(title: str) -> None:
    print("\n" + "=" * 72)
    print(f"  {title}")
    print("=" * 72)


def run(max_ticks: int, run_id: str) -> int:
    log_dir = ROOT / "logs"
    logger = ScenarioLogger("standalone", run_id, log_dir=log_dir)
    store = EventStore()
    city = LiveCity(run_id)
    facade = TwinFacade(city, event_store=store, scenario_logger=logger)

    guardian = GuardianMock(facade)
    brain = BrainMock(facade)

    guardian_actions = []
    brain_actions = []

    try:
        logger.log_start(
            sim_tick=0,
            config={
                "faulty_sensor": FAULTY_SENSOR,
                "backup_sensor": BACKUP_SENSOR,
                "fault": "FALSE_READING +100cm",
                "disturbance": "heavy_rain",
            },
        )

        _banner("STANDALONE TWIN + PARTNER MOCKS")
        print(f"Run ID          : {run_id}")
        print(f"JSONL audit log : {logger.log_path}")
        print(f"Guardian token  : configured")
        print(f"Brain token     : configured")

        for _ in range(5):
            city.step()

        print("\n>> Injecting FALSE_READING (+100 cm) into", FAULTY_SENSOR)
        city.sensor_fault(FAULTY_SENSOR, "FALSE_READING", {"offset": 100.0})
        print(">> Starting heavy rain disturbance")
        city.incident("heavy_rain")

        for _ in range(max_ticks):
            city.step()
            tick = city.status()["sim_tick"]

            if tick % 5 == 0:
                guardian_actions.extend(guardian.scan())
                brain_actions.extend(brain.tick())

            if tick % 25 == 0:
                logger.log_telemetry_pulse(
                    sim_tick=tick,
                    readings=facade.get_sensor_readings(token=guardian.token)["data"]["readings"],
                )

            if guardian_actions and brain_actions:
                break

        _banner("RESULTS")
        if guardian_actions:
            for action in guardian_actions:
                print(
                    f"[GUARDIAN] Isolated {action.device_id} "
                    f"(backup {action.backup_id}, discrepancy {action.discrepancy_cm} cm)"
                )
                print(f"           Reason: {action.reason}")
        else:
            print("[GUARDIAN] No quarantine action recorded.")

        if brain_actions:
            for action in brain_actions:
                print(
                    f"[BRAIN] Dispatched {action.device_id} at "
                    f"{action.water_level_cm} cm from {action.sensor_id} "
                    f"(request {action.request_id})"
                )
        else:
            print("[BRAIN] No pump dispatch recorded.")

        isolated = facade.is_device_isolated(FAULTY_SENSOR)
        dispatched = bool(brain_actions)
        summary = {
            "guardian_isolated": isolated,
            "brain_dispatched": dispatched,
            "sim_tick": city.status()["sim_tick"],
            "events_recorded": store.total_recorded,
            "log_lines": logger.line_count,
        }
        logger.log_end(sim_tick=city.status()["sim_tick"], summary=summary)

        print(f"\nQuarantine active : {isolated}")
        print(f"Pump dispatched   : {dispatched}")
        print(f"Events recorded   : {store.total_recorded}")
        print(f"JSONL lines       : {logger.line_count}")

        passed = isolated and dispatched and logger.line_count > 0
        print("\n" + ("[PASS] Standalone Twin + mocks completed successfully." if passed
                      else "[FAIL] Standalone proof did not reach both milestones."))
        return 0 if passed else 1
    finally:
        city.close()
        logger.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ticks", type=int, default=1150, help="maximum simulation ticks")
    parser.add_argument("--run-id", default="standalone-001", help="simulation run identifier")
    args = parser.parse_args()
    raise SystemExit(run(max_ticks=args.ticks, run_id=args.run_id))
