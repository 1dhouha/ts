import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from city_live.records import LiveError
from city_live.runtime import LiveCity


ROOT = Path(__file__).resolve().parents[1]
CHILD = """
import json
import os
import sys
from city_live.runtime import LiveCity

city = LiveCity("durable-process", persistence_path=sys.argv[1])
before = city.diagnostics()["physical_state"]
revision_before = city.status()["revision"]
result = city.execute_actuator_command(
    "PRT-PMP-01", "set_speed", {"speed_pct": 35},
    command_id="process-restart-request", run_id=city.status()["run_id"],
    expected_revision=0, correlation_id="durable-process-correlation",
    scenario_id="SCN-02",
)
after = city.diagnostics()["physical_state"]
print(json.dumps({
    "run_id": city.status()["run_id"],
    "revision_before": revision_before,
    "revision_after": city.status()["revision"],
    "before": before,
    "after": after,
    "result": result,
}, sort_keys=True))
sys.stdout.flush()
os._exit(0)
"""


class DurableIdempotencyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.database = Path(self.temporary.name) / "twin.sqlite3"

    def city(self, run_id="durable-run"):
        owner = LiveCity(run_id, persistence_path=self.database)
        self.addCleanup(owner.close)
        return owner

    def execute(self, city, request_id="durable-request", *, speed=35,
                revision=0):
        return city.execute_actuator_command(
            "PRT-PMP-01", "set_speed", {"speed_pct": speed},
            command_id=request_id, run_id=city.status()["run_id"],
            expected_revision=revision, correlation_id="corr-durable",
            scenario_id="SCN-02",
        )

    def test_owner_recreation_replays_committed_receipt_and_city_state(self):
        first_owner = self.city()
        initial = first_owner.diagnostics()["physical_state"]
        first = self.execute(first_owner)
        self.assertNotEqual(initial, first_owner.diagnostics()["physical_state"])
        self.assertEqual(first_owner.status()["revision"], 1)
        first_owner.close()

        restarted = self.city("new-owner-run-id")
        restored = restarted.diagnostics()["physical_state"]
        revision = restarted.status()["revision"]
        duplicate = self.execute(restarted)

        self.assertEqual(restarted.status()["run_id"], "durable-run")
        self.assertEqual(duplicate, first)
        self.assertEqual(restarted.status()["revision"], revision)
        self.assertEqual(restored, restarted.diagnostics()["physical_state"])
        row = restarted._persistence.request("durable-request")
        self.assertEqual(row["status"], "COMMITTED")
        self.assertEqual(row["correlation_id"], "corr-durable")
        self.assertEqual(row["scenario_id"], "SCN-02")

    def test_same_id_with_changed_payload_is_rejected_after_restart(self):
        city = self.city()
        self.execute(city)
        city.close()
        restarted = self.city()
        with self.assertRaisesRegex(LiveError, "IDEMPOTENCY_CONFLICT"):
            self.execute(restarted, speed=70)
        self.assertEqual(restarted.status()["revision"], 1)

    def test_full_process_restart_returns_original_result_without_second_effect(self):
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(ROOT / "src")
        command = [sys.executable, "-c", CHILD, str(self.database)]
        first_process = subprocess.run(
            command, cwd=ROOT, env=environment, capture_output=True,
            text=True, check=True, timeout=30,
        )
        first = json.loads(first_process.stdout)
        second_process = subprocess.run(
            command, cwd=ROOT, env=environment, capture_output=True,
            text=True, check=True, timeout=30,
        )
        second = json.loads(second_process.stdout)

        self.assertEqual(first["revision_after"], 1)
        self.assertEqual(second["revision_before"], 1)
        self.assertEqual(second["revision_after"], 1)
        self.assertEqual(second["result"], first["result"])
        self.assertEqual(second["before"], second["after"])
        self.assertNotEqual(first["before"], first["after"])
        connection = sqlite3.connect(self.database)
        try:
            row = connection.execute(
                "SELECT status, resulting_revision, correlation_id, scenario_id "
                "FROM processed_requests "
                "WHERE request_id='process-restart-request'"
            ).fetchone()
            self.assertEqual(row, (
                "COMMITTED", 1, "durable-process-correlation", "SCN-02",
            ))
            self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        finally:
            connection.close()

    def test_concurrent_duplicate_requests_commit_once(self):
        city = self.city()
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.execute(city), range(8)))

        self.assertTrue(all(result == results[0] for result in results))
        self.assertEqual(city.status()["revision"], 1)
        self.assertEqual(city._persistence.request_count(city.status()["run_id"]), 1)
        operation = city._persistence.manager.connection.execute(
            "SELECT status FROM operations WHERE request_id='durable-request'"
        ).fetchone()
        self.assertEqual(operation["status"], "DONE")

    def test_interrupted_request_is_not_replayed_after_restart(self):
        city = self.city()
        city._effect = lambda effect, *, persist=True, correlation_id=None: (_ for _ in ()).throw(
            RuntimeError("simulated interruption after durable intent")
        )
        with self.assertRaisesRegex(RuntimeError, "simulated interruption"):
            self.execute(city, request_id="interrupted-request")
        city.close()

        restarted = self.city()
        self.assertEqual(restarted.status()["revision"], 0)
        with self.assertRaisesRegex(LiveError, "RECOVERY_REQUIRED"):
            self.execute(restarted, request_id="interrupted-request")
        self.assertEqual(restarted.status()["revision"], 0)
        row = restarted._persistence.request("interrupted-request")
        self.assertEqual(row["status"], "RECOVERY_REQUIRED")

    def test_storage_failure_rolls_back_citycore_and_does_not_report_success(self):
        city = self.city()
        state_before = city.diagnostics()["physical_state"]
        with patch.object(
            city._persistence, "persist_transitions",
            side_effect=sqlite3.OperationalError("disk unavailable"),
        ):
            with self.assertRaisesRegex(LiveError, "PERSISTENCE_FAILURE"):
                self.execute(city)

        self.assertEqual(city.status()["revision"], 0)
        self.assertEqual(city.diagnostics()["physical_state"], state_before)
        with self.assertRaisesRegex(LiveError, "RECOVERY_REQUIRED"):
            self.execute(city)

    def test_quarantine_survives_owner_recreation(self):
        city = self.city()
        city.quarantine_device("PRT-PMP-01", "persistent safety restriction")
        city.close()
        restarted = self.city()

        self.assertTrue(restarted.is_device_isolated("PRT-PMP-01"))
        with self.assertRaisesRegex(LiveError, "GUARDIAN_RESTRICTED"):
            self.execute(restarted, request_id="quarantined-request")
        restarted.restore_quarantined_device("PRT-PMP-01")
        self.assertFalse(restarted.is_device_isolated("PRT-PMP-01"))

    def test_citycore_steps_and_reset_are_restored_from_persistent_active_run(self):
        city = self.city()
        city.step()
        self.assertEqual(city.status()["sim_tick"], 1)
        city.reset("durable-reset-run")
        self.assertEqual(city.status()["revision"], 0)
        city.close()

        restarted = self.city("unrelated-run-id")
        self.assertEqual(restarted.status()["run_id"], "durable-reset-run")
        self.assertEqual(restarted.status()["sim_tick"], 0)
        self.assertEqual(restarted.status()["revision"], 0)
        with self.assertRaisesRegex(LiveError, "RUN_ID_REUSED"):
            restarted.reset("durable-run")

    def test_sqlite_transaction_rolls_back_failed_changes(self):
        city = self.city()
        connection = city._persistence.manager.connection
        with self.assertRaisesRegex(RuntimeError, "abort transaction"):
            with city._persistence.manager.transaction() as transaction:
                transaction.execute(
                    "INSERT INTO recovery_metadata(key, value, updated_at) "
                    "VALUES ('temporary', 'value', 'now')"
                )
                raise RuntimeError("abort transaction")
        self.assertIsNone(connection.execute(
            "SELECT value FROM recovery_metadata WHERE key='temporary'"
        ).fetchone())

    def test_corrupted_persisted_replay_fails_closed(self):
        city = self.city()
        self.execute(city)
        city.close()
        connection = sqlite3.connect(self.database)
        try:
            connection.execute(
                "UPDATE core_runs SET final_digest=? WHERE run_id='durable-run'",
                ("0" * 64,),
            )
            connection.commit()
        finally:
            connection.close()

        with self.assertRaises(LiveError):
            LiveCity("durable-run", persistence_path=self.database)

    def test_database_owner_lock_prevents_two_live_cities(self):
        first = self.city()
        with self.assertRaisesRegex(RuntimeError, "PERSISTENCE_IN_USE"):
            LiveCity("second-owner", persistence_path=self.database)
        first.close()
        recovered = self.city()
        self.assertEqual(recovered.status()["state"], "READY")


if __name__ == "__main__":
    unittest.main()
