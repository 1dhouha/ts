"""Audit log viewer and Requirement R2 verifier.

Inspects generated .jsonl audit files in the `logs/` directory, prints a clean
chronological table of events, and verifies zero ground-truth leakage.

Run with:
    python tools/view_logs.py
    python tools/view_logs.py logs/manual_demo_live_001.jsonl
"""
import argparse
import json
from pathlib import Path
import sys

# Ensure UTF-8 output on Windows consoles
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
LOGS_DIR = ROOT / "logs"

FORBIDDEN_KEYS = ("actual_value", "delta", "fault", "ground_truth")


def format_details(event: dict) -> str:
    payload = event.get("payload", {})
    etype = event.get("event_type", "")

    if etype == "RUN_START":
        return f"Scenario: '{payload.get('scenario')}' (Run ID: {payload.get('run_id')})"
    if etype == "RUN_END":
        return f"Completed at tick {payload.get('total_ticks')} (Summary: {payload.get('summary')})"
    if etype == "TELEMETRY_PULSE":
        return f"Telemetry pulse with {payload.get('sensor_count', 0)} sensors reporting"
    if etype == "ACTUATOR_COMMAND":
        return f"{payload.get('command').upper()} -> {payload.get('device_id')} (Outcome: {payload.get('outcome')})"
    if etype == "QUARANTINE_ACTION":
        return f"{payload.get('action')} -> {payload.get('device_id')} (Reason: {payload.get('reason')})"
    if etype == "ALERT":
        return f"Level: {payload.get('level')} - {payload.get('title')}"
    return json.dumps(payload, separators=(",", ":"))


def main() -> None:
    parser = argparse.ArgumentParser(description="View and verify .jsonl audit logs.")
    parser.add_argument("log_file", nargs="?", help="Path to specific .jsonl log file.")
    args = parser.parse_args()

    if args.log_file:
        target_path = Path(args.log_file)
    else:
        # Pick the most recently modified .jsonl file in logs/
        jsonl_files = sorted(LOGS_DIR.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not jsonl_files:
            print(f"[-] No .jsonl files found in {LOGS_DIR}. Run a scenario first (e.g. python tools/manual_test.py)!")
            return
        target_path = jsonl_files[0]

    print("\n" + "=" * 80)
    print(f"  AUDIT LOG VIEWER [REQUIREMENT R2]: {target_path.name}")
    print("=" * 80)

    lines = target_path.read_text(encoding="utf-8").strip().split("\n")
    if not lines or lines == [""]:
        print("[-] File is empty.")
        return

    print(f"\nTotal Records: {len(lines)}")
    print("-" * 80)
    print(f"{'SEQ':<6} | {'TICK':<6} | {'SOURCE':<10} | {'EVENT TYPE':<18} | {'SUMMARY'}")
    print("-" * 80)

    leaks_found = 0
    for idx, line in enumerate(lines, 1):
        if not line.strip():
            continue

        # Check for forbidden leaks in raw string
        for fkey in FORBIDDEN_KEYS:
            if f'"{fkey}"' in line:
                print(f"❌ [SECURITY BREACH] Found forbidden key '{fkey}' on line {idx}!")
                leaks_found += 1

        record = json.loads(line)
        seq = str(record.get("seq", "-"))
        tick = f"t={record.get('sim_tick', 0)}s"
        source = record.get("source", "system")
        etype = record.get("event_type", "UNKNOWN")
        summary = format_details(record)

        print(f"{seq:<6} | {tick:<6} | {source:<10} | {etype:<18} | {summary}")

    print("-" * 80)
    if leaks_found == 0:
        print("✔ [AUDIT PASSED] ZERO Ground Truth leaked! (0 actual_value, 0 delta, 0 fault)")
    else:
        print(f"❌ [AUDIT FAILED] {leaks_found} leakage violation(s) detected!")
    print("=" * 80 + "\n")


if __name__ == "__main__":
    main()
