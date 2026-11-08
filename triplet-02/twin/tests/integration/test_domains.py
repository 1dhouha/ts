import json
from pathlib import Path
import unittest

from city_core import baseline_catalog, baseline_config, create_core
from city_core.transitions import digest, plain
from tests.effect_support import request
from tests.support import city
from tests.traffic_support import effect, reserve, snapshot, step, traffic_city
from tests.unit.test_air import air_city


class DomainIntegrationTests(unittest.TestCase):
    def test_source_driver_private_read_duplicate_and_reset(self):
        core=air_city(plant=False)
        original=snapshot(core)
        data=effect(core,'air_driver',source='B_SCENARIO',target_id='Z-RESIDENTIAL',source_ug_m3_s=100)
        self.assertEqual(snapshot(core).physical_state.air['Z-RESIDENTIAL'].pm25_ug_m3,10)
        first=step(core)
        self.assertEqual(first.physical_state.air['Z-RESIDENTIAL'].pm25_ug_m3,110)
        self.assertEqual(first.physical_state.air['Z-INDUSTRIAL'].pm25_ug_m3,10)
        self.assertTrue(core.scenarios.apply_effect(data).value.duplicate)
        self.assertEqual(snapshot(core),first)
        second=step(core)
        self.assertAlmostEqual(second.physical_state.air['Z-RESIDENTIAL'].pm25_ug_m3,10+100/1.01+100)
        self.assertEqual(original.physical_state.air['Z-RESIDENTIAL'].pm25_ug_m3,10)
        self.assertTrue(core.lifecycle.reset('r-test',second.context.revision,'new').ok)
        reset=snapshot(core,'new')
        self.assertEqual(reset.physical_state.air['Z-RESIDENTIAL'].external_source_ug_m3_s,0)
        self.assertEqual(reset.physical_state.air['Z-RESIDENTIAL'].pm25_ug_m3,10)
        self.assertEqual(core.scenarios.apply_effect(data).error.code,'OLD_RUN')

    def test_emergency_reads_share_resources_access_and_reset_without_call_state(self):
        core=traffic_city(surface=200,queue=0,arrivals=0)
        reserve(core,unit='AMB-01',destination='J-R1',route=('R-HIGH',))
        before=snapshot(core)
        for _ in range(3):
            self.assertEqual(snapshot(core),before)
            self.assertTrue(snapshot(core).access.reachable_by_origin['J-D1'])
            self.assertEqual(sum(u.status=='available' for u in snapshot(core).physical_state.responders.values()),2)
        for payload in ({'run_id':'r-test','caller':'help'},['r-test'],True):
            self.assertEqual(core.observations.read_private(payload).error.code,'INVALID_ARGUMENT')
            self.assertEqual(snapshot(core),before)
        for payload in ({'caller':'help','reported_flood':True},
            request('emergency_call',revision=before.context.revision,target_id='S-CALL-01',count=20),
            request('reported_air',revision=before.context.revision,target_id='S-AIR-IND',value=0)):
            self.assertFalse(core.actuators.apply_effect(payload).ok)
            self.assertEqual(snapshot(core),before)
        progressed=step(core)
        self.assertEqual(progressed.physical_state.responders['AMB-01'].edge_remaining_s,49)
        effect(core,'road_driver',source='B_SCENARIO',target_id='R-LINK',arrival_rate_veh_s=0,obstruction_fraction=1)
        blocked=step(core)
        self.assertFalse(blocked.access.reachable_by_origin['J-D1'])
        self.assertEqual(blocked.physical_state.responders['AMB-01'].edge_remaining_s,48)
        self.assertTrue(core.lifecycle.reset('r-test',blocked.context.revision,'new').ok)
        reset=snapshot(core,'new')
        self.assertEqual(sum(u.status=='available' for u in reset.physical_state.responders.values()),3)
        self.assertTrue(reset.access.reachable_by_origin['J-D1'])
        self.assertEqual(before.physical_state.responders['AMB-01'].edge_remaining_s,50)
        self.assertNotIn('call',plain(reset.physical_state))

    def test_device_isolation_and_reading_correction_never_imply_physical_outage(self):
        core=air_city()
        before=snapshot(core)
        for tag in ('quarantine','isolate_device','rollback_reading'):
            data=request(tag,target_id='S-AIR-IND')
            self.assertEqual(core.scenarios.apply_effect({**data,'source':'B_SCENARIO'}).error.code,'INVALID_ARGUMENT')
            self.assertEqual(snapshot(core),before)
        after=step(core)
        self.assertTrue(after.power.hospital_powered)
        self.assertTrue(after.power.plant_active)
        self.assertEqual(after.physical_state.air['Z-INDUSTRIAL'].pm25_ug_m3,10.2)

    def test_all_domains_replay_and_public_metadata_allowlist(self):
        core=air_city(concentration=1000,source=1)
        reserve(core)
        for _ in range(3): step(core)
        effect(core,'feeder_position',target_id='F-IND',closed=False)
        effect(core,'air_driver',source='B_SCENARIO',target_id='Z-INDUSTRIAL',source_ug_m3_s=0)
        for _ in range(4): step(core)
        held=snapshot(core)
        page=core.lifecycle.read_private_transitions('r-test',-1,100).value
        replay=create_core()
        self.assertTrue(replay.lifecycle.initialize('replay',initial_config=plain(page.header.initial_config)).ok)
        for record in page.records:
            self.assertEqual(digest(snapshot(replay,'replay').physical_state),record.before_digest)
            data=plain(record.input); data['run_id']='replay'
            if record.cause=='STEP': result=replay.lifecycle.advance_one(**data)
            else:
                handle=replay.scenarios if data['source']=='B_SCENARIO' else replay.actuators
                result=handle.apply_effect(data)
            self.assertTrue(result.ok)
            self.assertEqual(digest(snapshot(replay,'replay').physical_state),record.after_digest)
        self.assertEqual(snapshot(replay,'replay').physical_state,held.physical_state)
        public=json.dumps({'metadata':plain(core.metadata.read_metadata().value),
                           'token':plain(core.metadata.make_evidence_token('r-test').value)})
        for key in ('pm25','physical_state','external_source','responders','reachable','trusted','fault'):
            self.assertNotIn(key,public)

    def test_five_domain_fixture_real_core_conformance_and_family_handoffs(self):
        fixture=json.loads((Path(__file__).parents[2]/'fixtures/five_domain_contract.json').read_text())
        self.assertEqual(fixture['contract_version'],'pa-internal-0.2-draft')
        self.assertEqual(fixture['visibility'],'PRIVATE_A_B_EVALUATOR_ONLY')
        self.assertEqual(set(fixture['sensor_families']),{'traffic_camera','water_level','power_load','air_quality','emergency_call'})
        self.assertEqual(set(fixture['actuator_families']),{'dispatch','traffic_signal','water_valve','grid_switch','device_isolation_quarantine'})
        for family, handoff in {**fixture['sensor_families'],**fixture['actuator_families']}.items():
            self.assertEqual(set(handoff),{'a_path','b_consumer','c_handoff','status'})
            self.assertTrue(all(type(value) is str and value for value in handoff.values()))
            self.assertEqual(handoff['status'],'B_C_ONLY_PENDING_POLICY' if family=='device_isolation_quarantine'
                             else 'A_SUPPORT_IMPLEMENTED_BC_UNVERIFIED')
        catalog=baseline_catalog()
        for device in catalog['devices']:
            if device['family']!='pump_extension':
                self.assertIn(device['family'],{**fixture['sensor_families'],**fixture['actuator_families']})
        for case in fixture['cases']:
            with self.subTest(case=case['name']):
                config=baseline_config(); actual=config['initial_physical']
                for section, records in case['initial'].items():
                    if section in ('air','feeders'):
                        for key, fields in records.items(): actual[section][key].update(fields)
                    else: actual[section].update(records)
                core=city(initial_config=config)
                for data in case['effects']:
                    handle=core.scenarios if data['source']=='B_SCENARIO' else core.actuators
                    self.assertTrue(handle.apply_effect(data).ok)
                after=step(core); expected=case['expected']
                self.assertAlmostEqual(after.physical_state.roads['R-LOW'].queue_vehicles,expected['traffic_low_queue'])
                self.assertAlmostEqual(after.physical_state.water.surface_volume_m3,expected['water_surface_m3'])
                self.assertAlmostEqual(after.power.total_delivered_load_kw,expected['power_delivered_kw'])
                self.assertAlmostEqual(after.physical_state.air['Z-INDUSTRIAL'].pm25_ug_m3,expected['air_industrial_ug_m3'])
                self.assertEqual(after.access.reachable_by_origin['J-D1'],expected['emergency_access'])
                self.assertEqual(sum(u.status=='available' for u in after.physical_state.responders.values()),expected['available_units'])
                self.assertNotIn('trusted',plain(after.physical_state.air['Z-INDUSTRIAL']))
