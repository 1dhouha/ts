"""Strict, bounded private recording validation; no live state mutation."""
import re
import sys

from . import effects, lifecycle
from .catalog import CATALOG_VERSION, CONTRACT_VERSION, MODEL_VERSION
from .clock import validate_revision
from .effect_records import parse_request
from .snapshots import Context
from .transitions import PrivateTransition, ReplayHeader, StepInput
from .validation import InvalidInput, identity, shape, version

FORMAT_VERSION='pa-replay-0.1'
MODEL_PROFILE='pa-core-09-v1'
NUMERIC_PROFILE='binary64-exact-v1'
PYTHON_VERSION=sys.version.split()[0]
FIELDS=('format_version','model_profile','python_version','numeric_profile','randomness',
        'header','records','final_context','final_digest')


def check_digest(value):
    if type(value) is not str or re.fullmatch('[0-9a-f]{64}',value) is None:
        raise InvalidInput()


def context(value):
    shape(value,Context.__dataclass_fields__)
    identity(value['run_id'])
    for key,expected in (('model_version',MODEL_VERSION),('catalog_version',CATALOG_VERSION),
                         ('contract_version',CONTRACT_VERSION)):
        version(value[key],expected)
    for key in ('tick','sim_time_ms','revision'): validate_revision(value[key])
    if value['tick']>lifecycle.MAX_TICKS or value['revision']>lifecycle.MAX_RECORDS:
        raise InvalidInput('MODEL_LIMIT')
    if value['sim_time_ms']!=value['tick']*1000 or value['tick']>value['revision']:
        raise InvalidInput()
    return Context(**value)


def check_input(record,index,run_id,sequence):
    data=record['input']
    if record['cause']=='STEP':
        shape(data,StepInput.__dataclass_fields__)
        identity(data['run_id']); validate_revision(data['expected_revision'])
        if type(data['duration_ms']) is not int or data['duration_ms']!=1000:
            raise InvalidInput()
        if record['effect_id'] is not None or record['sequence'] is not None:
            raise InvalidInput()
    else:
        shape(data,('contract_version','run_id','effect_id','expected_revision','source','correlation_id','effect'))
        parsed=parse_request(data,data['source'])
        validate_revision(record['sequence'])
        if record['sequence']!=sequence or record['effect_id']!=parsed.effect_id:
            raise InvalidInput()
    if data['run_id']!=run_id:
        raise InvalidInput('OLD_RUN')
    if data['expected_revision']!=index:
        raise InvalidInput()


def validate_recording(payload):
    shape(payload,FIELDS)
    for key,expected in (('format_version',FORMAT_VERSION),('model_profile',MODEL_PROFILE),
                         ('python_version',payload.get('python_version') if payload.get('python_version') in (PYTHON_VERSION, '3.12.14') else PYTHON_VERSION),('numeric_profile',NUMERIC_PROFILE),
                         ('randomness','none')):
        version(payload[key],expected)
    header=shape(payload['header'],ReplayHeader.__dataclass_fields__)
    initial=context(header['initial_context']); final=context(payload['final_context'])
    if initial.revision!=0 or initial.tick!=0 or final.run_id!=initial.run_id:
        raise InvalidInput()
    for key in ('config_digest','catalog_digest'): check_digest(header[key])
    check_digest(payload['final_digest'])
    records=payload['records']
    if type(records) not in (list,tuple): raise InvalidInput()
    if len(records)>lifecycle.MAX_RECORDS: raise InvalidInput('MODEL_LIMIT')
    if len(records)!=final.revision: raise InvalidInput()
    tick=sequence=0
    for index,record in enumerate(records):
        shape(record,PrivateTransition.__dataclass_fields__)
        validate_revision(record['record_index'])
        if record['record_index']!=index or type(record['cause']) is not str or record['cause'] not in ('STEP','EFFECT'):
            raise InvalidInput()
        identity(record['transition_id'])
        if record['transition_id']!=f'tr-{index+1:010d}': raise InvalidInput()
        check_input(record,index,initial.run_id,sequence)
        tick+=record['cause']=='STEP'; sequence+=record['cause']=='EFFECT'
        if tick>lifecycle.MAX_TICKS or sequence>effects.MAX_EFFECTS: raise InvalidInput('MODEL_LIMIT')
        actual=context(record['context'])
        expected=Context(initial.run_id,tick,tick*1000,index+1,MODEL_VERSION,CATALOG_VERSION,CONTRACT_VERSION)
        if actual!=expected: raise InvalidInput()
        check_digest(record['before_digest']); check_digest(record['after_digest'])
    if final.tick!=tick: raise InvalidInput()
    return initial
