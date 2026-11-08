import unittest

from city_core import baseline_config
from tests.support import city
from tests.effect_support import request
from tests.traffic_support import traffic_city, snapshot, step, effect, reserve


class ResponderTravelTests(unittest.TestCase):
    def test_both_services_require_elapsed_time_and_remain_reserved_on_arrival(self):
        for unit in ('AMB-01','AMB-02','POL-01'):
            with self.subTest(unit=unit):
                core = traffic_city(queue=0,arrivals=0)
                data = reserve(core,unit=unit)
                held = snapshot(core)
                self.assertEqual(held.context.tick,0)
                self.assertEqual(held.physical_state.responders[unit].status,'enroute')
                self.assertEqual(held.physical_state.responders[unit].edge_remaining_s,30)
                for n in range(1,31):
                    after = step(core)
                    responder = after.physical_state.responders[unit]
                    self.assertEqual(responder.status,'enroute' if n<30 else 'arrived')
                    self.assertEqual(responder.edge_remaining_s,30-n)
                    self.assertEqual(responder.at_node_id,'J-H1' if n<30 else 'J-D1')
                self.assertTrue(core.actuators.apply_effect(data).value.duplicate)
                self.assertEqual(snapshot(core),after)
                still = step(core).physical_state.responders[unit]
                self.assertEqual(still,responder)
                rejected = request('reserve_responder',revision=after.context.revision+1,
                    effect_id='double',target_id=unit,mission_ref='other',destination_node_id='J-D1',route_road_ids=[])
                self.assertEqual(core.actuators.apply_effect(rejected).error.code,'RESOURCE_UNAVAILABLE')
                effect(core,'release_responder',target_id=unit,mission_ref='m-one')
                released = snapshot(core).physical_state.responders[unit]
                self.assertEqual((released.status,released.at_node_id,released.route_road_ids),('available','J-D1',()))
                self.assertEqual(held.physical_state.responders[unit].edge_remaining_s,30)

    def test_fractional_edge_carryover_and_suffix_topology(self):
        core = traffic_city(queue=.25,arrivals=.5)
        reserve(core,destination='J-D2',route=('R-HIGH','R-LINK'))
        for n in range(1,92):
            responder = step(core).physical_state.responders['AMB-01']
            if n==50: self.assertEqual((responder.edge_index,responder.edge_remaining_s),(0,.5))
            if n==51:
                self.assertEqual((responder.at_node_id,responder.edge_index,responder.edge_remaining_s),('J-R1',1,40.0))
            self.assertEqual(responder.status,'enroute' if n<91 else 'arrived')
        self.assertEqual((responder.at_node_id,responder.edge_index,responder.edge_remaining_s),('J-D2',2,0))
        self.assertEqual(responder.route_road_ids,('R-HIGH','R-LINK'))

    def test_started_edge_pauses_resumes_without_timer_resampling(self):
        core = traffic_city()
        reserve(core)
        self.assertEqual(step(core).physical_state.responders['AMB-01'].edge_remaining_s,49)
        effect(core,'signal_mode',target_id='J-D1',mode='stop')
        for _ in range(3):
            responder = step(core).physical_state.responders['AMB-01']
            self.assertEqual((responder.status,responder.edge_remaining_s,responder.edge_index,responder.at_node_id),
                             ('blocked',49,0,'J-H1'))
        effect(core,'signal_mode',target_id='J-D1',mode='priority')
        recovered = step(core).physical_state.responders['AMB-01']
        self.assertEqual((recovered.status,recovered.edge_remaining_s),('enroute',48))
        self.assertEqual(recovered.mission_ref,'m-one')

    def test_closed_next_edge_waits_with_zero_timer_and_samples_posttraffic_on_recovery(self):
        core = traffic_city(queue=0,arrivals=.5)
        reserve(core,destination='J-D2',route=('R-HIGH','R-LINK'))
        effect(core,'signal_mode',target_id='J-D2',mode='stop')
        for _ in range(50): final = step(core)
        responder = final.physical_state.responders['AMB-01']
        self.assertEqual((responder.status,responder.at_node_id,responder.edge_index,responder.edge_remaining_s),
                         ('blocked','J-R1',1,0))
        for _ in range(2): self.assertEqual(step(core).physical_state.responders['AMB-01'],responder)
        effect(core,'signal_mode',target_id='J-D2',mode='normal')
        resumed = step(core)
        # LINK accumulated26 during52 closed ticks, then arrivals==departure.
        self.assertEqual(resumed.physical_state.roads['R-LINK'].queue_vehicles,26)
        self.assertEqual(resumed.physical_state.responders['AMB-01'].edge_remaining_s,91)
        effect(core,'signal_mode',target_id='J-D2',mode='priority')
        self.assertEqual(step(core).physical_state.responders['AMB-01'].edge_remaining_s,90)

    def test_duplicate_finite_allocations_and_rejected_release_keep_state(self):
        core = traffic_city(queue=0,arrivals=0)
        for unit in ('AMB-01','AMB-02','POL-01'):
            data = reserve(core,unit=unit)
            before = snapshot(core)
            self.assertTrue(core.actuators.apply_effect(data).value.duplicate)
            self.assertEqual(snapshot(core),before)
            failed = request('release_responder',revision=before.context.revision,effect_id='too-early',
                             target_id=unit,mission_ref='m-one')
            self.assertEqual(core.actuators.apply_effect(failed).error.code,'RESOURCE_UNAVAILABLE')
            self.assertEqual(snapshot(core),before)
        self.assertEqual(sum(u.status=='enroute' for u in snapshot(core).physical_state.responders.values()),3)
        step(core)
        self.assertEqual({u.edge_remaining_s for u in snapshot(core).physical_state.responders.values()},{29})

    def test_posttraffic_next_edge_numeric_failure_is_atomic(self):
        config = baseline_config()
        for road in config['initial_physical']['roads'].values():
            road.update(queue_vehicles=0,arrival_rate_veh_s=0)
        config['initial_physical']['roads']['R-LINK']['queue_vehicles'] = 1.7e308
        core = city(initial_config=config)
        reserve(core,destination='J-D2',route=('R-HIGH','R-LINK'))
        for _ in range(49): step(core)
        # Valid initial second-edge queue overflows its duration only on entry.
        before = snapshot(core)
        page = core.lifecycle.read_private_transitions('r-test',-1,100).value
        rejected = core.lifecycle.advance_one('r-test',before.context.revision)
        self.assertFalse(rejected.ok)
        self.assertEqual(rejected.error.code,'MODEL_LIMIT')
        self.assertEqual(snapshot(core),before)
        self.assertEqual(core.lifecycle.read_private_transitions('r-test',-1,100).value,page)
        self.assertEqual(core.metadata.read_metadata().value.context,before.context)
        effect(core,'road_driver',source='B_SCENARIO',target_id='R-LINK',
               arrival_rate_veh_s=0,obstruction_fraction=1)
        recovered = step(core)
        self.assertEqual(recovered.physical_state.responders['AMB-01'].status,'blocked')
        self.assertEqual(recovered.physical_state.responders['AMB-01'].at_node_id,'J-R1')
        self.assertEqual(recovered.physical_state.responders['AMB-01'].edge_remaining_s,0)
        self.assertEqual(recovered.context.tick,50)

    def test_arrival_does_not_clear_road_or_restore_normal_signal(self):
        core = traffic_city(queue=0,arrivals=0,obstructions={'R-LOW':.5},modes={'J-D1':'priority'})
        reserve(core,unit='POL-01')
        for _ in range(30): after = step(core)
        self.assertEqual(after.physical_state.responders['POL-01'].status,'arrived')
        self.assertEqual(after.physical_state.roads['R-LOW'].obstruction_fraction,.5)
        self.assertEqual(after.physical_state.signals['J-D1'].effective_mode,'priority')
        self.assertEqual(after.traffic['R-LOW'].capacity_veh_s,.375)

    def test_zero_length_allocation_has_no_step_motion_or_automatic_release(self):
        core = traffic_city()
        reserve(core,destination='J-H1',route=())
        held = snapshot(core).physical_state.responders['AMB-01']
        self.assertEqual(held.status,'arrived')
        self.assertEqual(step(core).physical_state.responders['AMB-01'],held)
        effect(core,'release_responder',target_id='AMB-01',mission_ref='m-one')
        self.assertEqual(step(core).physical_state.responders['AMB-01'].status,'available')
