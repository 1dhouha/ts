from copy import deepcopy
from dataclasses import FrozenInstanceError
import json
import unittest
from unittest.mock import patch

from city_core import create_core
from city_core.transitions import plain
from tests.replay_support import api, original, recording


class ReplayTests(unittest.TestCase):
    def test_export_is_complete_detached_immutable_and_cannot_be_public(self):
        export,replay=api()
        core=original('flood')
        before=core.observations.read_private('r-source').value
        result=export(core.lifecycle,'r-source')
        self.assertTrue(result.ok)
        value=result.value
        self.assertEqual(value.randomness,'none')
        self.assertEqual(len(value.records),before.context.revision)
        self.assertEqual(value.final_context,before.context)
        with self.assertRaises(FrozenInstanceError): value.randomness='seeded'
        with self.assertRaises(TypeError): value.header.initial_config['parameters']['step_ms']=1
        with self.assertRaises(FrozenInstanceError): value.records[0].record_index=10
        detached=plain(value); detached['records'].clear()
        self.assertEqual(len(value.records),before.context.revision)
        self.assertEqual(core.observations.read_private('r-source').value,before)
        self.assertEqual(export(core.lifecycle,'old').error.code,'OLD_RUN')
        self.assertEqual(export(core.metadata,'r-source').error.code,'INVALID_ARGUMENT')
        self.assertEqual(export(create_core().lifecycle,'r-source').error.code,'NOT_READY')
        self.assertFalse(hasattr(core.metadata,'export_replay'))

    def test_normal_and_flood_json_roundtrip_replay_exact_trajectories(self):
        export,replay=api()
        for name in ('normal','flood'):
            with self.subTest(name=name):
                payload=recording(name)
                result=replay(json.loads(json.dumps(payload)),f'replay-{name}')
                self.assertTrue(result.ok,result)
                exported=plain(export(result.value.lifecycle,f'replay-{name}').value)
                expected=deepcopy(payload)
                expected['header']['initial_context']['run_id']=f'replay-{name}'
                expected['final_context']['run_id']=f'replay-{name}'; expected['python_version']=exported['python_version']
                for record in expected['records']:
                    record['context']['run_id']=f'replay-{name}'
                    record['input']['run_id']=f'replay-{name}'
                self.assertEqual(exported,expected)

    def test_empty_run_and_duplicate_lookups_do_not_create_replay_records(self):
        export,replay=api()
        core=create_core(); self.assertTrue(core.lifecycle.initialize('empty').ok)
        payload=plain(export(core.lifecycle,'empty').value)
        self.assertEqual(payload['records'],[])
        result=replay(payload,'empty-replay'); self.assertTrue(result.ok,result)
        self.assertEqual(result.value.metadata.read_metadata().value.context.revision,0)
        source=original()
        before=export(source.lifecycle,'r-source').value
        request=plain(before.records[0].input)
        self.assertTrue(source.actuators.apply_effect(request).value.duplicate)
        self.assertEqual(export(source.lifecycle,'r-source').value,before)

    def test_required_metadata_versions_profile_runtime_and_randomness(self):
        export,replay=api()
        base=recording()
        for key in tuple(base):
            payload=deepcopy(base); del payload[key]
            self.assertEqual(replay(payload,'r').error.code,'INVALID_ARGUMENT')
        for key in ('format_version','model_profile','python_version','numeric_profile','randomness'):
            payload={**base,key:'unknown'}
            self.assertEqual(replay(payload,'r').error.code,'UNSUPPORTED_VERSION')
        for section in ('header','final_context'):
            payload=deepcopy(base)
            context=payload['header']['initial_context'] if section=='header' else payload[section]
            context['model_version']='old-model'
            self.assertEqual(replay(payload,'r').error.code,'UNSUPPORTED_VERSION')
        for value in (None,True,{},(base,),lambda:base):
            self.assertEqual(replay(value,'r').error.code,'INVALID_ARGUMENT')
        self.assertEqual(replay({**base,'seed':123},'r').error.code,'INVALID_ARGUMENT')
        self.assertEqual(replay(base,'bad run').error.code,'INVALID_ARGUMENT')

    def test_incomplete_out_of_order_wrong_run_and_forged_metadata_reject(self):
        export,replay=api()
        base=recording('flood')
        mutations=[]
        value=deepcopy(base); value['records'].pop(); mutations.append(value)
        value=deepcopy(base); value['records'][0],value['records'][1]=value['records'][1],value['records'][0]; mutations.append(value)
        for key,altered in (('record_index',True),('transition_id','wrong'),('cause','REPORTED'),
                            ('sequence',True),('effect_id','wrong'),('after_digest','z'*64)):
            value=deepcopy(base); value['records'][0][key]=altered; mutations.append(value)
        value=deepcopy(base); value['records'][1]['context']['tick']=True; mutations.append(value)
        value=deepcopy(base); value['records'][0]['input']['run_id']='old'; mutations.append(value)
        value=deepcopy(base); value['records'][1]['input']['duration_ms']=500; mutations.append(value)
        value=deepcopy(base); value['records'][0]['before_digest']='0'*64; mutations.append(value)
        value=deepcopy(base); value['final_digest']='0'*64; mutations.append(value)
        value=deepcopy(base); value['header']['catalog_digest']='0'*64; mutations.append(value)
        value=deepcopy(base); value['header']['initial_config']['initial_physical']['water']['rain_mm_h']=0; mutations.append(value)
        value=deepcopy(base); value['records'][0]['context']['reported_air']=0; mutations.append(value)
        for number,payload in enumerate(mutations):
            with self.subTest(number=number):
                result=replay(payload,f'failed-{number}')
                self.assertFalse(result.ok,result)
                self.assertFalse(hasattr(result,'value'))
                self.assertEqual(result.error.safe_message,'Replay rejected')
                self.assertIsNone(result.error.context)

    def test_approved_size_tick_and_effect_limits_reject_without_eviction(self):
        export,replay=api()
        for module,limit in (('city_core.lifecycle','MAX_RECORDS'),('city_core.lifecycle','MAX_TICKS'),
                             ('city_core.effects','MAX_EFFECTS')):
            with self.subTest(limit=limit),patch(f'{module}.{limit}',1):
                self.assertEqual(replay(recording('flood'),'limited').error.code,'MODEL_LIMIT')
        payload=recording()
        payload['final_context']['tick']=100001
        self.assertEqual(replay(payload,'horizon').error.code,'MODEL_LIMIT')

    def test_invalid_nested_inputs_are_errors_and_never_touch_active_source(self):
        export,replay=api()
        source=original(); before=export(source.lifecycle,'r-source').value
        for fields in ({'tag':'valve_position','target_id':'V-12','opening_fraction':float('nan')},
                       {'tag':'sensor_value','target_id':'S-WATER-01','value':0},
                       {'tag':'valve_position','target_id':'V-12','opening_fraction':True}):
            payload=recording(); payload['records'][0]['input']['effect']=fields
            self.assertFalse(replay(payload,'bad').ok)
            self.assertEqual(export(source.lifecycle,'r-source').value,before)
        payload=recording(); payload['header']['initial_config']['parameters']['extra']=payload
        self.assertEqual(replay(payload,'cyclic').error.code,'INVALID_ARGUMENT')
