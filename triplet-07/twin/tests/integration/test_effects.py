from dataclasses import replace
import json
from pathlib import Path
import re
import unittest

from city_core import create_core
from city_core.transitions import digest, plain
from tests.effect_support import request
from tests.support import city


class EffectIntegrationTests(unittest.TestCase):
    def test_documented_first_effect_receipt_matches_real_core(self):
        root = Path(__file__).resolve().parents[2]
        contract = (root / "fixtures/effect_receipt_example.md").read_text(encoding="utf-8")
        section = contract.split("## 6.", 1)[1].split("## 7.", 1)[0]
        blocks = re.findall(r"```json\n(.*?)```", section, re.S)
        data, expected = [json.loads(block) for block in blocks[:2]]
        core = create_core()
        self.assertTrue(core.lifecycle.initialize(data["run_id"]).ok)
        applied = core.actuators.apply_effect(data)
        self.assertEqual(plain(applied.value), expected)
        retry = core.actuators.apply_effect(data)
        self.assertEqual(plain(retry.value), {**expected, "duplicate": True})

    def test_mixed_trace_replay_and_safe_c_consumer(self):
        core = city()
        first = core.actuators.apply_effect(request()).value
        self.assertTrue(core.lifecycle.advance_one("r-test", 1).ok)
        driver = request("water_driver", revision=2, effect_id="rain-1", source="B_SCENARIO",
                         rain_mm_h=10, river_level_m=2, outfall_blocked=True)
        self.assertTrue(core.scenarios.apply_effect(driver).ok)
        self.assertEqual(core.actuators.apply_effect(request()).value, replace(first, duplicate=True))
        page = core.lifecycle.read_private_transitions("r-test", -1, 100).value
        self.assertEqual([r.cause for r in page.records], ["EFFECT", "STEP", "EFFECT"])
        self.assertEqual([r.sequence for r in page.records], [0, None, 1])
        self.assertEqual([r.record_index for r in page.records], [0, 1, 2])
        replay = create_core()
        self.assertTrue(replay.lifecycle.initialize("replay", initial_config=plain(page.header.initial_config)).ok)
        for record in page.records:
            before = replay.observations.read_private("replay").value
            self.assertEqual(digest(before.physical_state), record.before_digest)
            data = plain(record.input)
            data["run_id"] = "replay"
            if record.cause == "STEP": result = replay.lifecycle.advance_one(**data)
            else:
                handle = replay.actuators if data["source"] == "B_ACTUATOR" else replay.scenarios
                result = handle.apply_effect(data)
            self.assertTrue(result.ok, result)
            self.assertEqual(digest(replay.observations.read_private("replay").value.physical_state), record.after_digest)
        self.assertEqual(plain(core.observations.read_private("r-test").value.physical_state),
                         plain(replay.observations.read_private("replay").value.physical_state))
        driver["effect"]["rain_mm_h"] = 100
        self.assertEqual(page.records[2].input.effect["rain_mm_h"], 10)
        with self.assertRaises(TypeError): page.records[2].input.effect["rain_mm_h"] = 99
        token = core.metadata.make_evidence_token("r-test", "command-17").value
        self.assertEqual(set(plain(token)), {"context", "correlation_id", "token_id"})
        self.assertEqual(core.metadata.read_metadata().value.context.revision, 3)
        for name in ("actuators", "scenarios", "apply_effect", "lifecycle", "read_private"):
            self.assertFalse(hasattr(core.metadata, name))
        self.assertEqual(set(core.metadata._metadata.__slots__), {"lock", "current", "token_count"})

    def test_reset_clears_reservation_ledger_sequence_and_old_delivery(self):
        core = city()
        data = request("reserve_responder", target_id="AMB-01", mission_ref="m-1",
                       destination_node_id="J-H1", route_road_ids=[])
        self.assertTrue(core.actuators.apply_effect(data).ok)
        self.assertFalse(core.lifecycle.reset("r-test", 0, "new-run").ok)
        self.assertTrue(core.actuators.apply_effect(data).value.duplicate)
        held = core.observations.read_private("r-test").value
        self.assertTrue(core.lifecycle.reset("r-test", 1, "new-run").ok)
        self.assertEqual(core.actuators.apply_effect(data).error.code, "OLD_RUN")
        self.assertEqual(core.observations.read_private("new-run").value.physical_state.responders["AMB-01"].status, "available")
        self.assertEqual(core.lifecycle.read_private_transitions("new-run", -1, 10).value.records, ())
        data["run_id"] = "new-run"
        applied = core.actuators.apply_effect(data)
        self.assertTrue(applied.ok)
        self.assertEqual((applied.value.sequence, applied.value.duplicate), (0, False))
        self.assertEqual(held.physical_state.responders["AMB-01"].status, "arrived")

    def test_versioned_effect_fixture_and_no_false_completion(self):
        fixture = json.loads((Path(__file__).resolve().parents[2] / "fixtures/effect_contract.json").read_text())
        core = city()
        for example in fixture["examples"]:
            data = example["request"]
            handle = core.actuators if data["source"] == "B_ACTUATOR" else core.scenarios
            result = handle.apply_effect(data)
            self.assertTrue(result.ok, result)
            self.assertEqual(result.value.outcome, "APPLIED")
        before = core.observations.read_private("r-test").value
        report = {"command_id": "cmd-99", "reported_setting": 1, "status": "completed"}
        self.assertFalse(core.actuators.apply_effect(report).ok)
        self.assertEqual(core.observations.read_private("r-test").value, before)
        self.assertTrue(core.lifecycle.advance_one("r-test", before.context.revision).ok)
        after = core.observations.read_private("r-test").value.physical_state
        self.assertAlmostEqual(after.water.basin_volume_m3, 5799.95)
        self.assertAlmostEqual(after.water.cumulative_in_m3, 0.15)
        self.assertAlmostEqual(after.water.cumulative_out_m3, 0.2)
        for road_id, road in after.roads.items():
            self.assertAlmostEqual(road.queue_vehicles, 10.5 if road_id == 'R-LOW' else 9.7)
            self.assertEqual(road.arrival_rate_veh_s, .5 if road_id == 'R-LOW' else .2)
            self.assertEqual(road.obstruction_fraction, 1 if road_id == 'R-LOW' else 0)
        for zone, record in after.air.items():
            self.assertAlmostEqual(record.pm25_ug_m3,
                10 + {'Z-INDUSTRIAL': .2, 'Z-RESIDENTIAL': .05}.get(zone, 0))
            self.assertEqual(record.external_source_ug_m3_s, 0)
        self.assertEqual(after, replace(before.physical_state, water=after.water, roads=after.roads, air=after.air))
