"""Comprehensive unit and security verification suite for Twin Observability [R2, R3]."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
os.environ.setdefault("TWIN_ALLOW_INSECURE_DEV_TOKENS", "1")

from twin_interface.auth import Role, DEFAULT_TOKENS
from twin_interface.facade import TwinFacade
from twin_interface.rest.router import handle_rest_request
from twin_observability.event_store import EventStore
from twin_observability.scenario_log import ScenarioLogger
from twin_observability.seq import AtomicSequencer, EventRecord, create_event, utc_now_iso
from twin_observability.timeline import format_sse_message, format_timeline_item


class ObservabilityTests(unittest.TestCase):
    """Test suite covering AtomicSequencer, EventStore, ScenarioLogger, and Live Timeline."""

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="test_obs_"))

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    # --------------------------------------------------------------------------
    # 1. ATOMIC SEQUENCER TESTS
    # --------------------------------------------------------------------------

    def test_atomic_sequencer_monotonic_and_reset(self) -> None:
        """Verify strict monotonic sequencing, boundary checking, and reset."""
        seq = AtomicSequencer(start=0)
        self.assertEqual(seq.current_seq(), 0)
        self.assertEqual(seq.next_seq(), 0)
        self.assertEqual(seq.next_seq(), 1)
        self.assertEqual(seq.next_seq(), 2)
        self.assertEqual(seq.current_seq(), 3)

        seq.reset(100)
        self.assertEqual(seq.current_seq(), 100)
        self.assertEqual(seq.next_seq(), 100)
        self.assertEqual(seq.next_seq(), 101)

        with self.assertRaises(ValueError):
            AtomicSequencer(start=-1)

        with self.assertRaises(ValueError):
            seq.reset(-5)

    def test_atomic_sequencer_multithreaded_concurrency(self) -> None:
        """Verify 10 concurrent threads hammering the sequencer produce zero duplicate IDs."""
        sequencer = AtomicSequencer(start=0)
        results: list[int] = []
        lock = threading.Lock()

        def worker() -> None:
            for _ in range(200):
                val = sequencer.next_seq()
                with lock:
                    results.append(val)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(results), 2000)
        self.assertEqual(len(set(results)), 2000, "Sequence collision detected in multi-threaded run!")
        self.assertEqual(sorted(results), list(range(2000)))

    def test_event_record_and_factory(self) -> None:
        """Verify EventRecord dataclass and create_event factory validations."""
        rec = EventRecord(
            seq=1,
            sim_tick=10,
            event_type="TEST_EVENT",
            source="test_source",
            payload={"key": "val"},
            timestamp="2026-10-25T15:00:00Z",
        )
        d = rec.to_dict()
        self.assertEqual(d["seq"], 1)
        self.assertEqual(d["sim_tick"], 10)
        self.assertEqual(d["payload"]["key"], "val")

        # Factory validation
        ev = create_event(seq=0, sim_tick=0, event_type="ALERT", source="core", payload={})
        self.assertIn("timestamp", ev)

        with self.assertRaises(ValueError):
            create_event(seq=-1, sim_tick=0, event_type="A", source="B", payload={})
        with self.assertRaises(ValueError):
            create_event(seq=0, sim_tick=-1, event_type="A", source="B", payload={})
        with self.assertRaises(ValueError):
            create_event(seq=0, sim_tick=0, event_type="", source="B", payload={})
        with self.assertRaises(ValueError):
            create_event(seq=0, sim_tick=0, event_type="A", source="", payload={})

    # --------------------------------------------------------------------------
    # 2. EVENT STORE (RING BUFFER) TESTS
    # --------------------------------------------------------------------------

    def test_event_store_ring_buffer_overflow_and_stats(self) -> None:
        """Verify bounded deque eviction and statistics."""
        store = EventStore(capacity=5)
        self.assertEqual(store.capacity, 5)
        self.assertEqual(store.total_recorded, 0)

        # Append 8 events into capacity-5 buffer
        for i in range(8):
            store.append(sim_tick=i, event_type="SAMPLE", source="core", payload={"i": i})

        self.assertEqual(store.total_recorded, 8)
        stats = store.stats()
        self.assertEqual(stats["current_size"], 5)
        self.assertEqual(stats["oldest_retained_seq"], 4)  # seqs 1, 2, 3 evicted
        self.assertEqual(stats["newest_retained_seq"], 8)
        self.assertTrue(store.has_history_gap(1))
        self.assertFalse(store.has_history_gap(5))

        # Test clear
        store.clear()
        self.assertEqual(store.total_recorded, 0)
        self.assertEqual(len(store.get_latest(10)), 0)

        with self.assertRaises(ValueError):
            EventStore(capacity=0)

    def test_event_store_cursor_queries_and_role_projections(self) -> None:
        """Verify cursor-based queries and anti-leak projection on store reads."""
        store = EventStore(capacity=100)
        store.append(
            sim_tick=1,
            event_type="SENSOR_EVENT",
            source="core",
            payload={"sensor_id": "PRT-WLV-01", "reported_value": 75.0, "actual_value": 110.0, "fault": "DRIFT"},
        )
        store.append(
            sim_tick=2,
            event_type="SENSOR_EVENT",
            source="core",
            payload={"sensor_id": "PRT-WLV-02", "reported_value": 76.0, "actual_value": 76.0},
        )

        # 1. Query as Brain with cursor_seq=1 -> returns event with seq=2 (actual_value and fault stripped)
        brain_events = store.get_events_since(cursor_seq=1, limit=10, role=Role.BRAIN)
        self.assertEqual(len(brain_events), 1)
        payload = brain_events[0]["payload"]
        self.assertEqual(payload["sensor_id"], "PRT-WLV-02")
        self.assertNotIn("actual_value", payload)
        self.assertNotIn("fault", payload)

        # Query all with cursor_seq=0 -> returns both events
        all_brain = store.get_events_since(cursor_seq=0, limit=10, role=Role.BRAIN)
        self.assertEqual(len(all_brain), 2)

        # Query all from cursor -1 (invalid)
        with self.assertRaises(ValueError):
            store.get_events_since(cursor_seq=-1)
        with self.assertRaises(ValueError):
            store.get_events_since(limit=0)
        with self.assertRaises(ValueError):
            store.get_latest(limit=0)

        # 2. Query as Evaluator -> Ground truth preserved
        all_eval = store.get_latest(limit=5, role=Role.EVALUATOR)
        self.assertEqual(len(all_eval), 2)
        first_payload = all_eval[0]["payload"]
        self.assertIn("actual_value", first_payload)

        # Test append_raw
        raw = {"seq": 99, "sim_tick": 5, "event_type": "RAW", "source": "ext", "payload": {}}
        store.append_raw(raw)
        self.assertEqual(store.total_recorded, 3)

        with self.assertRaises(ValueError):
            store.append_raw({"sim_tick": 1})  # missing seq

    # --------------------------------------------------------------------------
    # 3. SCENARIO LOGGER (REQUIREMENT R2) TESTS
    # --------------------------------------------------------------------------

    def test_scenario_logger_append_and_zero_ground_truth_leak(self) -> None:
        """Verify Requirement R2: append-only JSONL produced with zero leaked Ground Truth."""
        logger = ScenarioLogger(
            scenario="flood_test",
            run_id="run_r2_audit",
            log_dir=self.temp_dir,
        )

        # Log complete incident lifecycle
        logger.log_start(sim_tick=0, config={"rainfall_rate": "heavy"})
        logger.log_telemetry_pulse(
            sim_tick=1,
            readings=[
                {
                    "sensor_id": "PRT-WLV-01",
                    "zone_id": "PRT",
                    "sensor_type": "water_level",
                    "reported_value": 75.5,
                    "unit": "cm",
                    "status": "OK",
                    # Injected private forbidden fields (Must be filtered out!)
                    "actual_value": 115.0,
                    "delta": 39.5,
                    "fault": "SENSOR_OFFSET_INJECTION",
                }
            ],
        )
        logger.log_command(
            sim_tick=2,
            device_id="PRT-PMP-02",
            command="start",
            params={"speed_pct": 100},
            outcome="APPLIED",
            request_id="req-999",
        )
        logger.log_quarantine(
            sim_tick=3,
            device_id="PRT-WLV-01",
            action="ISOLATED",
            reason="TELEMETRY_DISCREPANCY",
        )
        logger.log_end(sim_tick=4, summary={"flood_contained": True})

        self.assertEqual(logger.line_count, 5)
        logger.close()

        # Reopen with context manager to append another event
        with ScenarioLogger(scenario="flood_test", run_id="run_r2_audit", log_dir=self.temp_dir) as appended_logger:
            appended_logger.log_quarantine(sim_tick=5, device_id="PRT-WLV-01", action="RESTORED", reason="restored")

        # Now strictly audit the physical file on disk
        log_file = self.temp_dir / "flood_test_run_r2_audit.jsonl"
        self.assertTrue(log_file.exists())

        lines = log_file.read_text(encoding="utf-8").strip().split("\n")
        self.assertEqual(len(lines), 6)

        # STRICT ANTI-LEAK ASSERTION (Requirement R2 Audit)
        forbidden_keys = ("actual_value", "delta", "fault")
        for line_idx, line_str in enumerate(lines):
            self.assertTrue(line_str, f"Line {line_idx} is empty!")
            record = json.loads(line_str)
            # Ensure the line parses into valid JSON
            self.assertIn("seq", record)
            self.assertIn("sim_tick", record)
            self.assertIn("event_type", record)

            # Assert forbidden ground-truth fields never appear in raw string or JSON structure
            for forbidden in forbidden_keys:
                self.assertNotIn(
                    f'"{forbidden}"',
                    line_str,
                    f"SECURITY BREACH: Found private ground truth field '{forbidden}' in line {line_idx}!",
                )

        # Validation errors on construction
        with self.assertRaises(ValueError):
            ScenarioLogger(scenario="", run_id="run1", log_dir=self.temp_dir)
        with self.assertRaises(ValueError):
            ScenarioLogger(scenario="flood", run_id="", log_dir=self.temp_dir)

        # Error writing to closed logger
        closed_log = ScenarioLogger("s", "r", log_dir=self.temp_dir)
        closed_log.close()
        with self.assertRaises(RuntimeError):
            closed_log.log_start()

    # --------------------------------------------------------------------------
    # 4. LIVE TIMELINE FORMATTER (REQUIREMENT R3) TESTS
    # --------------------------------------------------------------------------

    def test_timeline_formatting_and_sse_packet(self) -> None:
        """Verify live timeline card generation and SSE packet serialization [Requirement R3]."""
        events = [
            {"event_type": "RUN_START", "source": "system", "payload": {"scenario": "flood", "run_id": "r1"}},
            {"event_type": "RUN_END", "source": "system", "payload": {"total_ticks": 100}},
            {"event_type": "TELEMETRY_PULSE", "source": "core", "payload": {"sensor_count": 12}, "sim_tick": 2},
            {
                "event_type": "ACTUATOR_COMMAND",
                "source": "brain",
                "payload": {"device_id": "PRT-PMP-02", "command": "start", "outcome": "APPLIED"},
            },
            {
                "event_type": "QUARANTINE_ACTION",
                "source": "guardian",
                "payload": {"device_id": "PRT-WLV-01", "action": "ISOLATED", "reason": "DISCREPANCY"},
            },
            {
                "event_type": "QUARANTINE_ACTION",
                "source": "guardian",
                "payload": {"device_id": "PRT-WLV-01", "action": "RESTORED", "reason": "repaired"},
            },
            {
                "event_type": "ALERT",
                "source": "core",
                "payload": {"title": "Water Level High", "description": "Exceeded 80cm", "level": "CRITICAL"},
            },
            {"event_type": "CUSTOM_UNKNOWN", "source": "custom", "payload": {"foo": "bar"}},
        ]

        for ev in events:
            card = format_timeline_item(ev)
            self.assertIn("badge", card)
            self.assertIn("title", card)
            self.assertIn("summary", card)
            self.assertIn("level", card)

            # Test SSE packet encoding
            sse = format_sse_message(card, event_name="timeline_pulse")
            self.assertTrue(sse.startswith("event: timeline_pulse\ndata: {"))
            self.assertTrue(sse.endswith("\n\n"))

    # --------------------------------------------------------------------------
    # 5. TWIN FACADE & REST INTEGRATION TESTS
    # --------------------------------------------------------------------------

    def test_facade_and_rest_observability_pipeline(self) -> None:
        """Verify TwinFacade and REST router dispatch into EventStore and ScenarioLogger."""
        store = EventStore(capacity=50)
        logger = ScenarioLogger("incident_test", "run_obs_facade", log_dir=self.temp_dir)
        facade = TwinFacade(event_store=store, scenario_logger=logger)

        # 1. Guardian isolates device
        iso_res = facade.isolate_device(
            token=DEFAULT_TOKENS[Role.GUARDIAN],
            device_id="IND-AIR-01",
            reason="PM2.5 Sensor stuck high",
        )
        self.assertTrue(iso_res["ok"])

        # 2. Brain commands actuator
        cmd_res = facade.send_actuator_command(
            token=DEFAULT_TOKENS[Role.BRAIN],
            device_id="PRT-PMP-02",
            command="start",
            params={"speed_pct": 100},
            request_id="obs-cmd-1",
        )
        self.assertTrue(cmd_res["ok"])

        # 3. Guardian restores device
        rest_res = facade.restore_device(
            token=DEFAULT_TOKENS[Role.GUARDIAN],
            device_id="IND-AIR-01",
        )
        self.assertTrue(rest_res["ok"])

        # Check EventStore retained all 3 actions
        self.assertEqual(store.total_recorded, 3)

        # Check Facade get_events_since reads from EventStore
        ev_envelope = facade.get_events_since(token=DEFAULT_TOKENS[Role.OPERATOR], seq=0)
        self.assertTrue(ev_envelope["ok"])
        self.assertEqual(ev_envelope["data"]["count"], 3)

        # 4. REST GET /api/v1/timeline endpoint
        status_code, rest_timeline = handle_rest_request(
            facade=facade,
            method="GET",
            path="/api/v1/timeline?seq=0",
            headers={"Authorization": f"Bearer {DEFAULT_TOKENS[Role.OPERATOR]}"},
        )
        self.assertEqual(status_code, 200)
        self.assertTrue(rest_timeline["ok"])
        self.assertEqual(rest_timeline["data"]["count"], 3)
        cards = rest_timeline["data"]["timeline"]
        self.assertEqual(cards[0]["badge"], "QUARANTINE")
        self.assertEqual(cards[1]["badge"], "ACTION")
        self.assertEqual(cards[2]["badge"], "RESTORED")

        # REST unauthorized check on timeline
        unauth_code, _ = handle_rest_request(
            facade=facade,
            method="GET",
            path="/api/v1/timeline",
            headers={"Authorization": "Bearer bad-token"},
        )
        self.assertEqual(unauth_code, 401)

        logger.close()


if __name__ == "__main__":
    unittest.main()
