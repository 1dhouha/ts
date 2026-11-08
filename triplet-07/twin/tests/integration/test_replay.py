from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from city_core import baseline_config, create_core
from city_core.transitions import plain
from tests.effect_support import request
from tests.replay_support import FIXTURES, api, execute_payload, original, recording


class ReplayIntegrationTests(unittest.TestCase):
    def test_independent_control_and_flood_checkpoints_and_changed_cause(self):
        export,replay=api()
        fixtures=json.loads((FIXTURES/'replay_contract.json').read_text())
        for case in fixtures['cases']:
            payload=recording(case['name']); core=create_core()
            self.assertTrue(core.lifecycle.initialize('run',initial_config=payload['header']['initial_config']).ok)
            for record in payload['records']:
                execute_payload(core,{'records':[record]},'run')
                snap=core.observations.read_private('run').value
                checkpoint=case['checkpoints'].get(str(snap.context.tick)) if record['cause']=='STEP' else None
                if checkpoint:
                    self.assertAlmostEqual(snap.physical_state.water.surface_volume_m3,checkpoint['surface'])
                    self.assertAlmostEqual(snap.physical_state.water.basin_volume_m3,checkpoint['basin'])
                    self.assertAlmostEqual(snap.physical_state.roads['R-LOW'].queue_vehicles,checkpoint['low_queue'])
                    self.assertEqual(snap.access.reachable_by_origin['J-D1'],checkpoint['access'])
                    self.assertEqual(snap.power.hospital_powered,checkpoint['hospital_powered'])
                    self.assertEqual(snap.physical_state.responders['AMB-01'].status,checkpoint['status'])
                    self.assertAlmostEqual(snap.physical_state.responders['AMB-01'].edge_remaining_s,checkpoint['remaining'])
            result=replay(plain(export(core.lifecycle,'run').value),'copy')
            self.assertTrue(result.ok,result)
            self.assertEqual(result.value.observations.read_private('copy').value.physical_state,snap.physical_state)
        # Remove direct rain in a separately executed valid causal sequence.
        changed=recording('flood'); changed['header']['initial_config']['initial_physical']['water']['rain_mm_h']=0
        alternate=create_core(); self.assertTrue(alternate.lifecycle.initialize('alt',initial_config=changed['header']['initial_config']).ok)
        execute_payload(alternate,changed,'alt')
        snap=alternate.observations.read_private('alt').value
        self.assertAlmostEqual(snap.physical_state.water.surface_volume_m3,198.2)
        self.assertEqual(snap.physical_state.responders['AMB-01'].status,'enroute')
        self.assertEqual(snap.physical_state.responders['AMB-01'].edge_remaining_s,44)
        self.assertTrue(snap.access.reachable_by_origin['J-D1'])
        valid=plain(export(alternate.lifecycle,'alt').value)
        self.assertTrue(replay(valid,'changed-copy').ok)
        self.assertFalse(replay(changed,'forged-old-hashes').ok)

    def test_reset_recapture_equals_fresh_replay_and_old_pending_work_rejects(self):
        export,replay=api()
        core=original('flood'); payload=recording('normal')
        held=export(core.lifecycle,'r-source').value
        revision=held.final_context.revision
        self.assertTrue(core.lifecycle.reset('r-source',revision,'reset',initial_config=payload['header']['initial_config']).ok)
        for data in (request(run_id='r-source'),request('reserve_responder',run_id='r-source',
            target_id='AMB-01',mission_ref='old',destination_node_id='J-D1',route_road_ids=['R-LOW'])):
            self.assertEqual(core.actuators.apply_effect(data).error.code,'OLD_RUN')
        execute_payload(core,payload,'reset')
        reset=core.observations.read_private('reset').value
        result=replay(payload,'fresh'); self.assertTrue(result.ok,result)
        self.assertEqual(result.value.observations.read_private('fresh').value.physical_state,reset.physical_state)
        self.assertTrue(replay(plain(export(core.lifecycle,'reset').value),'reset-copy').ok)
        self.assertEqual(held.final_context.run_id,'r-source')
        self.assertEqual(held.records[0].input.effect['mission_ref'],'m-1')

    def test_same_time_order_and_duplicate_conflict_resource_exhaustion(self):
        export,replay=api()
        core=create_core(); self.assertTrue(core.lifecycle.initialize('run').ok)
        for index,mode in enumerate(('stop','priority','normal')):
            data=request('signal_mode',run_id='run',revision=index,effect_id=f'sig-{index}',target_id='J-D1',mode=mode)
            self.assertTrue(core.actuators.apply_effect(data).ok)
            self.assertTrue(core.actuators.apply_effect(data).value.duplicate)
        payload=plain(export(core.lifecycle,'run').value)
        self.assertEqual([r['context']['sim_time_ms'] for r in payload['records']],[0,0,0])
        self.assertTrue(replay(payload,'ordered').ok)
        payload['records'].reverse(); self.assertFalse(replay(payload,'reordered').ok)
        for index,unit in enumerate(('AMB-01','AMB-02','POL-01')):
            data=request('reserve_responder',run_id='run',revision=3+index,effect_id=f'unit-{index}',target_id=unit,
                         mission_ref='m',destination_node_id='J-D1',route_road_ids=['R-LOW'])
            self.assertTrue(core.actuators.apply_effect(data).ok)
        before=export(core.lifecycle,'run').value
        occupied=request('reserve_responder',run_id='run',revision=6,effect_id='extra',target_id='AMB-01',
                         mission_ref='extra',destination_node_id='J-D1',route_road_ids=['R-LOW'])
        self.assertEqual(core.actuators.apply_effect(occupied).error.code,'RESOURCE_UNAVAILABLE')
        conflict=request('signal_mode',run_id='run',revision=0,effect_id='sig-0',target_id='J-D1',mode='normal')
        self.assertEqual(core.actuators.apply_effect(conflict).error.code,'IDENTITY_CONFLICT')
        self.assertEqual(export(core.lifecycle,'run').value,before)
        self.assertTrue(replay(plain(before),'fleet').ok)

    def test_coherent_export_under_step_and_reset_races(self):
        export,replay=api()
        for n in range(4):
            core=create_core(); self.assertTrue(core.lifecycle.initialize('run').ok)
            with ThreadPoolExecutor(max_workers=3) as pool:
                futures=[pool.submit(export,core.lifecycle,'run'),pool.submit(core.lifecycle.advance_one,'run',0),
                         pool.submit(core.lifecycle.reset,'run',0,'new')]
                results=[f.result() for f in futures]
            captured=results[0]
            if captured.ok: self.assertTrue(replay(plain(captured.value),f'captured-{n}').ok)
            else: self.assertEqual(captured.error.code,'OLD_RUN')
            self.assertEqual(sum(r.ok for r in results[1:]),1)

    def test_capture_spans_trace_chunk_boundary_and_privacy(self):
        export,replay=api()
        core=create_core(); self.assertTrue(core.lifecycle.initialize('run').ok)
        # Effect-only commits span CHUNK_SIZE without adding slow physical steps.
        for index in range(257):
            self.assertTrue(core.actuators.apply_effect(request(run_id='run',revision=index,effect_id=f'e-{index}')).ok)
        payload=plain(export(core.lifecycle,'run').value)
        self.assertEqual(len(payload['records']),257)
        result=replay(payload,'chunks'); self.assertTrue(result.ok,result)
        self.assertEqual(result.value.metadata.read_metadata().value.context.revision,257)
        public=json.dumps(plain(core.metadata.read_metadata().value))
        for field in ('initial_config','final_digest','before_digest','randomness','model_profile','records'):
            self.assertNotIn(field,public)
