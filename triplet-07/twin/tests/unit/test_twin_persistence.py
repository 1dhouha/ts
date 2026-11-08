import tempfile
import unittest
from pathlib import Path

from city_core import build_city
from city_core.catalog import CONTRACT_VERSION
from city_core.replay import export_replay
from city_core.transitions import plain
from city_live.commands import build_command_effect
from city_live.records import require
from twin_persistence import SQLiteTwinPersistence
from twin_persistence.store import command_fingerprint


class SQLiteTwinPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "twin.sqlite3"

    def test_request_and_replay_commit_share_one_durable_store(self):
        run_id = "persistence-unit-run"
        core = require(build_city(run_id))
        initial = plain(require(export_replay(core.lifecycle, run_id)))
        store = SQLiteTwinPersistence(self.path)
        self.addCleanup(store.close)

        store.initialize_run(initial)
        store.initialize_run(initial)
        payload = {"device_id": "PRT-PMP-01", "speed_pct": 45}
        fingerprint = command_fingerprint(payload)
        store.claim_request(
            request_id="unit-request",
            command_hash=fingerprint,
            run_id=run_id,
            correlation_id="unit-correlation",
            scenario_id="SCN-02",
            device_id="PRT-PMP-01",
            command_type="set_speed",
            payload=payload,
        )
        effect = build_command_effect(
            "PRT-PMP-01", "set_speed", {"speed_pct": 45},
        )
        self.assertTrue(core.actuators.apply_effect({
            "contract_version": CONTRACT_VERSION,
            "run_id": run_id,
            "effect_id": "unit-effect",
            "expected_revision": 0,
            "source": "B_ACTUATOR",
            "correlation_id": "unit-correlation",
            "effect": effect,
        }).ok)
        updated = plain(require(export_replay(core.lifecycle, run_id)))
        store.persist_transitions(
            run_id,
            [plain(record) for record in updated["records"]],
            updated["final_context"],
            updated["final_digest"],
            request_id="unit-request",
            result={"status": "APPLIED", "effective_revision": 1},
        )

        request = store.request("unit-request")
        self.assertEqual(request["command_hash"], fingerprint)
        self.assertEqual(request["correlation_id"], "unit-correlation")
        self.assertEqual(request["scenario_id"], "SCN-02")
        self.assertEqual(request["status"], "COMMITTED")
        self.assertEqual(store.duplicate_result(request, fingerprint), {
            "status": "APPLIED", "effective_revision": 1,
        })
        self.assertEqual(store.load_active_replay()[1]["final_context"]["revision"], 1)
        self.assertEqual(store.request_count(run_id), 1)
        self.assertTrue(store.run_exists(run_id))
        self.assertFalse(store.run_exists("missing-run"))
        self.assertEqual(store.run_count(), 1)

    def test_recovery_quarantine_owner_lock_and_transaction_rollback(self):
        store = SQLiteTwinPersistence(self.path)
        self.addCleanup(store.close)
        run_id = "recovery-unit-run"
        core = require(build_city(run_id))
        store.initialize_run(plain(require(export_replay(core.lifecycle, run_id))))
        store.claim_request(
            request_id="pending-unit-request",
            command_hash=command_fingerprint({"command": "pending"}),
            run_id=run_id,
            correlation_id=None,
            scenario_id=None,
            device_id="PRT-PMP-01",
            command_type="set_speed",
            payload={"command": "pending"},
        )
        store.save_quarantine("actuator:pump-01", {"reason": "test"})
        self.assertEqual(store.quarantines(), {
            "actuator:pump-01": {"reason": "test"},
        })
        with self.assertRaisesRegex(RuntimeError, "PERSISTENCE_IN_USE"):
            SQLiteTwinPersistence(self.path)

        connection = store.manager.connection
        with self.assertRaisesRegex(RuntimeError, "rollback"):
            with store.manager.transaction() as transaction:
                transaction.execute(
                    "INSERT INTO recovery_metadata(key, value, updated_at) "
                    "VALUES ('rollback-test', 'value', 'now')"
                )
                raise RuntimeError("rollback")
        self.assertIsNone(connection.execute(
            "SELECT value FROM recovery_metadata WHERE key='rollback-test'"
        ).fetchone())

        store.delete_quarantine("actuator:pump-01")
        self.assertEqual(store.quarantines(), {})
        store.recover_incomplete_requests()
        request = store.request("pending-unit-request")
        self.assertEqual(request["status"], "RECOVERY_REQUIRED")
        with self.assertRaisesRegex(ValueError, "RECOVERY_REQUIRED"):
            store.duplicate_result(request, request["command_hash"])


if __name__ == "__main__":
    unittest.main()
