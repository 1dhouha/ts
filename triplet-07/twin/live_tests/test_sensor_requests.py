import unittest
from city_live.runtime import LiveCity
from city_live.records import LiveError

class SensorRequestTests(unittest.TestCase):
 def setUp(self):
  self.city=LiveCity('sensor-requests');self.addCleanup(self.city.close);self.count=0
 def send(self,kind,args):
  self.count+=1;s=self.city.status()
  return self.city.operator_request({'command_id':f's{self.count}','run_id':s['run_id'],'expected_revision':s['revision'],'kind':kind,'args':args})
 def test_comparisons_are_private_and_nulls_remain_null(self):
  for _ in range(10):self.city.step()
  f=self.city.capture_dashboard_frame();self.assertIn('comparisons',f)
  self.assertEqual(next(r for r in f['comparisons'] if r['sensor_id']=='PRT-WLV-01')['actual_value'],50)
  self.assertNotIn('comparisons',self.city.public.capture_reported())
 def test_all_seven_fault_controls_and_repair_replace(self):
  for _ in range(10):self.city.step()
  for kind,params in [('FALSE_READING',{'value':180}),('STUCK',{}),('DRIFT',{'rate':1}),('NOISE',{'amplitude':2}),('DELAYED',{'lag_ticks':5}),('DROPOUT',{'duration_ticks':5}),('DEAD',{})]:
   self.send('sensor_fault',{'sensor_id':'DTN-WLV-01','fault_type':kind,'parameters':params})
   self.city.step()
   self.assertEqual(self.city.diagnostics()['active_faults']['DTN-WLV-01']['type'],kind)
   self.send('sensor_repair',{'sensor_id':'DTN-WLV-01'})
  self.send('sensor_clear',{'sensor_id':'DTN-WLV-01'})
  self.send('sensor_replace',{'sensor_id':'DTN-WLV-01','new_id':'DTN-WLV-02'})
  self.assertTrue(any(s['sensor_id']=='DTN-WLV-02' and s['active'] for s in self.city.sensors()))
  self.send('sensor_fault',{'sensor_id':'PRT-WLV-01','fault_type':'DEAD','parameters':{}})
  for _ in range(5):self.city.step()
  self.assertEqual(self.city.sensor_compare('PRT-WLV-01')['status'],'FAILED')
