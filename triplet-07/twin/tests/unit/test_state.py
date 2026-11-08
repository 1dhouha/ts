from dataclasses import FrozenInstanceError
import math
import unittest
from unittest.mock import patch

from city_core import baseline_config, build_city
from tests.support import city


class StateTests(unittest.TestCase):
    def test_missing_or_unreadable_packaged_data_has_safe_readiness_failure(self):
        with patch("city_core.lifecycle.baseline_config", side_effect=OSError("private path")):
            result = build_city("r-test")
        self.assertEqual(result.error.code, "NOT_READY")
        self.assertNotIn("private path", result.error.safe_message)
        self.assertFalse(hasattr(result, "value"))

    def test_initial_actual_values_and_derived_geometry(self):
        state = city().observations.read_private("r-test").value.physical_state
        self.assertEqual(state.water.basin_volume_m3, 5800.0)
        self.assertEqual(state.water.basin_level_m, 2.9)
        self.assertEqual(state.water.surface_depth_m, 0.0)
        self.assertEqual(state.valve.effective_opening_fraction, 1 / 3)
        self.assertFalse(state.pump.effective_enabled)
        self.assertTrue(state.source.source_available)
        self.assertEqual(len(state.roads), 5)
        self.assertEqual(len(state.responders), 3)
        self.assertEqual(len(state.air), 5)
        self.assertTrue(all(a.pm25_ug_m3 == 10 for a in state.air.values()))
        self.assertTrue(all(r.status == "available" for r in state.responders.values()))
        self.assertEqual(state.responders["AMB-01"].route_road_ids, ())

    def test_numeric_types_finiteness_bounds_and_unknown_fields(self):
        cases = [
            ("water", "basin_volume_m3", x) for x in
            (-1, 6000.01, True, "5800", None, math.nan, math.inf, -math.inf, 10**1000)
        ] + [("water", "rain_mm_h", -1), ("water", "rain_mm_h", 201),
             ("water", "river_level_m", 11), ("water", "surface_volume_m3", -1),
             ("water", "outfall_blocked", 1), ("pump", "effective_enabled", "false"),
             ("valve", "effective_opening_fraction", 1.01),
             ("source", "source_available", 0)]
        for record, field, value in cases:
            with self.subTest(record=record, field=field, value=type(value).__name__):
                config = baseline_config()
                config["initial_physical"][record][field] = value
                self.assertEqual(build_city("r-test", initial_config=config).error.code,
                                 "INVALID_ARGUMENT")
        for field in ("basin_level_m", "sensor_value", "unit"):
            config = baseline_config()
            config["initial_physical"]["water"][field] = "cm"
            self.assertFalse(build_city("r-test", initial_config=config).ok)

    def test_all_domain_map_shapes_and_bounds(self):
        cases = [("roads", "R-LOW", "queue_vehicles", -1),
                 ("roads", "R-LOW", "arrival_rate_veh_s", 3),
                 ("roads", "R-LOW", "obstruction_fraction", 2),
                 ("feeders", "F-ESS", "effective_closed", 1),
                 ("signals", "J-D1", "effective_mode", "unsafe_green"),
                 ("air", "Z-HEALTH", "pm25_ug_m3", math.nan),
                 ("air", "Z-HEALTH", "external_source_ug_m3_s", 101),
                 ("responders", "AMB-01", "status", "arrived"),
                 ("responders", "AMB-01", "at_node_id", "J-D1"),
                 ("responders", "AMB-01", "mission_ref", "fake"),
                 ("responders", "AMB-01", "edge_index", True),
                 ("responders", "AMB-01", "route_road_ids", ["R-LOW"])]
        for domain, target, field, value in cases:
            with self.subTest(domain=domain, field=field):
                config = baseline_config()
                config["initial_physical"][domain][target][field] = value
                self.assertFalse(build_city("r-test", initial_config=config).ok)
        for domain in ("roads", "feeders", "signals", "responders", "air"):
            config = baseline_config()
            config["initial_physical"][domain].pop(next(iter(config["initial_physical"][domain])))
            self.assertFalse(build_city("r-test", initial_config=config).ok)

    def test_fixed_profile_versions_and_complete_input(self):
        for version in ("model_version", "catalog_version", "contract_version"):
            config = baseline_config()
            config[version] = "unknown"
            self.assertEqual(build_city("r-test", initial_config=config).error.code,
                             "UNSUPPORTED_VERSION")
            config[version] = True
            self.assertEqual(build_city("r-test", initial_config=config).error.code,
                             "INVALID_ARGUMENT")
        for key, value in (("basin_area_m2", 4000), ("step_ms", True),
                           ("step_ms", 1.0), ("basin_area_m2", math.nan)):
            config = baseline_config()
            config["parameters"][key] = value
            self.assertFalse(build_city("r-test", initial_config=config).ok)
        for field in ("parameters", "initial_physical"):
            config = baseline_config()
            del config[field]
            self.assertFalse(build_city("r-test", initial_config=config).ok)
        for value in (None, [], True):
            self.assertFalse(build_city("r-test", initial_config=value).ok)

    def test_valid_extreme_initial_values_and_input_detachment(self):
        config = baseline_config()
        config["initial_physical"]["water"].update(basin_volume_m3=6000,
                                                       surface_volume_m3=200,
                                                       rain_mm_h=200)
        core = city(initial_config=config)
        before = core.observations.read_private("r-test").value
        config["initial_physical"]["water"]["basin_volume_m3"] = 0
        self.assertEqual(before.physical_state.water.basin_level_m, 3.0)
        self.assertEqual(before.physical_state.water.surface_depth_m, 0.2)
        self.assertEqual(before, core.observations.read_private("r-test").value)
        with self.assertRaises(FrozenInstanceError):
            before.physical_state.water.basin_volume_m3 = 0
        with self.assertRaises((FrozenInstanceError, AttributeError, TypeError)):
            before.physical_state.water.basin_level_m = 0


if __name__ == "__main__":
    unittest.main()
