from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
import json
from pathlib import Path
import unittest

from city_core import create_core, baseline_config
from city_core.transitions import digest, plain
from tests.effect_support import request
from tests.traffic_support import traffic_city, snapshot, step, effect, reserve


class TrafficIntegrationTests(unittest.TestCase):
    def test_rain_flood_access_queue_and_inflight_pause_with_pump_recovery(self):
        config = baseline_config()
        actual = config['initial_physical']
        actual['water'].update(basin_volume_m3=6000,surface_volume_m3=199.4,rain_mm_h=36)
        actual['valve']['effective_opening_fraction'] = 1
        from tests.support import city
        core = city(initial_config=config)
        reserve(core)
        before = snapshot(core)
        flooded = step(core)
        self.assertAlmostEqual(flooded.physical_state.water.surface_volume_m3,200)
        self.assertTrue(flooded.flood['R-LOW'].flooded)
        self.assertEqual(flooded.traffic['R-LOW'].capacity_veh_s,0)
        self.assertAlmostEqual(flooded.physical_state.roads['R-LOW'].queue_vehicles,10.2)
        self.assertTrue(flooded.access.reachable_by_origin['J-D1'])
        unit = flooded.physical_state.responders['AMB-01']
        self.assertEqual((unit.status,unit.edge_remaining_s,unit.at_node_id),('blocked',50,'J-H1'))
        effect(core,'road_driver',source='B_SCENARIO',target_id='R-LINK',arrival_rate_veh_s=.2,obstruction_fraction=1)
        self.assertFalse(snapshot(core).access.reachable_by_origin['J-D1'])
        self.assertEqual(snapshot(core).physical_state.responders['AMB-01'],unit)
        effect(core,'pump_enable',target_id='P-03',enabled=True)
        effect(core,'feeder_position',target_id='F-ESS',closed=False)
        no_power = step(core)
        self.assertFalse(no_power.power.hospital_powered)
        self.assertFalse(no_power.access.reachable_by_origin['J-D1'])
        self.assertEqual(no_power.physical_state.responders['AMB-01'].edge_remaining_s,50)
        effect(core,'feeder_position',target_id='F-ESS',closed=True)
        for _ in range(2): recovered = step(core)
        self.assertFalse(recovered.flood['R-LOW'].flooded)
        self.assertTrue(recovered.access.reachable_by_origin['J-D1'])
        self.assertEqual(recovered.physical_state.responders['AMB-01'].edge_remaining_s,49)
        self.assertEqual(before.physical_state.responders['AMB-01'].status,'enroute')
        self.assertEqual(before.physical_state.water.surface_volume_m3,199.4)

    def test_source_outage_stalls_signal_routes_but_not_unsignalized_high_road(self):
        core = traffic_city(queue=0,arrivals=0)
        reserve(core)
        reserve(core,unit='POL-01',destination='J-R1',route=('R-HIGH',))
        effect(core,'source_driver',source='B_SCENARIO',available=False)
        after = step(core)
        self.assertEqual(after.physical_state.responders['AMB-01'].status,'blocked')
        self.assertEqual(after.physical_state.responders['AMB-01'].edge_remaining_s,30)
        self.assertEqual(after.physical_state.responders['POL-01'].edge_remaining_s,49)
        self.assertFalse(after.power.hospital_powered)
        self.assertTrue(after.access.reachable_by_origin['J-R1'])
        self.assertFalse(after.access.reachable_by_origin['J-D1'])

    def test_mixed_replay_reset_held_snapshots_and_private_evidence(self):
        core = traffic_city(queue=.25,arrivals=.5)
        reserve(core,destination='J-D2',route=('R-HIGH','R-LINK'))
        for _ in range(55): step(core)
        effect(core,'signal_mode',target_id='J-D2',mode='stop')
        for _ in range(3): step(core)
        effect(core,'signal_mode',target_id='J-D2',mode='normal')
        for _ in range(39): step(core)
        held = snapshot(core)
        self.assertEqual(held.physical_state.responders['AMB-01'].status,'arrived')
        page = core.lifecycle.read_private_transitions('r-test',-1,1000).value
        replay = create_core()
        self.assertTrue(replay.lifecycle.initialize('replay',initial_config=plain(page.header.initial_config)).ok)
        for record in page.records:
            self.assertEqual(digest(snapshot(replay,'replay').physical_state),record.before_digest)
            if record.cause=='STEP':
                result = replay.lifecycle.advance_one('replay',record.input.expected_revision)
            else:
                data = plain(record.input)
                data['run_id'] = 'replay'
                handle = replay.scenarios if data['source']=='B_SCENARIO' else replay.actuators
                result = handle.apply_effect(data)
            self.assertTrue(result.ok)
            self.assertEqual(digest(snapshot(replay,'replay').physical_state),record.after_digest)
        self.assertEqual(snapshot(replay,'replay').physical_state,held.physical_state)
        public = json.dumps({'metadata':plain(core.metadata.read_metadata().value),
                             'token':plain(core.metadata.make_evidence_token('r-test').value)})
        for key in ('traffic','access','queue','mission','route','physical_state','flood','remaining'):
            self.assertNotIn(key,public)
        self.assertFalse(hasattr(core.metadata,'access'))
        self.assertTrue(core.lifecycle.reset('r-test',held.context.revision,'new').ok)
        reset = snapshot(core,'new')
        self.assertEqual(reset.physical_state.roads['R-LOW'].queue_vehicles,10)
        self.assertEqual(reset.physical_state.responders['AMB-01'].status,'available')
        self.assertEqual(core.lifecycle.advance_one('r-test',held.context.revision).error.code,'OLD_RUN')
        self.assertEqual(core.lifecycle.read_private_transitions('new',-1,1).value.records,())
        self.assertEqual(held.physical_state.responders['AMB-01'].status,'arrived')

    def test_dispatch_race_one_assignment_and_atomic_route_rejects(self):
        core = traffic_city()
        data = request('reserve_responder',target_id='AMB-01',mission_ref='m',destination_node_id='J-D1',route_road_ids=['R-LOW'])
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(core.actuators.apply_effect,[data]*8))
        self.assertTrue(all(r.ok for r in results))
        self.assertEqual(sum(not r.value.duplicate for r in results),1)
        self.assertEqual(snapshot(core).context.revision,1)
        for route in ([],['R-IND'],['R-LOW','R-LOW'],['R-HIGH','R-DOWN']):
            before = snapshot(core)
            bad = request('reserve_responder',revision=1,effect_id='not-consumed',target_id='AMB-02',
                          mission_ref='m-other',destination_node_id='J-D1',route_road_ids=route)
            self.assertEqual(core.actuators.apply_effect(bad).error.code,'ROUTE_UNAVAILABLE')
            self.assertEqual(snapshot(core),before)
        before = snapshot(core)
        for mode in ('green','PRIORITY',True,None):
            bad = request('signal_mode',revision=1,effect_id='invalid',target_id='J-D1',mode=mode)
            self.assertEqual(core.actuators.apply_effect(bad).error.code,'INVALID_ARGUMENT')
            self.assertEqual(snapshot(core),before)

    def test_internal_versioned_traffic_checkpoint(self):
        path = Path(__file__).resolve().parents[2]/'fixtures/traffic_contract.json'
        fixture = json.loads(path.read_text())
        self.assertEqual(fixture['contract_version'],'pa-internal-0.2-draft')
        self.assertEqual(fixture['visibility'],'PRIVATE_A_B_EVALUATOR_ONLY')
        for case in fixture['cases']:
            core = traffic_city(**case['setup'])
            after = step(core)
            self.assertAlmostEqual(after.physical_state.roads['R-LOW'].queue_vehicles,case['expected_low_queue'])
            self.assertEqual(after.access.reachable_by_origin['J-D1'],case['expected_access'])
