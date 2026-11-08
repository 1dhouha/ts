"""Prove command idempotency across abrupt Twin process termination."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

CHILD = """
import json
import os
import sys
from city_live.runtime import LiveCity

city = LiveCity("durable-evidence-run", persistence_path=sys.argv[1])
before_state = city.diagnostics()["physical_state"]
before_revision = city.status()["revision"]
result = city.execute_actuator_command(
    "PRT-PMP-01", "set_speed", {"speed_pct": 35},
    command_id="jury-restart-request", run_id=city.status()["run_id"],
    expected_revision=0, correlation_id="jury-restart-correlation",
    scenario_id="SCN-02",
)
after_state = city.diagnostics()["physical_state"]
print(json.dumps({
    "run_id": city.status()["run_id"],
    "revision_before": before_revision,
    "revision_after": city.status()["revision"],
    "before_state": before_state,
    "after_state": after_state,
    "result": result,
}, sort_keys=True), flush=True)
os._exit(0)
"""


def database_evidence(database):
    import sqlite3

    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        return {
            "requests": [dict(row) for row in connection.execute(
                "SELECT request_id, command_hash, run_id, correlation_id, scenario_id, "
                "device_id, command_type, status, result_json, resulting_revision, "
                "created_at, completed_at FROM processed_requests ORDER BY created_at"
            )],
            "operations": [dict(row) for row in connection.execute(
                "SELECT operation_id, request_id, operation_type, status, created_at, "
                "completed_at, error_code FROM operations ORDER BY created_at"
            )],
            "core_runs": [dict(row) for row in connection.execute(
                "SELECT run_id, final_context_json, final_digest, updated_at FROM core_runs"
            )],
            "journal_mode": connection.execute(
                "PRAGMA journal_mode"
            ).fetchone()[0],
        }
    finally:
        connection.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", type=Path)
    args = parser.parse_args()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    evidence = args.evidence_dir or ROOT / "test-evidence" / (
        f"durable-idempotency-{stamp}-{uuid4().hex[:8]}"
    )
    evidence = evidence.resolve()
    evidence.mkdir(parents=True, exist_ok=False)
    database = evidence / "twin.sqlite3"
    command = [sys.executable, "-c", CHILD, str(database)]
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(ROOT / "src")

    first = subprocess.run(
        command, cwd=ROOT, env=environment, capture_output=True,
        text=True, check=True, timeout=30,
    )
    (evidence / "process-before-restart.log").write_text(
        first.stdout + first.stderr, encoding="utf-8",
    )
    first_result = json.loads(first.stdout)
    (evidence / "database-before-restart.json").write_text(
        json.dumps(database_evidence(database), indent=2), encoding="utf-8",
    )

    second = subprocess.run(
        command, cwd=ROOT, env=environment, capture_output=True,
        text=True, check=True, timeout=30,
    )
    (evidence / "process-after-restart.log").write_text(
        second.stdout + second.stderr, encoding="utf-8",
    )
    second_result = json.loads(second.stdout)
    (evidence / "database-after-restart.json").write_text(
        json.dumps(database_evidence(database), indent=2), encoding="utf-8",
    )

    passed = (
        first_result["revision_after"] == 1
        and second_result["revision_before"] == 1
        and second_result["revision_after"] == 1
        and first_result["result"] == second_result["result"]
        and first_result["before_state"] != first_result["after_state"]
        and second_result["before_state"] == second_result["after_state"]
    )
    result = {
        "result": "PASS" if passed else "FAIL",
        "request_id": "jury-restart-request",
        "correlation_id": "jury-restart-correlation",
        "process_one_exit_code": first.returncode,
        "process_two_exit_code": second.returncode,
        "first_process": first_result,
        "second_process": second_result,
        "evidence_directory": str(evidence),
        "commands": [command, command],
    }
    (evidence / "summary.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8",
    )
    print(json.dumps(result, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
