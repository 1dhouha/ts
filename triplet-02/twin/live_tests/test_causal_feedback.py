"""Deterministic causal checks for documented CityCore actor paths."""
import os
import unittest

os.environ.setdefault("TWIN_ALLOW_INSECURE_DEV_TOKENS", "1")

from city_live.runtime import LiveCity
from twin_interface.auth import Role, get_token_for_role
from twin_interface.facade import TwinFacade
from twin_observability.event_store import EventStore


class CausalFeedbackTests(unittest.TestCase):
    def city(self, run_id):
        city = LiveCity(run_id, seed=1406)
        self.addCleanup(city.close)
        return city

    def set_effect(self, city, device_id, command, params, request_id):
        return city.execute_actuator_command(
            device_id, command, params, command_id=request_id,
        )

    def test_pump_speed_has_distinct_citycore_discharge(self):
        speeds = (0, 35, 70, 100)
        observed = []
        for speed in speeds:
            city = self.city(f"pump-{speed}")
            receipt = self.set_effect(
                city, "P-03", "set_speed", {"speed_pct": speed},
                f"pump-speed-{speed}",
            )
            self.assertEqual(receipt["status"], "APPLIED")
            self.assertEqual(receipt["requested_effect"], {
                "tag": "pump_speed", "target_id": "P-03",
                "speed_fraction": speed / 100,
            })
            self.assertEqual(receipt["effective_effect"], receipt["requested_effect"])
            self.assertEqual(receipt["execution_reason"], "CITYCORE_EFFECT_COMMITTED")
            city.step()
            state = city._snapshot().physical_state
            observed.append((
                state.pump.effective_speed_fraction,
                state.water.last_step.pump_out_m3,
            ))

        self.assertEqual([item[0] for item in observed], [0, .35, .7, 1.0])
        self.assertEqual([item[1] for item in observed], [0, .42, .84, 1.2])

    def test_valve_position_controls_only_its_citycore_inlet_path(self):
        closed, opened = self.city("valve-closed"), self.city("valve-open")
        self.set_effect(closed, "V-12", "set_opening", {"open_pct": 0}, "valve-0")
        self.set_effect(opened, "V-12", "set_opening", {"open_pct": 100}, "valve-100")
        closed.step()
        opened.step()

        closed_state = closed._snapshot().physical_state
        open_state = opened._snapshot().physical_state
        self.assertEqual(closed_state.water.last_step.inlet_in_m3, 0)
        self.assertEqual(open_state.water.last_step.inlet_in_m3, .6)
        self.assertGreater(open_state.water.basin_volume_m3, closed_state.water.basin_volume_m3)

    def test_responder_dispatch_updates_and_advances_canonical_response_state(self):
        city = self.city("responder-dispatch")
        receipt = self.set_effect(
            city,
            "AMB-01",
            "dispatch",
            {
                "destination_node_id": "J-D1",
                "route_road_ids": ["R-LOW"],
                "mission_ref": "causal-response-1",
            },
            "responder-dispatch-1",
        )

        self.assertEqual(receipt["status"], "APPLIED")
        self.assertEqual(receipt["execution_status"], "EXECUTED")
        self.assertTrue(receipt["city_core_updated"])
        assigned = city._snapshot().physical_state.responders["AMB-01"]
        self.assertEqual(assigned.status, "enroute")
        self.assertEqual(assigned.mission_ref, "causal-response-1")
        self.assertEqual(assigned.destination_node_id, "J-D1")
        self.assertEqual(assigned.route_road_ids, ("R-LOW",))

        city.step()
        progressed = city._snapshot().physical_state.responders["AMB-01"]
        self.assertEqual(progressed.status, "enroute")
        self.assertLess(progressed.edge_remaining_s, assigned.edge_remaining_s)

    def test_signal_capacity_changes_only_its_linked_traffic_sensor(self):
        cities = {mode: self.city(f"signal-{mode}") for mode in ("normal", "priority", "stop")}
        for mode, city in cities.items():
            city.traffic("surge")
            city.command("A-SIG-01", mode, command_id=f"signal-{mode}")
            for _ in range(10):
                city.step()

        linked = {
            mode: city.sensor_compare("DTN-TRF-01")["actual_value"]
            for mode, city in cities.items()
        }
        unrelated = {
            city.sensor_compare("DTN-TRF-02")["actual_value"]
            for city in cities.values()
        }
        self.assertEqual(linked["stop"], 0)
        self.assertLess(linked["normal"], linked["priority"])
        self.assertEqual(len(unrelated), 1)

    def test_second_signal_changes_only_its_two_topology_linked_counters(self):
        cities = {mode: self.city(f"second-signal-{mode}") for mode in ("normal", "priority")}
        for mode, city in cities.items():
            city.traffic("surge")
            city.command("A-SIG-02", mode, command_id=f"second-signal-{mode}")
            for _ in range(10):
                city.step()

        linked = {
            mode: (
                city.sensor_compare("DTN-TRF-02")["actual_value"],
                city.sensor_compare("RES-TRF-01")["actual_value"],
            )
            for mode, city in cities.items()
        }
        unrelated = {
            sensor_id: {
                mode: city.sensor_compare(sensor_id)["actual_value"]
                for mode, city in cities.items()
            }
            for sensor_id in ("DTN-TRF-01",)
        }

        self.assertEqual(linked["normal"], (30.0, 30.0))
        self.assertEqual(linked["priority"], (45.0, 45.0))
        self.assertEqual(unrelated["DTN-TRF-01"]["normal"],
                         unrelated["DTN-TRF-01"]["priority"])

    def test_scenario_observation_brain_actions_and_post_action_feedback(self):
        baseline = self.city("scenario-no-brain-action")
        acted = self.city("scenario-brain-feedback")
        baseline_facade = TwinFacade(baseline)
        event_store = EventStore()
        acted_facade = TwinFacade(acted, event_store=event_store)
        brain_token = get_token_for_role(Role.BRAIN)

        for city in (baseline, acted):
            city.traffic("surge")
            city.start_b_scenario("scn-05")
            for _ in range(5):
                city.step()

        def brain_read(facade):
            response = facade.get_sensor_readings(
                brain_token, sensor_id="DTN-TRF-01"
            )
            self.assertTrue(response["ok"], response)
            readings = response["data"]["readings"]
            self.assertEqual(len(readings), 1)
            return readings[0]

        initial = brain_read(acted_facade)
        self.assertEqual(initial["reported_value"], 105.0)
        self.assertEqual(brain_read(baseline_facade)["reported_value"], 105.0)
        self.assertEqual(baseline.actions(), [])
        self.assertEqual(
            baseline.diagnostics()["physical_state"]["signals"]["J-D1"]["effective_mode"],
            "normal",
        )

        priority = acted_facade.send_actuator_command(
            brain_token,
            "DTN-TLT-01",
            "set_mode",
            {"mode": "priority"},
            request_id="scn05-priority-action",
        )
        self.assertTrue(priority["ok"], priority)
        self.assertEqual(priority["data"]["status"], "APPLIED")
        self.assertEqual(priority["data"]["execution_status"], "EXECUTED")
        self.assertTrue(priority["data"]["city_core_updated"])
        self.assertEqual(
            priority["data"]["correlation_id"],
            acted._scenario_runner.correlation_id,
        )

        for _ in range(5):
            baseline.step()
            acted.step()

        after_priority = brain_read(acted_facade)
        baseline_reading = brain_read(baseline_facade)
        self.assertEqual(after_priority["reported_value"], 37.5)
        self.assertEqual(baseline_reading["reported_value"], 30.0)
        self.assertEqual(
            acted.sensor_compare("DTN-TRF-01")["actual_value"],
            after_priority["reported_value"],
        )
        self.assertEqual(
            acted.diagnostics()["physical_state"]["signals"]["J-D1"]["effective_mode"],
            "priority",
        )
        self.assertEqual(
            baseline.diagnostics()["physical_state"]["signals"]["J-D1"]["effective_mode"],
            "normal",
        )

        stop = acted_facade.send_actuator_command(
            brain_token,
            "DTN-TLT-01",
            "set_mode",
            {"mode": "stop"},
            request_id="scn05-stop-action",
        )
        self.assertTrue(stop["ok"], stop)
        for _ in range(10):
            baseline.step()
            acted.step()

        after_stop = brain_read(acted_facade)
        self.assertEqual(after_stop["reported_value"], 0.0)
        self.assertEqual(brain_read(baseline_facade)["reported_value"], 30.0)
        self.assertEqual(
            acted.diagnostics()["physical_state"]["signals"]["J-D1"]["effective_mode"],
            "stop",
        )
        self.assertEqual(
            baseline.diagnostics()["physical_state"]["signals"]["J-D1"]["effective_mode"],
            "normal",
        )
        self.assertFalse(any(
            event["event_type"] == "ACTUATOR_COMMAND_REJECTED"
            for event in event_store.get_latest(limit=100)
        ))

    def test_traffic_demand_brain_action_and_subsequent_actual_feedback(self):
        baseline = self.city("traffic-feedback-baseline")
        acted = self.city("traffic-feedback-acted")
        baseline_facade = TwinFacade(baseline)
        acted_facade = TwinFacade(acted)
        brain_token = get_token_for_role(Role.BRAIN)

        for city in (baseline, acted):
            city.traffic("surge")
            for _ in range(10):
                city.step()

        for city in (baseline, acted):
            road = city._snapshot().physical_state.roads["R-LOW"]
            self.assertGreater(road.arrival_rate_veh_s, 0)
            self.assertGreater(road.queue_vehicles, 0)

        baseline_initial = baseline.sensor_compare("DTN-TRF-01")
        acted_initial = acted.sensor_compare("DTN-TRF-01")
        self.assertEqual(acted_initial["actual_value"], baseline_initial["actual_value"])
        self.assertEqual(acted_initial["reported_value"], baseline_initial["reported_value"])
        self.assertEqual(
            acted._snapshot().physical_state.roads,
            baseline._snapshot().physical_state.roads,
        )

        response = acted_facade.send_actuator_command(
            brain_token,
            "DTN-TLT-01",
            "set_mode",
            {"mode": "priority"},
            request_id="traffic-feedback-brain-priority",
        )
        self.assertTrue(response["ok"], response)
        self.assertEqual(response["data"]["status"], "APPLIED")
        self.assertTrue(response["data"]["city_core_updated"])
        self.assertEqual(
            acted._snapshot().physical_state.signals["J-D1"].effective_mode,
            "priority",
        )

        # DTN traffic sensors publish every 10 ticks and average that window.
        for _ in range(10):
            baseline.step()
            acted.step()

        baseline_observation = baseline_facade.get_sensor_readings(
            brain_token, sensor_id="DTN-TRF-01"
        )["data"]["readings"][0]
        acted_observation = acted_facade.get_sensor_readings(
            brain_token, sensor_id="DTN-TRF-01"
        )["data"]["readings"][0]
        baseline_after = baseline.sensor_compare("DTN-TRF-01")
        acted_after = acted.sensor_compare("DTN-TRF-01")
        self.assertEqual(baseline_after["actual_value"], 30.0)
        self.assertEqual(acted_after["actual_value"], 45.0)
        self.assertEqual(acted_observation["reported_value"], acted_after["actual_value"])
        self.assertEqual(baseline_observation["reported_value"], baseline_after["actual_value"])
        self.assertGreater(
            baseline._snapshot().physical_state.roads["R-LOW"].queue_vehicles,
            acted._snapshot().physical_state.roads["R-LOW"].queue_vehicles,
        )
        self.assertEqual(
            acted.sensor_compare("DTN-TRF-02")["actual_value"],
            baseline.sensor_compare("DTN-TRF-02")["actual_value"],
        )

    def test_unsupported_valve_is_rejected_and_audited_without_effect(self):
        city = self.city("unsupported-valves")
        event_store = EventStore()
        facade = TwinFacade(city, event_store=event_store)
        brain_token = get_token_for_role(Role.BRAIN)
        for _ in range(10):
            city.step()
        sensor_values = {
            sensor_id: reading["actual_value"]
            for sensor_id, reading in city._sensors.latest.items()
        }

        for device_id in ("IND-VLV-01", "DTN-VLV-01", "RES-VLV-01"):
            with self.subTest(device_id=device_id):
                before = city._snapshot()
                response = facade.send_actuator_command(
                    brain_token,
                    device_id,
                    "set_opening",
                    {"open_pct": 25},
                    request_id=f"unsupported-{device_id.lower()}",
                )

                self.assertFalse(response["ok"])
                self.assertEqual(
                    response["error"]["details"]["execution_status"], "REJECTED"
                )
                self.assertEqual(
                    response["error"]["details"]["reason"], "UNSUPPORTED_DEVICE"
                )
                self.assertFalse(response["error"]["details"]["city_core_updated"])
                self.assertEqual(city.status()["revision"], before.context.revision)
                self.assertEqual(city._snapshot().physical_state, before.physical_state)
                self.assertEqual(
                    city._snapshot().physical_state.valve,
                    before.physical_state.valve,
                )
                self.assertEqual({
                    sensor_id: city.sensor_compare(sensor_id)["actual_value"]
                    for sensor_id in sensor_values
                }, sensor_values)

        rejected = event_store.get_latest(limit=10)
        self.assertEqual(len(rejected), 3)
        self.assertTrue(all(
            event["event_type"] == "ACTUATOR_COMMAND_REJECTED"
            and event["payload"]["reason"] == "UNSUPPORTED_DEVICE"
            for event in rejected
        ))

    def test_sensor_fault_is_downstream_and_reset_repeats_deterministically(self):
        faulted, control = self.city("sensor-faulted"), self.city("sensor-control")
        for city in (faulted, control):
            for _ in range(5):
                city.step()

        before_fault = faulted._snapshot().physical_state
        faulted.sensor_fault(
            "PRT-WLV-01", "FALSE_READING", {"value": 180.0}
        )
        for _ in range(5):
            faulted.step()
            control.step()

        faulted_reading = faulted.sensor_compare("PRT-WLV-01")
        control_reading = control.sensor_compare("PRT-WLV-01")
        self.assertEqual(faulted_reading["actual_value"], control_reading["actual_value"])
        self.assertEqual(faulted_reading["reported_value"], 180.0)
        self.assertEqual(control_reading["reported_value"], control_reading["actual_value"])
        self.assertNotEqual(
            faulted._snapshot().physical_state, before_fault
        )
        self.assertEqual(
            faulted._snapshot().physical_state, control._snapshot().physical_state
        )

        faulted.reset("sensor-fault-reset")
        control.reset("sensor-control-reset")
        for _ in range(10):
            faulted.step()
            control.step()
        def normalized(readings):
            return [
                {key: value for key, value in reading.items() if key != "run_id"}
                for reading in readings
            ]

        self.assertEqual(
            normalized(faulted.public.observations()),
            normalized(control.public.observations()),
        )


if __name__ == "__main__":
    unittest.main()
