from dataclasses import FrozenInstanceError
import math
import unittest
from unittest.mock import patch

from city_core import baseline_catalog, baseline_config, build_city
from city_core.transitions import plain
from tests.support import city


def water_city(*, basin=6000.0, surface=199.4, rain=36.0, valve=1.0,
               pump=False, source=True, ess=True, blocked=False, river=1.0):
    config = baseline_config()
    state = config["initial_physical"]
    state["water"].update(basin_volume_m3=basin, surface_volume_m3=surface,
                          rain_mm_h=rain, outfall_blocked=blocked, river_level_m=river)
    state["valve"]["effective_opening_fraction"] = valve
    state["pump"]["effective_enabled"] = pump
    state["source"]["source_available"] = source
    state["feeders"]["F-ESS"]["effective_closed"] = ess
    return city(initial_config=config)


class WaterTests(unittest.TestCase):
    def test_independent_flood_pump_and_outfall_fixtures(self):
        for changes, surface, gravity, pumped in (
                ({}, 200.0, 0.2, 0.0), ({"pump": True}, 198.8, 0.2, 1.2),
                ({"pump": True, "ess": False}, 200.0, 0.2, 0.0),
                ({"pump": True, "source": False}, 200.0, 0.2, 0.0),
                ({"pump": True, "blocked": True}, 200.2, 0.0, 0.0),
                ({"pump": True, "river": 2.0}, 200.2, 0.0, 0.0),
                ({"valve": 0.0}, 199.4, 0.2, 0.0)):
            with self.subTest(changes=changes):
                core = water_city(**changes)
                before = core.observations.read_private("r-test").value
                self.assertIsNone(before.physical_state.water.last_step)
                after = core.lifecycle.advance_one("r-test", 0).value
                water = after.physical_state.water
                self.assertAlmostEqual(water.basin_volume_m3, 6000.0)
                self.assertAlmostEqual(water.surface_volume_m3, surface)
                self.assertAlmostEqual(water.last_step.rain_in_m3, 0.2)
                self.assertAlmostEqual(water.last_step.inlet_in_m3, 0.0 if changes.get("valve") == 0 else 0.6)
                self.assertAlmostEqual(water.last_step.gravity_out_m3, gravity)
                self.assertAlmostEqual(water.last_step.pump_out_m3, pumped)
                self.assertAlmostEqual(water.cumulative_in_m3, 0.2 if changes.get("valve") == 0 else 0.8)
                self.assertAlmostEqual(water.cumulative_out_m3, gravity + pumped)
                self.assertAlmostEqual(water.basin_volume_m3 + water.surface_volume_m3,
                                       6199.4 + water.cumulative_in_m3 - water.cumulative_out_m3)
                for road_id, road in after.physical_state.roads.items():
                    closed = road_id == 'R-LOW' and surface >= 200
                    unpowered = road_id != 'R-HIGH' and changes.get('source') is False
                    self.assertAlmostEqual(road.queue_vehicles, 10.2 if closed or unpowered else 9.7)
                    self.assertEqual(road.arrival_rate_veh_s, .2)
                    self.assertEqual(road.obstruction_fraction, 0)
                for zone, record in after.physical_state.air.items():
                    source = ({'Z-INDUSTRIAL': .2, 'Z-RESIDENTIAL': .05}.get(zone, 0)
                              if changes.get('source') is not False else 0)
                    self.assertAlmostEqual(record.pm25_ug_m3, 10 + source)
                    self.assertEqual(record.external_source_ug_m3_s, 0)
                for key in plain(before.physical_state):
                    if key not in ('water', 'roads', 'air'):
                        self.assertEqual(getattr(after.physical_state, key), getattr(before.physical_state, key))

    def test_dry_near_empty_and_gravity_priority(self):
        for basin, surface, gravity, pump in ((0.0, 0.0, 0.0, 0.0),
                (0.1, 0.0, 0.1, 0.0), (0.3, 0.0, 0.2, 0.1),
                (0.0, 0.3, 0.2, 0.1), (0.7, 0.7, 0.2, 1.2)):
            core = water_city(basin=basin, surface=surface, rain=0, valve=0, pump=True)
            water = core.lifecycle.advance_one("r-test", 0).value.physical_state.water
            self.assertAlmostEqual(water.last_step.gravity_out_m3, gravity)
            self.assertAlmostEqual(water.last_step.pump_out_m3, pump)
            self.assertEqual(water.basin_volume_m3, 0.0)
            self.assertEqual(water.surface_volume_m3, 0.0)
            self.assertLessEqual(water.last_step.outflow_m3, basin + surface)
            self.assertGreaterEqual(water.cumulative_out_m3, 0)

    def test_mm_hour_conversion_and_rain_persists_after_inlet_closure(self):
        core = water_city(basin=0, surface=0, rain=180, valve=0, blocked=True)
        snapshot = core.observations.read_private("r-test").value
        self.assertAlmostEqual(snapshot.water.rain_inflow_m3_s, 1.0)
        self.assertEqual(snapshot.water.inlet_flow_m3_s, 0.0)
        water = core.lifecycle.advance_one("r-test", 0).value.physical_state.water
        self.assertAlmostEqual(water.basin_volume_m3, 1.0)
        self.assertEqual(water.surface_volume_m3, 0.0)
        self.assertEqual(water.last_step.inlet_in_m3, 0.0)

    def test_overflow_retained_and_stronger_inflow_beats_running_pump(self):
        core = water_city(basin=5999.9, surface=0, rain=200, valve=1, pump=True)
        after = core.lifecycle.advance_one("r-test", 0).value
        self.assertTrue(after.power.pump_running)
        self.assertAlmostEqual(after.physical_state.water.basin_volume_m3, 6000.0)
        self.assertAlmostEqual(after.physical_state.water.surface_volume_m3, 0.2111111111111111)
        for revision in range(1, 12): self.assertTrue(core.lifecycle.advance_one("r-test", revision).ok)
        water = core.observations.read_private("r-test").value.physical_state.water
        self.assertGreater(water.surface_volume_m3, 3.0)
        self.assertAlmostEqual(water.basin_volume_m3 + water.surface_volume_m3,
                               5999.9 + water.cumulative_in_m3 - water.cumulative_out_m3)

    def test_exposure_mapping_exact_and_adjacent_thresholds(self):
        for road, cutoff in (("R-LOW", 200.0), ("R-HIGH", 1000.0)):
            for volume, flooded in ((math.nextafter(cutoff, 0), False),
                                    (cutoff, True), (math.nextafter(cutoff, math.inf), True)):
                with self.subTest(road=road, volume=volume):
                    core = water_city(surface=volume, rain=0, valve=0, blocked=True)
                    for snapshot in (core.observations.read_private("r-test").value,
                                     core.lifecycle.advance_one("r-test", 0).value):
                        self.assertEqual(snapshot.flood[road].flooded, flooded)
                        for other in ("R-LINK", "R-DOWN", "R-IND"):
                            self.assertEqual(snapshot.flood[other].depth_m, 0.0)
                            self.assertFalse(snapshot.flood[other].flooded)
        core = water_city(surface=1000.0)
        self.assertAlmostEqual(core.observations.read_private("r-test").value.flood["R-HIGH"].depth_m, 0.2)

    def test_fixed_water_profile_and_road_map_match_catalog(self):
        core = water_city(rain=36, valve=1, pump=True)
        view = core.observations.read_private("r-test").value.water
        self.assertAlmostEqual(view.rain_inflow_m3_s, 0.2)
        self.assertEqual(view.inlet_flow_m3_s, baseline_config()["parameters"]["inlet_max_m3_s"])
        self.assertEqual(view.gravity_capacity_m3_s, baseline_config()["parameters"]["gravity_max_m3_s"])
        self.assertEqual(view.pump_capacity_m3_s, baseline_config()["parameters"]["pump_max_m3_s"])
        self.assertTrue(view.outfall_available)
        exposed = {r["target_id"] for r in baseline_catalog()["relationships"]
                   if r["source_id"] == "FLOOD-01" and r["relation"] == "exposes"}
        flood = water_city(surface=2000).observations.read_private("r-test").value.flood
        self.assertEqual({name for name, record in flood.items() if record.flooded}, exposed)

    def test_runtime_accounting_cannot_be_injected_or_mutated(self):
        for key, value in (("last_step", None), ("cumulative_in_m3", 100), ("cumulative_out_m3", 2)):
            config = baseline_config()
            config["initial_physical"]["water"][key] = value
            self.assertEqual(build_city("bad", initial_config=config).error.code, "INVALID_ARGUMENT")
        core = water_city()
        snapshot = core.lifecycle.advance_one("r-test", 0).value
        with self.assertRaises(FrozenInstanceError): snapshot.physical_state.water.last_step.pump_out_m3 = 1
        with self.assertRaises(FrozenInstanceError): snapshot.physical_state.water.cumulative_in_m3 = 100
        with self.assertRaises(TypeError): snapshot.flood["R-LOW"] = snapshot.flood["R-HIGH"]
        with self.assertRaises(FrozenInstanceError): snapshot.flood["R-LOW"].flooded = False
        with self.assertRaises(FrozenInstanceError): snapshot.water.inlet_flow_m3_s = 10

    def test_internal_numeric_failure_rolls_back_whole_step(self):
        core = water_city()
        before = core.observations.read_private("r-test").value
        with patch("city_core.water.math.fsum", side_effect=OverflowError("private numeric detail")):
            result = core.lifecycle.advance_one("r-test", 0)
        self.assertEqual(result.error.code, "MODEL_LIMIT")
        self.assertNotIn("private", result.error.safe_message)
        self.assertEqual(core.observations.read_private("r-test").value, before)
        self.assertEqual(core.lifecycle.read_private_transitions("r-test", -1, 10).value.records, ())
        self.assertTrue(core.lifecycle.advance_one("r-test", 0).ok)
