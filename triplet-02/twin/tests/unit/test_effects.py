from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
import math
import unittest
from unittest.mock import patch

from city_core import baseline_config, create_core
from city_core.transitions import plain
from tests.effect_support import request
from tests.support import city


class EffectTests(unittest.TestCase):
    def setUp(self):
        self.core = city()

    def snapshot(self):
        return self.core.observations.read_private("r-test").value

    def reject(self, data, code="INVALID_ARGUMENT", handle=None):
        before = self.snapshot()
        trace = self.core.lifecycle.read_private_transitions("r-test", -1, 100).value
        result = (handle or self.core.actuators).apply_effect(data)
        self.assertFalse(result.ok)
        self.assertEqual(result.error.code, code)
        self.assertEqual(result.error.retryable, code == "STATE_CONFLICT")
        self.assertEqual(result.error.safe_message, "Effect request rejected")
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.core.lifecycle.read_private_transitions("r-test", -1, 100).value, trace)
        return result

    def test_each_allowed_setting_changes_exact_fields(self):
        cases = [
            ("valve_position", {"target_id": "V-12", "opening_fraction": 0.75},
             "valve", {"effective_opening_fraction": 0.75}),
            ("pump_enable", {"target_id": "P-03", "enabled": True},
             "pump", {"effective_enabled": True, "effective_speed_fraction": 1.0}),
            ("pump_speed", {"target_id": "P-03", "speed_fraction": 0.3},
             "pump", {"effective_enabled": True, "effective_speed_fraction": 0.3}),
            ("feeder_position", {"target_id": "F-IND", "closed": False},
             "feeders", {"F-IND": {"effective_closed": False}}),
            ("signal_mode", {"target_id": "J-D1", "mode": "priority"},
             "signals", {"J-D1": {"effective_mode": "priority"}}),
            ("water_driver", {"rain_mm_h": 200, "river_level_m": 10, "outfall_blocked": True},
             "water", {"rain_mm_h": 200.0, "river_level_m": 10.0, "outfall_blocked": True}),
            ("source_driver", {"available": False}, "source", {"source_available": False}),
            ("road_driver", {"target_id": "R-LOW", "arrival_rate_veh_s": 2, "obstruction_fraction": 1},
             "roads", {"R-LOW": {"arrival_rate_veh_s": 2.0, "obstruction_fraction": 1.0}}),
            ("air_driver", {"target_id": "Z-HEALTH", "source_ug_m3_s": 100},
             "air", {"Z-HEALTH": {"external_source_ug_m3_s": 100.0}})]
        for tag, fields, group, changes in cases:
            for source in ("B_ACTUATOR", "B_SCENARIO"):
                if tag.endswith("driver") and source == "B_ACTUATOR":
                    continue
                with self.subTest(tag=tag, source=source):
                    core = city()
                    before = core.observations.read_private("r-test").value
                    expected = plain(before.physical_state)
                    for key, value in changes.items():
                        if isinstance(value, dict):
                            expected[group][key].update(value)
                        else:
                            expected[group][key] = value
                    handle = core.actuators if source == "B_ACTUATOR" else core.scenarios
                    receipt = handle.apply_effect(request(tag, source=source, **fields))
                    self.assertTrue(receipt.ok, receipt)
                    after = core.observations.read_private("r-test").value
                    self.assertEqual(plain(after.physical_state), expected)
                    self.assertEqual(after.context, replace(before.context, revision=1))
                    self.assertEqual(receipt.value.context, after.context)
                    self.assertEqual((receipt.value.sequence, receipt.value.outcome,
                                      receipt.value.duplicate), (0, "APPLIED", False))
                    self.assertNotEqual(plain(before.physical_state), expected)

    def test_envelope_and_nested_malformed_inputs_atomic(self):
        base = request()
        bad = [None, [], "request", 1, True, {}, {**base, "extra": 1}]
        bad += [{k: v for k, v in base.items() if k != missing} for missing in base]
        for key in ("run_id", "effect_id", "source", "contract_version"):
            bad += [{**base, key: v} for v in (None, {}, [], 1, True, "", "a\n", "é", "a" * 129)]
        bad += [{**base, "expected_revision": v} for v in (-1, True, 0.0, "0", None, {}, [])]
        bad += [{**base, "correlation_id": v} for v in (True, 7, {}, [], "", "x" * 129, "x\n", "é", "\x7f")]
        bad += [{**base, "effect": v} for v in (None, [], {}, "x", {"tag": []},
            {"tag": "unknown"}, {**base["effect"], "unit": "percent"},
            {"tag": "valve_position", "target_id": [], "opening_fraction": 0})]
        for data in bad:
            with self.subTest(data=data):
                self.reject(data)
        self.reject({**base, "contract_version": "pa-internal-99"}, "UNSUPPORTED_VERSION")

    def test_value_types_bounds_and_false_reports(self):
        for value in (-1, 1.01, True, "0", None, {}, [], float("nan"), float("inf"), 10**400):
            self.reject(request(opening_fraction=value, target_id="V-12"))
        for tag, fields, key in [
            ("pump_enable", {"target_id": "P-03", "enabled": True}, "enabled"),
            ("feeder_position", {"target_id": "F-ESS", "closed": True}, "closed"),
            ("source_driver", {"available": True}, "available")]:
            for value in (0, 1, "true", None, [], {}):
                self.reject(request(tag, source="B_SCENARIO", **{**fields, key: value}),
                            handle=self.core.scenarios)
        for speed in (-0.1, 1.1, True, "0.5", None, float("nan"), float("inf")):
            self.reject(request("pump_speed", target_id="P-03", speed_fraction=speed))
        for mode in ([], {}, True, 1, None, "unpowered", "unsafe"):
            self.reject(request("signal_mode", target_id="J-D1", mode=mode))
        for tag in ("requested_setting", "reported_setting", "command_complete", "quarantine", "set_field"):
            self.reject(request(tag))
        for tag, fields in [
            ("water_driver", {"rain_mm_h": 201, "river_level_m": 1, "outfall_blocked": False}),
            ("water_driver", {"rain_mm_h": 2, "river_level_m": 11, "outfall_blocked": False}),
            ("water_driver", {"rain_mm_h": 2, "river_level_m": 1, "outfall_blocked": 0}),
            ("road_driver", {"target_id": "R-LOW", "arrival_rate_veh_s": 2.1, "obstruction_fraction": 0}),
            ("road_driver", {"target_id": "R-LOW", "arrival_rate_veh_s": 1, "obstruction_fraction": 1.1}),
            ("air_driver", {"target_id": "Z-HEALTH", "source_ug_m3_s": 101})]:
            self.reject(request(tag, source="B_SCENARIO", **fields), handle=self.core.scenarios)
        self.assertTrue(self.core.actuators.apply_effect(request()).ok)

    def test_binding_capability_run_revision_and_timing(self):
        for target in ("missing", "P-03", "DEV-VALVE-12"):
            self.reject(request(target_id=target, opening_fraction=0), "UNKNOWN_TARGET")
        self.reject(request(run_id="r-old"), "OLD_RUN")
        self.reject(request(revision=1), "STATE_CONFLICT")
        self.reject({**request(), "sim_time_ms": 1000})
        self.reject(request(source="B_SCENARIO"), "FORBIDDEN_CAPABILITY")
        self.reject(request("source_driver", available=False), "FORBIDDEN_CAPABILITY")
        result = create_core().actuators.apply_effect(request())
        self.assertEqual(result.error.code, "NOT_READY")
        self.assertIsNone(result.error.context)

    def test_duplicate_original_receipt_after_step_and_content_conflict(self):
        data = request()
        first = self.core.actuators.apply_effect(data).value
        self.assertTrue(self.core.lifecycle.advance_one("r-test", 1).ok)
        before = self.snapshot()
        duplicate = self.core.actuators.apply_effect(deepcopy(data)).value
        self.assertEqual(duplicate, replace(first, duplicate=True))
        self.assertEqual(self.snapshot(), before)
        normalized = deepcopy(data)
        normalized["effect"]["opening_fraction"] = -0.0
        self.assertEqual(self.core.actuators.apply_effect(normalized).value, duplicate)
        for key, value in (("correlation_id", "changed"), ("expected_revision", 2)):
            self.reject({**data, key: value}, "IDENTITY_CONFLICT")
        changed = deepcopy(data)
        changed["effect"]["opening_fraction"] = 1
        self.reject(changed, "IDENTITY_CONFLICT")
        self.reject(data, "FORBIDDEN_CAPABILITY", self.core.scenarios)
        self.reject(request(revision=0, effect_id="not-consumed"), "STATE_CONFLICT")
        self.assertTrue(self.core.actuators.apply_effect(request(revision=2, effect_id="not-consumed")).ok)

    def test_reserve_release_resource_and_immutable_input(self):
        data = request("reserve_responder", target_id="AMB-01", mission_ref="m-1",
                       destination_node_id="J-H1", route_road_ids=[])
        first = self.core.actuators.apply_effect(data)
        self.assertTrue(first.ok)
        occupied = self.snapshot()
        self.assertEqual(occupied.physical_state.responders["AMB-01"].status, "arrived")
        self.assertEqual(self.core.actuators.apply_effect(data).value.sequence, 0)
        data["effect"]["route_road_ids"].append("R-LOW")
        self.assertEqual(occupied.physical_state.responders["AMB-01"].route_road_ids, ())
        self.reject(request("reserve_responder", revision=1, effect_id="eff-2", target_id="AMB-01",
            mission_ref="m-2", destination_node_id="J-H1", route_road_ids=[]), "RESOURCE_UNAVAILABLE")
        self.reject(request("release_responder", revision=1, effect_id="eff-2",
            target_id="AMB-01", mission_ref="wrong"), "RESOURCE_UNAVAILABLE")
        released = self.core.actuators.apply_effect(request("release_responder", revision=1,
            effect_id="eff-2", target_id="AMB-01", mission_ref="m-1"))
        self.assertTrue(released.ok)
        self.assertEqual(self.snapshot().physical_state.responders["AMB-01"].status, "available")
        self.assertEqual(occupied.physical_state.responders["AMB-01"].status, "arrived")
        self.reject(request("release_responder", revision=2, effect_id="eff-3",
            target_id="AMB-01", mission_ref="m-1"), "RESOURCE_UNAVAILABLE")

    def test_route_topology_and_initial_timer(self):
        reserve = dict(target_id="AMB-01", mission_ref="m-1", destination_node_id="J-D1")
        for route in ([], ["R-IND"], ["R-HIGH"], ["R-LOW", "R-LOW"], ["missing"]):
            code = "UNKNOWN_TARGET" if route == ["missing"] else "ROUTE_UNAVAILABLE"
            self.reject(request("reserve_responder", **reserve, route_road_ids=route), code)
        for route in ("R-LOW", None, {}, [True], ["R-LOW"] * 6):
            self.reject(request("reserve_responder", **reserve, route_road_ids=route))
        self.reject(request("reserve_responder", **{**reserve, "destination_node_id": "HOSP-01"},
                            route_road_ids=["R-LOW"]), "UNKNOWN_TARGET")
        result = self.core.actuators.apply_effect(request("reserve_responder", **reserve,
            route_road_ids=["R-HIGH", "R-LINK", "R-DOWN"]))
        self.assertTrue(result.ok)
        unit = self.snapshot().physical_state.responders["AMB-01"]
        self.assertEqual((unit.status, unit.at_node_id, unit.edge_index, unit.edge_remaining_s),
                         ("enroute", "J-H1", 0, 70.0))
        self.reject(request("release_responder", revision=1, effect_id="eff-2",
            target_id="AMB-01", mission_ref="m-1"), "RESOURCE_UNAVAILABLE")
        self.assertTrue(self.core.lifecycle.advance_one("r-test", 1).ok)
        moved = self.snapshot().physical_state.responders['AMB-01']
        self.assertEqual(moved, replace(unit, edge_remaining_s=69.0))
        self.assertEqual(unit.edge_remaining_s, 70.0)

    def test_route_checks_flood_obstruction_signal_power_and_overflow(self):
        variants = [
            ("surface", 200), ("obstruction", 1), ("signal", "stop"),
            ("source", False), ("feeder", False), ("overflow", 1.7e308)]
        for kind, value in variants:
            with self.subTest(kind=kind):
                config = baseline_config()
                state = config["initial_physical"]
                if kind == "surface": state["water"]["surface_volume_m3"] = value
                if kind == "obstruction": state["roads"]["R-LOW"]["obstruction_fraction"] = value
                if kind == "signal": state["signals"]["J-D1"]["effective_mode"] = value
                if kind == "source": state["source"]["source_available"] = value
                if kind == "feeder": state["feeders"]["F-URBAN"]["effective_closed"] = value
                if kind == "overflow": state["roads"]["R-LOW"]["queue_vehicles"] = value
                self.core = city(initial_config=config)
                self.reject(request("reserve_responder", target_id="POL-01", mission_ref="m",
                    destination_node_id="J-D1", route_road_ids=["R-LOW"]),
                    "MODEL_LIMIT" if kind == "overflow" else "ROUTE_UNAVAILABLE")

    def test_budgets_no_eviction_and_duplicate_at_limit(self):
        with patch("city_core.effects.MAX_EFFECTS", 1):
            first = self.core.actuators.apply_effect(request())
            self.assertTrue(first.ok)
            self.reject(request(revision=1, effect_id="eff-2"), "MODEL_LIMIT")
            self.assertTrue(self.core.actuators.apply_effect(request()).value.duplicate)
        with patch("city_core.lifecycle.MAX_RECORDS", 1):
            self.reject(request(revision=1, effect_id="eff-2"), "MODEL_LIMIT")
        self.assertTrue(self.core.actuators.apply_effect(request(revision=1, effect_id="eff-2")).ok)

    def test_exact_flood_closure_and_adjacent_representable_volumes(self):
        for road, destination, cutoff in (("R-LOW", "J-D1", 200.0),
                                           ("R-HIGH", "J-R1", 1000.0)):
            for volume, closed in ((math.nextafter(cutoff, 0.0), False),
                                   (cutoff, True), (math.nextafter(cutoff, math.inf), True)):
                with self.subTest(road=road, volume=volume):
                    config = baseline_config()
                    config["initial_physical"]["water"]["surface_volume_m3"] = volume
                    self.core = city(initial_config=config)
                    data = request("reserve_responder", target_id="AMB-01", mission_ref="m-boundary",
                                   destination_node_id=destination, route_road_ids=[road])
                    if closed:
                        self.reject(data, "ROUTE_UNAVAILABLE")
                        self.assertEqual(self.snapshot().physical_state.responders["AMB-01"].status, "available")
                    else:
                        self.assertTrue(self.core.actuators.apply_effect(data).ok)

    def test_concurrent_duplicate_and_revision_order(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(self.core.actuators.apply_effect, [request()] * 8))
        self.assertTrue(all(r.ok for r in results))
        self.assertEqual(sum(not r.value.duplicate for r in results), 1)
        self.assertEqual(self.snapshot().context.revision, 1)
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(self.core.actuators.apply_effect,
                [request(revision=1, effect_id=f"eff-next-{i}") for i in range(8)]))
        self.assertEqual(sum(r.ok for r in results), 1)
        self.assertTrue(all(r.ok or r.error.code == "STATE_CONFLICT" for r in results))
