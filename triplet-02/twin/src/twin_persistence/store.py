from datetime import datetime, timezone
import hashlib
import json

from .connection import SQLiteConnectionManager
from .migrations import MIGRATIONS


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _json(value):
    return json.dumps(value, sort_keys=True, allow_nan=False, separators=(",", ":"))


class SQLiteTwinPersistence:
    def __init__(self, path):
        self.manager = SQLiteConnectionManager(path)
        try:
            self._migrate()
        except BaseException:
            self.manager.close()
            raise

    @property
    def path(self):
        return self.manager.path

    def _migrate(self):
        connection = self.manager.connection
        connection.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations "
            "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        current = connection.execute(
            "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
        ).fetchone()[0]
        for index, migration in enumerate(MIGRATIONS, 1):
            if index <= current:
                continue
            with self.manager.transaction() as transaction:
                for statement in migration.split(";"):
                    if statement.strip():
                        transaction.execute(statement)
                transaction.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                    (index, _now()),
                )

    def active_run_id(self):
        row = self.manager.connection.execute(
            "SELECT value FROM recovery_metadata WHERE key='active_run_id'"
        ).fetchone()
        return row["value"] if row else None

    def run_exists(self, run_id):
        return self.manager.connection.execute(
            "SELECT 1 FROM core_runs WHERE run_id=?", (run_id,)
        ).fetchone() is not None

    def run_count(self):
        return self.manager.connection.execute(
            "SELECT COUNT(*) FROM core_runs"
        ).fetchone()[0]

    def initialize_run(self, replay):
        header = replay["header"]
        run_id = header["initial_context"]["run_id"]
        with self.manager.transaction() as connection:
            exists = connection.execute(
                "SELECT 1 FROM core_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if exists is None:
                initial = replay["final_context"]
                connection.execute(
                    "INSERT INTO core_runs(run_id, metadata_json, header_json, "
                    "initial_digest, final_context_json, final_digest, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        run_id,
                        _json({key: replay[key] for key in (
                            "format_version", "model_profile", "python_version",
                            "numeric_profile", "randomness",
                        )}),
                        _json(header),
                        replay["final_digest"],
                        _json(initial),
                        replay["final_digest"],
                        _now(),
                    ),
                )
            connection.execute(
                "INSERT INTO recovery_metadata(key, value, updated_at) "
                "VALUES ('active_run_id', ?, ?) ON CONFLICT(key) DO UPDATE SET "
                "value=excluded.value, updated_at=excluded.updated_at",
                (run_id, _now()),
            )
            connection.execute("DELETE FROM quarantined_devices")

    def load_active_replay(self):
        run_id = self.active_run_id()
        if run_id is None:
            return None
        run = self.manager.connection.execute(
            "SELECT * FROM core_runs WHERE run_id=?", (run_id,)
        ).fetchone()
        if run is None:
            raise RuntimeError("PERSISTENCE_CORRUPT")
        records = self.manager.connection.execute(
            "SELECT record_json FROM core_transitions WHERE run_id=? "
            "ORDER BY record_index", (run_id,),
        ).fetchall()
        replay = json.loads(run["metadata_json"])
        replay.update({
            "header": json.loads(run["header_json"]),
            "records": [json.loads(row["record_json"]) for row in records],
            "final_context": json.loads(run["final_context_json"]),
            "final_digest": run["final_digest"],
        })
        if len(replay["records"]) != replay["final_context"]["revision"]:
            raise RuntimeError("PERSISTENCE_CORRUPT")
        return run_id, replay

    def recover_incomplete_requests(self):
        with self.manager.transaction() as connection:
            now = _now()
            connection.execute(
                "UPDATE processed_requests SET status='RECOVERY_REQUIRED' "
                "WHERE status='EXECUTING'"
            )
            connection.execute(
                "UPDATE operations SET status='RECOVERY_REQUIRED', completed_at=?, "
                "error_code='PROCESS_INTERRUPTED' WHERE status IN ('PENDING','DOING')",
                (now,),
            )

    def request(self, request_id):
        row = self.manager.connection.execute(
            "SELECT * FROM processed_requests WHERE request_id=?", (request_id,)
        ).fetchone()
        return dict(row) if row else None

    def request_count(self, run_id):
        return self.manager.connection.execute(
            "SELECT COUNT(*) FROM processed_requests WHERE run_id=?", (run_id,)
        ).fetchone()[0]

    def claim_request(self, *, request_id, command_hash, run_id, correlation_id,
                      scenario_id, device_id, command_type, payload):
        now = _now()
        with self.manager.transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM processed_requests WHERE request_id=?", (request_id,)
            ).fetchone()
            if existing is not None:
                return dict(existing)
            connection.execute(
                "INSERT INTO processed_requests(request_id, command_hash, run_id, "
                "correlation_id, scenario_id, device_id, command_type, payload_json, "
                "status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'EXECUTING', ?)",
                (request_id, command_hash, run_id, correlation_id, scenario_id,
                 device_id, command_type, _json(payload), now),
            )
            connection.execute(
                "INSERT INTO operations(operation_id, request_id, operation_type, "
                "payload_json, status, created_at) VALUES (?, ?, 'ACTUATOR_COMMAND', ?, "
                "'DOING', ?)",
                (f"command:{request_id}", request_id, _json(payload), now),
            )
        return None

    def persist_transitions(self, run_id, records, final_context, final_digest,
                            *, request_id=None, result=None):
        now = _now()
        with self.manager.transaction() as connection:
            current = connection.execute(
                "SELECT final_context_json FROM core_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if current is None:
                raise RuntimeError("PERSISTENCE_RUN_NOT_FOUND")
            expected_index = json.loads(current["final_context_json"])["revision"]
            for record in records:
                if record["record_index"] != expected_index:
                    raise RuntimeError("PERSISTENCE_REVISION_CONFLICT")
                connection.execute(
                    "INSERT INTO core_transitions(run_id, record_index, record_json) "
                    "VALUES (?, ?, ?)",
                    (run_id, expected_index, _json(record)),
                )
                expected_index += 1
            if final_context["revision"] != expected_index:
                raise RuntimeError("PERSISTENCE_REVISION_CONFLICT")
            connection.execute(
                "UPDATE core_runs SET final_context_json=?, final_digest=?, updated_at=? "
                "WHERE run_id=?",
                (_json(final_context), final_digest, now, run_id),
            )
            if request_id is not None:
                if result is None:
                    raise RuntimeError("PERSISTENCE_RESULT_REQUIRED")
                connection.execute(
                    "UPDATE processed_requests SET status='COMMITTED', result_json=?, "
                    "resulting_revision=?, completed_at=? WHERE request_id=? "
                    "AND status='EXECUTING'",
                    (_json(result), final_context["revision"], now, request_id),
                )
                if connection.execute("SELECT changes()").fetchone()[0] != 1:
                    raise RuntimeError("PERSISTENCE_REQUEST_STATE")
                connection.execute(
                    "UPDATE operations SET status='DONE', completed_at=? "
                    "WHERE request_id=? AND status='DOING'",
                    (now, request_id),
                )
                if connection.execute("SELECT changes()").fetchone()[0] != 1:
                    raise RuntimeError("PERSISTENCE_REQUEST_STATE")

    def duplicate_result(self, request, command_hash):
        if request["command_hash"] != command_hash:
            raise ValueError("IDEMPOTENCY_CONFLICT")
        if request["status"] == "COMMITTED":
            return json.loads(request["result_json"])
        raise ValueError("RECOVERY_REQUIRED")

    def quarantines(self):
        rows = self.manager.connection.execute(
            "SELECT device_key, quarantine_json FROM quarantined_devices"
        ).fetchall()
        return {row["device_key"]: json.loads(row["quarantine_json"]) for row in rows}

    def save_quarantine(self, device_key, quarantine):
        with self.manager.transaction() as connection:
            connection.execute(
                "INSERT INTO quarantined_devices(device_key, quarantine_json, updated_at) "
                "VALUES (?, ?, ?) ON CONFLICT(device_key) DO UPDATE SET "
                "quarantine_json=excluded.quarantine_json, updated_at=excluded.updated_at",
                (device_key, _json(quarantine), _now()),
            )

    def delete_quarantine(self, device_key):
        with self.manager.transaction() as connection:
            connection.execute(
                "DELETE FROM quarantined_devices WHERE device_key=?", (device_key,)
            )

    def close(self):
        self.manager.close()


def command_fingerprint(payload):
    return hashlib.sha256(_json(payload).encode("utf-8")).hexdigest()
