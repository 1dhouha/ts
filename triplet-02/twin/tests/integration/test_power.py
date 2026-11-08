from dataclasses import asdict, replace
import json
from pathlib import Path
import unittest

from city_core import baseline_config, create_core
from city_core.transitions import digest, plain
from tests.effect_support import request
from tests.support import city


class PowerIntegrationTests(unittest.TestCase):
    def test_effect_private_read_branch_isolation_and_reset(self):
        core = city()
        baseline = core.observations.read_private("r-test").value
        self.assertTrue(core.actuators.apply_effect(request("pump_enable", target_id="P-03", enabled=True)).ok)
        enabled = core.observations.read_private("r-test").value
        self.assertTrue(enabled.power.pump_running)
        self.assertEqual(enabled.power.feeders["F-ESS"].delivered_load_kw, 60.0)
        self.assertFalse(baseline.power.pump_running)
        self.assertTrue(core.actuators.apply_effect(request("feeder_position", revision=1, effect_id="industrial-off",
            target_id="F-IND", closed=False)).ok)
        isolated = core.observations.read_private("r-test").value
        self.assertFalse(isolated.power.plant_active)
        self.assertTrue(isolated.power.hospital_powered)
        self.assertTrue(isolated.power.pump_running)
        self.assertEqual(isolated.power.feeders["F-ESS"], enabled.power.feeders["F-ESS"])
        self.assertEqual(isolated.power.feeders["F-URBAN"], enabled.power.feeders["F-URBAN"])
        self.assertTrue(core.actuators.apply_effect(request("feeder_position", revision=2, effect_id="ess-off",
            target_id="F-ESS", closed=False)).ok)
        no_ess = core.observations.read_private("r-test").value
        self.assertTrue(no_ess.physical_state.pump.effective_enabled)
        self.assertFalse(no_ess.power.pump_running)
        self.assertFalse(no_ess.power.hospital_powered)
        self.assertEqual(no_ess.power.feeders["F-ESS"].requested_demand_kw, 60.0)
        self.assertEqual(no_ess.power.feeders["F-ESS"].unmet_demand_kw, 60.0)
        self.assertTrue(core.lifecycle.reset("r-test", 3, "new").ok)
        reset = core.observations.read_private("new").value
        self.assertEqual(reset.power, baseline.power)
        self.assertEqual(reset.context.revision, 0)
        self.assertFalse(isolated.power.plant_active)

    def test_unavailable_source_duplicate_step_and_private_safe_metadata(self):
        core = city()
        data = request("source_driver", source="B_SCENARIO", available=False)
        receipt = core.scenarios.apply_effect(data).value
        before = core.observations.read_private("r-test").value
        for feeder in before.physical_state.feeders.values(): self.assertTrue(feeder.effective_closed)
        self.assertFalse(any(feeder.energized for feeder in before.power.feeders.values()))
        self.assertAlmostEqual(before.power.total_unmet_demand_kw, 230.2)
        for _ in range(8):
            self.assertEqual(core.observations.read_private("r-test").value.power, before.power)
            self.assertEqual(core.metadata.read_metadata().value.context, before.context)
        self.assertEqual(core.scenarios.apply_effect(data).value.sequence, receipt.sequence)
        stepped = core.lifecycle.advance_one("r-test", 1).value
        self.assertEqual(stepped.power, before.power)
        for road_id, road in stepped.physical_state.roads.items():
            self.assertAlmostEqual(road.queue_vehicles, 9.7 if road_id == 'R-HIGH' else 10.2)
        self.assertEqual(stepped.physical_state, replace(before.physical_state,
            water=stepped.physical_state.water, roads=stepped.physical_state.roads))
        self.assertEqual(stepped.physical_state.water.basin_volume_m3, 5800.0)
        self.assertAlmostEqual(stepped.physical_state.water.cumulative_in_m3, 0.2)
        self.assertAlmostEqual(stepped.physical_state.water.cumulative_out_m3, 0.2)
        public = json.dumps({"metadata": asdict(core.metadata.read_metadata().value),
                             "token": asdict(core.metadata.make_evidence_token("r-test").value)})
        for private in ("energized", "requested_demand", "delivered_load", "pump_running", "power", "unmet"):
            self.assertNotIn(private, public)
        self.assertFalse(hasattr(core.metadata, "power"))
        page = core.lifecycle.read_private_transitions("r-test", -1, 10).value
        self.assertEqual([record.cause for record in page.records], ["EFFECT", "STEP"])
        self.assertNotEqual(page.records[1].before_digest, page.records[1].after_digest)
        self.assertEqual(page.records[1].before_digest, digest(before.physical_state))
        self.assertEqual(page.records[1].after_digest, digest(stepped.physical_state))
        self.assertNotIn("power", plain(before))
        self.assertEqual(set(plain(before)), {"context", "config_digest", "physical_state"})

    def test_signal_route_precondition_uses_same_current_supply(self):
        config = baseline_config()
        config["initial_physical"]["feeders"]["F-URBAN"]["effective_closed"] = False
        core = city(initial_config=config)
        self.assertFalse(core.observations.read_private("r-test").value.power.assets["J-D1"].powered)
        reserve = request("reserve_responder", target_id="AMB-01", mission_ref="m", destination_node_id="J-D1", route_road_ids=["R-LOW"])
        self.assertEqual(core.actuators.apply_effect(reserve).error.code, "ROUTE_UNAVAILABLE")
        self.assertTrue(core.actuators.apply_effect(request("feeder_position", effect_id="urban-reconnect",
            target_id="F-URBAN", closed=True)).ok)
        self.assertTrue(core.observations.read_private("r-test").value.power.assets["J-D1"].powered)
        reserve["expected_revision"] = 1
        self.assertTrue(core.actuators.apply_effect(reserve).ok)

    def test_versioned_private_checkpoint_fixture(self):
        fixture = json.loads((Path(__file__).resolve().parents[2] / "fixtures/power_contract.json").read_text())
        for example in fixture["cases"]:
            core = create_core()
            config = baseline_config()
            config["initial_physical"]["pump"]["effective_enabled"] = example["pump_enabled"]
            self.assertTrue(core.lifecycle.initialize("r-test", initial_config=config).ok)
            if example["effect"] is not None:
                data = example["effect"]
                handle = core.actuators if data["source"] == "B_ACTUATOR" else core.scenarios
                self.assertTrue(handle.apply_effect(data).ok)
            power = core.observations.read_private("r-test").value.power
            expected = example["expected"]
            self.assertEqual(power.pump_running, expected["pump_running"])
            self.assertEqual(power.hospital_powered, expected["hospital_powered"])
            self.assertEqual(power.plant_active, expected["plant_active"])
            self.assertEqual(power.feeders["F-ESS"].delivered_load_kw, expected["ess_delivered_kw"])
            self.assertAlmostEqual(power.total_delivered_load_kw, expected["total_delivered_kw"])
