from concurrent.futures import ThreadPoolExecutor
import json
import math
from pathlib import Path
import unittest

from city_core import create_core
from city_core.transitions import digest, plain
from tests.effect_support import request
from tests.unit.test_water import water_city


class WaterIntegrationTests(unittest.TestCase):
    def test_actual_effect_power_pump_water_and_mapped_flood(self):
        core = water_city(pump=True)
        initial = core.observations.read_private("r-test").value
        pumped = core.lifecycle.advance_one("r-test", 0).value
        self.assertAlmostEqual(pumped.physical_state.water.surface_volume_m3, 198.8)
        self.assertFalse(pumped.flood["R-LOW"].flooded)
        self.assertTrue(core.actuators.apply_effect(request("feeder_position", revision=1,
            target_id="F-ESS", closed=False)).ok)
        for revision in (2, 3): self.assertTrue(core.lifecycle.advance_one("r-test", revision).ok)
        flooded = core.observations.read_private("r-test").value
        self.assertFalse(flooded.power.pump_running)
        self.assertAlmostEqual(flooded.physical_state.water.surface_volume_m3, 200.0)
        self.assertTrue(flooded.flood["R-LOW"].flooded)
        self.assertFalse(flooded.flood["R-HIGH"].flooded)
        self.assertEqual(initial.physical_state.water.surface_volume_m3, 199.4)
        self.assertIsNone(initial.physical_state.water.last_step)

    def test_intent_false_reading_and_unchanged_valve_do_not_rewrite_truth(self):
        core = water_city()
        before = core.observations.read_private("r-test").value
        for payload in ({"command": "close", "reported_position": 0, "success": True},
                        request("sensor_value", target_id="S-WAT-01", reading=0),
                        request("water_driver", source="B_SCENARIO", rain_mm_h=math.nan,
                                river_level_m=1, outfall_blocked=False)):
            self.assertFalse(core.actuators.apply_effect(payload).ok)
        self.assertEqual(core.observations.read_private("r-test").value, before)
        unchanged = request(target_id="V-12", opening_fraction=1.0)
        self.assertTrue(core.actuators.apply_effect(unchanged).ok)
        self.assertTrue(core.actuators.apply_effect(unchanged).value.duplicate)
        first = core.lifecycle.advance_one("r-test", 1).value
        self.assertAlmostEqual(first.physical_state.water.last_step.inlet_in_m3, 0.6)
        self.assertAlmostEqual(first.physical_state.water.surface_volume_m3, 200.0)
        close = request(revision=2, effect_id="actually-closed", target_id="V-12", opening_fraction=0.0)
        self.assertTrue(core.actuators.apply_effect(close).ok)
        self.assertEqual(core.observations.read_private("r-test").value.water.inlet_flow_m3_s, 0)
        next_step = core.lifecycle.advance_one("r-test", 3).value
        self.assertAlmostEqual(next_step.physical_state.water.last_step.rain_in_m3, 0.2)
        self.assertEqual(next_step.physical_state.water.last_step.inlet_in_m3, 0)

    def test_replay_reset_and_safe_public_metadata(self):
        core = water_city(pump=True)
        for revision in range(8): self.assertTrue(core.lifecycle.advance_one("r-test", revision).ok)
        held = core.observations.read_private("r-test").value
        page = core.lifecycle.read_private_transitions("r-test", -1, 100).value
        replay = create_core()
        self.assertTrue(replay.lifecycle.initialize("replay", initial_config=plain(page.header.initial_config)).ok)
        for record in page.records:
            self.assertEqual(digest(replay.observations.read_private("replay").value.physical_state), record.before_digest)
            self.assertTrue(replay.lifecycle.advance_one("replay", record.input.expected_revision).ok)
            self.assertEqual(digest(replay.observations.read_private("replay").value.physical_state), record.after_digest)
        self.assertEqual(replay.observations.read_private("replay").value.physical_state, held.physical_state)
        public = json.dumps(plain(core.metadata.read_metadata().value))
        for field in ("water", "flood", "cumulative_in", "last_step", "basin_volume", "pump_out"):
            self.assertNotIn(field, public)
        self.assertTrue(core.lifecycle.reset("r-test", 8, "new").ok)
        reset = core.observations.read_private("new").value
        self.assertIsNone(reset.physical_state.water.last_step)
        self.assertEqual(reset.physical_state.water.cumulative_in_m3, 0)
        self.assertEqual(reset.physical_state.water.cumulative_out_m3, 0)
        self.assertEqual(core.lifecycle.advance_one("r-test", 8).error.code, "OLD_RUN")
        self.assertIsNotNone(held.physical_state.water.last_step)

    def test_same_revision_race_only_evolves_water_once(self):
        core = water_city()
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: core.lifecycle.advance_one("r-test", 0), range(8)))
        self.assertEqual(sum(result.ok for result in results), 1)
        state = core.observations.read_private("r-test").value
        self.assertAlmostEqual(state.physical_state.water.cumulative_in_m3, 0.8)
        self.assertEqual(state.context.revision, 1)

    def test_versioned_water_checkpoint_fixture(self):
        fixture = json.loads((Path(__file__).resolve().parents[2] / "fixtures/water_contract.json").read_text())
        for case in fixture["cases"]:
            with self.subTest(name=case["name"]):
                core = water_city(**case["initial"])
                after = core.lifecycle.advance_one("r-test", 0).value
                water = after.physical_state.water
                self.assertAlmostEqual(water.surface_volume_m3, case["expected"]["surface_m3"])
                self.assertAlmostEqual(water.last_step.pump_out_m3, case["expected"]["pump_out_m3"])
                self.assertEqual(after.flood["R-LOW"].flooded, case["expected"]["low_flooded"])
