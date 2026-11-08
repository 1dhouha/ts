from dataclasses import FrozenInstanceError, replace
from unittest.mock import patch
import unittest

from city_core import baseline_config, build_city, create_core
from city_core.transitions import digest


class LifecycleTests(unittest.TestCase):
    def test_uninitialized_and_initialize_once(self):
        core = create_core()
        for result in (core.metadata.read_metadata(), core.metadata.read_topology(),
                       core.observations.read_private("r-one"),
                       core.lifecycle.advance_one("r-one", 0),
                       core.lifecycle.reset("r-one", 0, "r-two"),
                       core.lifecycle.read_private_transitions("r-one", -1, 10),
                       core.metadata.make_evidence_token("r-one")):
            self.assertFalse(result.ok)
            self.assertEqual(result.error.code, "NOT_READY")
        self.assertFalse(core.lifecycle.initialize("bad run").ok)
        self.assertTrue(core.lifecycle.initialize("r-one").ok)
        self.assertEqual(core.lifecycle.initialize("r-two").error.code, "ALREADY_INITIALIZED")
        self.assertEqual(core.metadata.read_metadata().value.context.run_id, "r-one")

    def test_fixed_steps_and_immutable_held_state(self):
        core = build_city("r-one").value
        initial = core.observations.read_private("r-one").value
        for n in range(1, 4):
            result = core.lifecycle.advance_one("r-one", n - 1)
            self.assertTrue(result.ok)
            self.assertEqual((result.value.context.tick, result.value.context.sim_time_ms,
                              result.value.context.revision), (n, n * 1000, n))
            water = result.value.physical_state.water
            self.assertEqual(water.basin_volume_m3, 5800.0)
            self.assertEqual(water.surface_volume_m3, 0.0)
            self.assertAlmostEqual(water.cumulative_in_m3, n * 0.2)
            self.assertAlmostEqual(water.cumulative_out_m3, n * 0.2)
            roads = result.value.physical_state.roads
            for road_id, road in roads.items():
                self.assertAlmostEqual(road.queue_vehicles, 10.0 - n * .3)
                self.assertEqual(road.arrival_rate_veh_s, .2)
                self.assertEqual(road.obstruction_fraction, 0)
            air = result.value.physical_state.air
            for zone, record in air.items():
                source = {'Z-INDUSTRIAL': .2, 'Z-RESIDENTIAL': .05}.get(zone, 0)
                expected = 10 + source * sum((1/1.01)**k for k in range(n))
                self.assertAlmostEqual(record.pm25_ug_m3, expected)
                self.assertEqual(record.external_source_ug_m3_s, 0)
            self.assertEqual(result.value.physical_state, replace(initial.physical_state, water=water, roads=roads, air=air))
            self.assertEqual(core.metadata.read_metadata().value.context, result.value.context)
        self.assertEqual(initial.context.tick, 0)
        self.assertEqual(initial.context.revision, 0)
        self.assertIsNone(initial.physical_state.water.last_step)
        self.assertEqual(initial.physical_state.water.cumulative_in_m3, 0.0)
        self.assertEqual(initial.physical_state.roads['R-LOW'].queue_vehicles, 10.0)
        self.assertEqual(initial.physical_state.air['Z-INDUSTRIAL'].pm25_ug_m3, 10.0)

    def test_invalid_and_late_steps_are_atomic(self):
        core = build_city("r-one").value
        initial = core.observations.read_private("r-one").value
        for duration in (-1, 0, 500, 2000, 1000.0, True, None, "1000", float("nan"), float("inf")):
            result = core.lifecycle.advance_one("r-one", 0, duration_ms=duration)
            self.assertEqual(result.error.code, "INVALID_ARGUMENT")
        for revision in (-1, True, 0.0, None, "0"):
            self.assertEqual(core.lifecycle.advance_one("r-one", revision).error.code,
                             "INVALID_ARGUMENT")
        self.assertEqual(core.lifecycle.advance_one("r-old", 0).error.code, "OLD_RUN")
        self.assertEqual(core.lifecycle.advance_one("bad run", 0).error.code, "INVALID_ARGUMENT")
        self.assertEqual(initial, core.observations.read_private("r-one").value)
        core.lifecycle.advance_one("r-one", 0)
        conflict = core.lifecycle.advance_one("r-one", 0)
        self.assertEqual(conflict.error.code, "STATE_CONFLICT")
        self.assertTrue(conflict.error.retryable)
        self.assertEqual(len(core.lifecycle.read_private_transitions("r-one", -1, 10).value.records), 1)

    def test_reset_restores_baseline_and_never_reuses_run_ids(self):
        config = baseline_config()
        config["initial_physical"]["water"]["basin_volume_m3"] = 6000
        core = build_city("r-one", initial_config=config).value
        core.lifecycle.advance_one("r-one", 0)
        held = core.observations.read_private("r-one").value
        receipt = core.lifecycle.reset("r-one", 1, "r-two")
        self.assertTrue(receipt.ok)
        self.assertEqual(receipt.value.status, "READY")
        fresh = build_city("r-fresh").value.observations.read_private("r-fresh").value
        new = core.observations.read_private("r-two").value
        self.assertEqual(new.physical_state, fresh.physical_state)
        self.assertEqual((new.context.tick, new.context.revision, new.context.sim_time_ms), (0, 0, 0))
        self.assertEqual(held.physical_state.water.basin_volume_m3, 6000)
        self.assertEqual(core.lifecycle.advance_one("r-one", 1).error.code, "OLD_RUN")
        self.assertEqual(core.observations.read_private("r-one").error.code, "OLD_RUN")
        self.assertEqual(core.lifecycle.read_private_transitions("r-two", -1, 10).value.records, ())
        self.assertEqual(core.lifecycle.reset("r-two", 0, "r-one").error.code, "RUN_ID_REUSED")
        self.assertEqual(core.lifecycle.reset("r-two", 0, "r-two").error.code, "RUN_ID_REUSED")
        self.assertTrue(core.lifecycle.reset("r-two", 0, "r-three").ok)

    def test_failed_reset_preserves_state_history_and_identity_budget(self):
        core = build_city("r-one").value
        core.lifecycle.advance_one("r-one", 0)
        before = core.lifecycle.read_private_transitions("r-one", -1, 10).value
        for old, revision, new, config, code in (
                ("r-old", 1, "r-two", baseline_config(), "OLD_RUN"),
                ("r-one", 0, "r-two", baseline_config(), "STATE_CONFLICT"),
                ("r-one", True, "r-two", baseline_config(), "INVALID_ARGUMENT"),
                ("r-one", 1, "bad run", baseline_config(), "INVALID_ARGUMENT"),
                ("r-one", 1, "r-two", {}, "INVALID_ARGUMENT"),
                ("r-one", 1, "r-two", None, "INVALID_ARGUMENT")):
            result = core.lifecycle.reset(old, revision, new, initial_config=config)
            self.assertEqual(result.error.code, code)
        self.assertEqual(before, core.lifecycle.read_private_transitions("r-one", -1, 10).value)
        self.assertTrue(core.lifecycle.reset("r-one", 1, "r-two").ok)

    def test_record_pagination_and_exact_frozen_initial_inputs(self):
        config = baseline_config()
        core = build_city("r-one", initial_config=config).value
        initial_hash = digest(core.observations.read_private("r-one").value.physical_state)
        config["initial_physical"]["water"]["basin_volume_m3"] = 0
        empty = core.lifecycle.read_private_transitions("r-one", -1, 1).value
        self.assertTrue(empty.at_end)
        self.assertEqual(empty.next_after_index, -1)
        self.assertEqual(empty.header.initial_config["initial_physical"]["water"]["basin_volume_m3"], 5800)
        with self.assertRaises(TypeError):
            empty.header.initial_config["initial_physical"]["water"]["basin_volume_m3"] = 1
        for n in range(260):
            self.assertTrue(core.lifecycle.advance_one("r-one", n).ok)
        first = core.lifecycle.read_private_transitions("r-one", -1, 257).value
        self.assertFalse(first.at_end)
        self.assertEqual(first.next_after_index, 256)
        last = core.lifecycle.read_private_transitions("r-one", 256, 10).value
        self.assertEqual([r.record_index for r in last.records], [257, 258, 259])
        self.assertTrue(last.at_end)
        for i, record in enumerate(first.records):
            self.assertEqual(record.context.revision, i + 1)
            self.assertEqual(record.input.expected_revision, i)
            self.assertEqual(record.input.duration_ms, 1000)
            self.assertNotEqual(record.before_digest, record.after_digest)
            self.assertEqual(record.before_digest, initial_hash if i == 0 else first.records[i - 1].after_digest)
            self.assertEqual(record.cause, "STEP")
        with self.assertRaises(FrozenInstanceError):
            first.records[0].record_index = 2
        for after in (-2, 260):
            self.assertEqual(core.lifecycle.read_private_transitions("r-one", after, 1).error.code,
                             "CURSOR_INVALID")
        for after, limit in ((True, 1), (0.0, 1), (-1, 0),
                             (-1, 1001), (-1, True), (-1, 1.0), (None, 1)):
            self.assertEqual(core.lifecycle.read_private_transitions("r-one", after, limit).error.code,
                             "INVALID_ARGUMENT")
        core.lifecycle.reset("r-one", 260, "r-two")
        self.assertEqual(core.lifecycle.read_private_transitions("r-one", -1, 1).error.code, "OLD_RUN")
        self.assertEqual(len(first.records), 257)

    def test_limits_reject_without_silent_eviction(self):
        core = build_city("r-one").value
        with patch("city_core.lifecycle.MAX_TICKS", 1):
            self.assertTrue(core.lifecycle.advance_one("r-one", 0).ok)
            self.assertEqual(core.lifecycle.advance_one("r-one", 1).error.code, "MODEL_LIMIT")
        with patch("city_core.lifecycle.MAX_RECORDS", 1):
            self.assertEqual(core.lifecycle.advance_one("r-one", 1).error.code, "MODEL_LIMIT")
        with patch("city_core.lifecycle.MAX_RUNS", 1):
            self.assertEqual(core.lifecycle.reset("r-one", 1, "r-two").error.code, "MODEL_LIMIT")
        self.assertEqual(core.observations.read_private("r-one").value.context.revision, 1)

    def test_data_unavailable_has_safe_error(self):
        with patch("city_core.lifecycle.baseline_config", side_effect=OSError("private path")):
            result = build_city("r-one")
        self.assertEqual(result.error.code, "NOT_READY")
        self.assertNotIn("private", result.error.safe_message)

    def test_end_cursor_polling_and_custom_reset_inputs(self):
        core = build_city("r-one").value
        core.lifecycle.advance_one("r-one", 0)
        end = core.lifecycle.read_private_transitions("r-one", 0, 1).value
        self.assertEqual((end.records, end.next_after_index, end.at_end), ((), 0, True))
        core.lifecycle.advance_one("r-one", 1)
        next_page = core.lifecycle.read_private_transitions("r-one", end.next_after_index, 1).value
        self.assertEqual(next_page.records[0].record_index, 1)
        config = baseline_config()
        config["initial_physical"]["water"]["surface_volume_m3"] = 200
        self.assertTrue(core.lifecycle.reset("r-one", 2, "r-two", initial_config=config).ok)
        config["initial_physical"]["water"]["surface_volume_m3"] = 0
        self.assertEqual(core.observations.read_private("r-two").value.physical_state.water.surface_volume_m3, 200)
        with patch("city_core.lifecycle.baseline_config", side_effect=OSError("private")):
            result = core.lifecycle.reset("r-two", 0, "r-three")
        self.assertEqual(result.error.code, "NOT_READY")
        self.assertTrue(core.lifecycle.reset("r-two", 0, "r-three").ok)


if __name__ == "__main__":
    unittest.main()
