from dataclasses import FrozenInstanceError
from itertools import product
import math
import unittest

from city_core import baseline_catalog, baseline_config, build_city
from city_core.validation import InvalidInput
from tests.support import city


class PowerTests(unittest.TestCase):
    def test_baseline_hand_calculated_loads_and_connected_assets(self):
        snapshot = city().observations.read_private("r-test").value
        power = snapshot.power
        expected = {"F-ESS": (50.0, 100.0), "F-URBAN": (80.2, 120.0), "F-IND": (100.0, 150.0)}
        for target, (load, rating) in expected.items():
            feeder = power.feeders[target]
            self.assertTrue(feeder.energized)
            self.assertAlmostEqual(feeder.requested_demand_kw, load)
            self.assertAlmostEqual(feeder.delivered_load_kw, load)
            self.assertEqual(feeder.unmet_demand_kw, 0.0)
            self.assertEqual(feeder.rated_capacity_kw, rating)
            self.assertFalse(feeder.overload)
        self.assertEqual(set(power.assets), {"HOSP-01", "P-03", "LOAD-URBAN", "J-D1", "J-D2", "PLANT-01"})
        self.assertTrue(power.assets["P-03"].powered)
        self.assertFalse(power.pump_running)
        self.assertEqual(power.pump_delivered_demand_kw, 0.0)
        self.assertTrue(power.hospital_powered)
        self.assertTrue(power.plant_active)
        self.assertAlmostEqual(power.total_requested_demand_kw, 230.2)
        self.assertAlmostEqual(power.total_delivered_load_kw, 230.2)
        self.assertEqual(power.total_unmet_demand_kw, 0.0)

    def test_all_supply_switch_and_enable_combinations(self):
        for source, enabled, ess, urban, industrial in product((False, True), repeat=5):
            with self.subTest(source=source, enabled=enabled, ess=ess, urban=urban, industrial=industrial):
                config = baseline_config()
                actual = config["initial_physical"]
                actual["source"]["source_available"] = source
                actual["pump"]["effective_enabled"] = enabled
                for name, closed in (("F-ESS", ess), ("F-URBAN", urban), ("F-IND", industrial)):
                    actual["feeders"][name]["effective_closed"] = closed
                power = city(initial_config=config).observations.read_private("r-test").value.power
                expected = {"F-ESS": (source and ess, 60.0 if enabled else 50.0),
                            "F-URBAN": (source and urban, 80.2), "F-IND": (source and industrial, 100.0)}
                for name, (energized, demand) in expected.items():
                    self.assertEqual(power.feeders[name].energized, energized)
                    self.assertAlmostEqual(power.feeders[name].requested_demand_kw, demand)
                    self.assertAlmostEqual(power.feeders[name].delivered_load_kw, demand if energized else 0.0)
                    self.assertAlmostEqual(power.feeders[name].unmet_demand_kw, 0.0 if energized else demand)
                    self.assertFalse(power.feeders[name].overload)
                self.assertEqual(power.pump_running, source and ess and enabled)
                self.assertEqual(power.pump_delivered_demand_kw, 10.0 if source and ess and enabled else 0.0)
                self.assertEqual(power.hospital_powered, source and ess)
                self.assertEqual(power.plant_active, source and industrial)
                self.assertEqual(power.assets["J-D1"].powered, source and urban)
                self.assertEqual(power.assets["J-D2"].powered, source and urban)
                self.assertAlmostEqual(sum(a.requested_demand_kw for a in power.assets.values()),
                                       power.total_requested_demand_kw)
                self.assertAlmostEqual(sum(a.delivered_load_kw for a in power.assets.values()),
                                       power.total_delivered_load_kw)
                self.assertAlmostEqual(power.total_requested_demand_kw,
                                       power.total_delivered_load_kw + power.total_unmet_demand_kw)

    def test_power_view_matches_fixed_profile_and_approved_topology(self):
        power = city().observations.read_private("r-test").value.power
        config = baseline_config()["parameters"]
        relations = {(r["source_id"], r["relation"], r["target_id"]) for r in baseline_catalog()["relationships"]}
        for name, feeder in power.feeders.items():
            self.assertEqual(feeder.rated_capacity_kw, config["feeder_rated_kw"][name])
            self.assertIn(("GRID-01", "supplies", name), relations)
        for name, asset in power.assets.items():
            self.assertIn((asset.feeder_id, "powers", name), relations)
        self.assertEqual(power.assets["HOSP-01"].requested_demand_kw, config["hospital_kw"])
        self.assertEqual(power.assets["LOAD-URBAN"].requested_demand_kw, config["urban_kw"])
        self.assertEqual(power.assets["PLANT-01"].requested_demand_kw, config["plant_kw"])
        self.assertEqual(power.assets["J-D1"].requested_demand_kw, config["signal_kw"])
        config = baseline_config()
        config["initial_physical"]["pump"]["effective_enabled"] = True
        self.assertEqual(city(initial_config=config).observations.read_private("r-test").value.power.assets["P-03"].requested_demand_kw, config["parameters"]["pump_kw"])

    def test_motor_demand_independent_of_water_outfall_and_signal_mode(self):
        for blocked, river in ((True, 1.0), (False, 2.0), (True, 10.0)):
            config = baseline_config()
            config["initial_physical"]["pump"]["effective_enabled"] = True
            config["initial_physical"]["water"].update(outfall_blocked=blocked, river_level_m=river)
            config["initial_physical"]["signals"]["J-D1"]["effective_mode"] = "stop"
            power = city(initial_config=config).observations.read_private("r-test").value.power
            self.assertTrue(power.pump_running)
            self.assertEqual(power.pump_delivered_demand_kw, 10.0)
            self.assertEqual(power.feeders["F-ESS"].delivered_load_kw, 60.0)
            self.assertAlmostEqual(power.feeders["F-URBAN"].delivered_load_kw, 80.2)
            self.assertFalse(hasattr(power, "pump_flow_m3_s"))

    def test_power_facts_cannot_be_set_or_injected(self):
        snapshot = city().observations.read_private("r-test").value
        facts = snapshot.power
        with self.assertRaises((FrozenInstanceError, TypeError, AttributeError)):
            snapshot.power = facts
        with self.assertRaises(TypeError): facts.feeders["F-ESS"] = facts.feeders["F-IND"]
        with self.assertRaises(TypeError): facts.assets["P-03"] = facts.assets["HOSP-01"]
        with self.assertRaises(FrozenInstanceError): facts.feeders["F-ESS"].energized = False
        with self.assertRaises(FrozenInstanceError): facts.assets["P-03"].powered = False
        with self.assertRaises((FrozenInstanceError, TypeError, AttributeError)): facts.pump_running = True
        for path, name, value in (((), "power", {}), (("feeders", "F-ESS"), "energized", True),
                                  (("pump",), "running", True)):
            config = baseline_config()
            target = config["initial_physical"]
            for key in path: target = target[key]
            target[name] = value
            self.assertEqual(build_city("invalid", initial_config=config).error.code, "INVALID_ARGUMENT")
        for key in ("hospital_kw", "pump_kw"):
            config = baseline_config()
            config["parameters"][key] = 0.0
            self.assertEqual(build_city("invalid", initial_config=config).error.code, "INVALID_ARGUMENT")

    def test_zero_demand_and_strict_overload_arithmetic_without_auto_trip(self):
        from city_core.power import account_feeder
        zero = account_feeder(True, True, 0.0, 100.0)
        self.assertTrue(zero.energized)
        self.assertEqual((zero.requested_demand_kw, zero.delivered_load_kw, zero.unmet_demand_kw), (0.0, 0.0, 0.0))
        self.assertFalse(zero.overload)
        for demand, expected in ((math.nextafter(100.0, 0.0), False), (100.0, False),
                                 (math.nextafter(100.0, math.inf), True), (150.0, True)):
            result = account_feeder(True, True, demand, 100.0)
            self.assertEqual(result.overload, expected)
            self.assertTrue(result.energized)
            self.assertEqual(result.delivered_load_kw, demand)
            self.assertEqual(result.unmet_demand_kw, 0.0)
        for source, closed in ((False, False), (False, True), (True, False)):
            result = account_feeder(source, closed, 150.0, 100.0)
            self.assertFalse(result.overload)
            self.assertFalse(result.energized)
            self.assertEqual(result.delivered_load_kw, 0.0)
            self.assertEqual(result.unmet_demand_kw, 150.0)

    def test_accounting_helper_rejects_invalid_numbers_flags_and_targets(self):
        from city_core.power import account_feeder, asset_powered
        for args in ((1, True, 10, 100), (True, 1, 10, 100), (True, True, True, 100),
                     (True, True, -1, 100), (True, True, math.inf, 100),
                     (True, True, math.nan, 100), (True, True, 10, 0),
                     (True, True, 10, -1), (True, True, 10, True),
                     (True, True, 10**400, 100)):
            with self.subTest(args=args), self.assertRaises(InvalidInput): account_feeder(*args)
        state = city().observations.read_private("r-test").value.physical_state
        for target in ("missing", "CITY-01", "F-ESS", "AMB-01"):
            with self.assertRaises(InvalidInput) as error: asset_powered(state, target)
            self.assertEqual(error.exception.code, "UNKNOWN_TARGET")
        for target in (None, True, [], "bad\n"):
            with self.assertRaises(InvalidInput): asset_powered(state, target)
