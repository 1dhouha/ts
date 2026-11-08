from pathlib import Path
import unittest
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'person_a/src'))
from city_live.runtime import LiveCity
from city_live.records import LiveError

class RequestTests(unittest.TestCase):
 def setUp(self):
  self.city=LiveCity('requests',tick_budget=80);self.addCleanup(self.city.close);self.counter=0
 def send(self,kind,args=None,**overrides):
  s=self.city.status();self.counter+=1
  envelope={'command_id':f'c-{self.counter}','run_id':s['run_id'],'expected_revision':None if kind in ('start','pause','resume','stop','speed') else s['revision'],'kind':kind,'args':args or {}}
  return self.city.operator_request({**envelope,**overrides})
 def error(self,code,fn):
  with self.assertRaises(LiveError) as caught:fn()
  self.assertEqual(caught.exception.code,code)
 def test_concurrent_duplicate_step_is_single_commit(self):
  import concurrent.futures
  env={'command_id':'parallel','run_id':'requests','expected_revision':0,'kind':'step','args':{}}
  with concurrent.futures.ThreadPoolExecutor(6) as pool: results=list(pool.map(self.city.operator_request,[env]*6))
  self.assertTrue(all(r==results[0] for r in results));self.assertEqual(self.city.status()['sim_tick'],1)
 def test_failed_request_retry_and_detached_result(self):
  self.error('INVALID_ARGUMENT',lambda:self.send('speed',{'speed':float('nan')}))
  self.error('INVALID_ARGUMENT',lambda:self.send('speed',{'speed':True}))
  self.error('INVALID_STATE',lambda:self.send('pause',command_id='failed'))
  self.error('INVALID_STATE',lambda:self.send('pause',command_id='failed'))
  result=self.send('step',command_id='detached');result['state'].clear()
  env={'command_id':'detached','run_id':'requests','expected_revision':0,'kind':'step','args':{}}
  self.assertEqual(self.city.operator_request(env)['state']['sim_tick'],1)
 def test_atomic_boundary_exists(self):self.assertTrue(callable(getattr(self.city,'operator_request',None)),'missing atomic operator request boundary')
 def test_duplicate_step_and_reset_do_not_repeat(self):
  env={'command_id':'step-one','run_id':'requests','expected_revision':0,'kind':'step','args':{}}
  a=self.city.operator_request(env);self.assertEqual(self.city.status()['sim_tick'],1)
  self.assertEqual(self.city.operator_request(env),a);self.assertEqual(self.city.status()['sim_tick'],1)
  self.error('IDENTITY_CONFLICT',lambda:self.city.operator_request({**env,'kind':'reset'}))
  r=self.send('reset',{'new_run_id':'fresh'},command_id='reset-one');self.assertEqual(self.city.status()['run_id'],'fresh')
  reset={'command_id':'reset-one','run_id':'requests','expected_revision':6,'kind':'reset','args':{'new_run_id':'fresh'}}
  self.assertEqual(self.city.operator_request(reset),r)
 def test_reject_stale_run_revision_invalid_envelope(self):
  self.error('OLD_RUN',lambda:self.send('step',run_id='old'))
  self.error('STATE_CONFLICT',lambda:self.send('step',expected_revision=5))
  self.error('INVALID_ARGUMENT',lambda:self.send('step',expected_revision=None))
  self.error('INVALID_ARGUMENT',lambda:self.send('pause',expected_revision=0))
  self.error('UNSUPPORTED_OPERATION',lambda:self.send('set_queue'))
  self.error('INVALID_ARGUMENT',lambda:self.send('step',{'extra':1}))
  self.error('INVALID_ARGUMENT',lambda:self.city.operator_request({}))
  self.error('INVALID_ARGUMENT',lambda:self.send([],{}))
  for kind,args in [('preset',{'name':[], 'delay':0}),('traffic',{'name':[]}),('incident',{'operation':[], 'params':{}})]:
   self.error('INVALID_ARGUMENT',lambda:self.send(kind,args))
 def test_lifecycle_and_incidents(self):
  self.send('speed',{'speed':10});self.send('start');self.send('pause');self.send('resume');self.send('pause');self.send('stop');self.send('step')
  self.send('traffic',{'name':'morning_rush'});self.assertEqual(self.city.diagnostics()['traffic_profile'],'morning_rush')
  for kind in ('accident','partial_block','road_clear'):self.send('incident',{'operation':kind,'params':{'target':'R-LOW'}})
  for kind in ('power_outage','power_restore','heavy_rain','rain_clear','pollution','pollution_clear','pump_on','pump_off'):self.send('incident',{'operation':kind,'params':{}})
  self.assertEqual(self.send('preset',{'name':'recovery','delay':2})['status'],'PENDING')
  self.send('schedule',{'action_id':'later','due_tick':20,'operation':'power_restore','params':{}});self.send('cancel',{'action_id':'later'})
  result=self.send('device',{'device_id':'A-VALVE-01','requested':1,'effective':.3,'available':True});self.assertEqual(result['status'],'PARTIAL')
  result=self.send('device',{'device_id':'A-PUMP-01','requested':True,'available':False});self.assertEqual(result['status'],'FAILED')
 def test_running_revision_and_budget_recovery(self):
  self.send('start');self.error('INVALID_STATE',lambda:self.send('reset',{'new_run_id':'bad'}));self.send('pause')
  self.send('reset',{'new_run_id':'reset-ok'})
  for _ in range(80):self.send('step')
  self.error('BUDGET_EXHAUSTED',lambda:self.send('step'))
  self.send('reset',{'new_run_id':'recovered'});self.assertEqual(self.city.status()['state'],'READY')
if __name__=='__main__':unittest.main()
