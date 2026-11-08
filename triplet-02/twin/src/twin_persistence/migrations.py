MIGRATIONS = (
    """
    CREATE TABLE core_runs (
        run_id TEXT PRIMARY KEY,
        metadata_json TEXT NOT NULL,
        header_json TEXT NOT NULL,
        initial_digest TEXT NOT NULL,
        final_context_json TEXT NOT NULL,
        final_digest TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE core_transitions (
        run_id TEXT NOT NULL REFERENCES core_runs(run_id) ON DELETE CASCADE,
        record_index INTEGER NOT NULL,
        record_json TEXT NOT NULL,
        PRIMARY KEY (run_id, record_index)
    );
    CREATE TABLE processed_requests (
        request_id TEXT PRIMARY KEY,
        command_hash TEXT NOT NULL,
        run_id TEXT NOT NULL,
        correlation_id TEXT,
        scenario_id TEXT,
        device_id TEXT NOT NULL,
        command_type TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        status TEXT NOT NULL,
        result_json TEXT,
        resulting_revision INTEGER,
        created_at TEXT NOT NULL,
        completed_at TEXT
    );
    CREATE TABLE operations (
        operation_id TEXT PRIMARY KEY,
        request_id TEXT NOT NULL UNIQUE REFERENCES processed_requests(request_id),
        operation_type TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        status TEXT NOT NULL,
        created_at TEXT NOT NULL,
        completed_at TEXT,
        error_code TEXT
    );
    CREATE TABLE recovery_metadata (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE quarantined_devices (
        device_key TEXT PRIMARY KEY,
        quarantine_json TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    """,
)
