import json
import unittest
import test_bridge

class CommandHTTPTests(unittest.TestCase):
    setUp = test_bridge.BridgeTests.setUp
    request = test_bridge.BridgeTests.request
    login = test_bridge.BridgeTests.login
    def test_command_route_requires_session_and_exact_envelope(self):
        status=self.city.status()
        body={'command_id':'http-step','run_id':status['run_id'],
              'expected_revision':status['revision'],'kind':'step','args':{}}
        self.assertEqual(self.request('POST','/api/operator/command',body)[0],401)
        cookie=self.login()
        code,_,raw=self.request('POST','/api/operator/command',body,{'Cookie':cookie})
        self.assertEqual(code,200)
        self.assertEqual(json.loads(raw)['data']['status'],'COMPLETED')
        self.assertEqual(self.city.status()['sim_tick'],1)
        self.assertEqual(self.request('POST','/api/operator/command',body,{'Cookie':cookie})[0],200)
        self.assertEqual(self.city.status()['sim_tick'],1)
        self.assertEqual(self.request('POST','/api/operator/command',{**body,'kind':'stop'}, {'Cookie':cookie})[0],409)

    def test_duplicate_json_keys_reject_before_effect(self):
        cookie=self.login()
        body='{"command_id":"duplicate-json","run_id":"bridge","expected_revision":0,"kind":"stop","kind":"step","args":{}}'
        self.assertEqual(self.request('POST','/api/operator/command',body,{'Cookie':cookie,'Content-Type':'application/json'})[0],400)
        self.assertEqual(self.city.status()['sim_tick'],0)
