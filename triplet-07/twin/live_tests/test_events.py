import json
import unittest

from city_live import LiveCity


class EventTests(unittest.TestCase):
    def test_sampled_status_transitions_have_private_identity_and_reset_scope(self):
        city = LiveCity('event-run')
        self.addCleanup(city.close)
        for _ in range(5):
            city.step()
        self.assertEqual(city.events()['events'], [])  # Initial sample is the baseline.
        city.sensor_fault('PRT-WLV-01', 'DEAD', {})
        for _ in range(5):
            city.step()
        city.sensor_repair('PRT-WLV-01')
        for _ in range(5):
            city.step()
        events = city.events()['events']
        changes = [row for row in events if row['type'] == 'STATUS_CHANGED']
        self.assertEqual([(row['details']['from'], row['details']['to']) for row in changes],
                         [('OK', 'FAILED'), ('FAILED', 'OK')])
        self.assertEqual([row['sim_tick'] for row in changes], [10, 15])
        self.assertTrue(all(row['device_id'] == 'PRT-WLV-01' for row in changes))
        self.assertTrue(all(row['zone_id'] == 'PRT' for row in changes))
        self.assertEqual(len({row['event_id'] for row in events}), len(events))
        self.assertTrue(all(row['event_id'].startswith('event-run:ev-') for row in events))
        public = city.public.observations()
        self.assertEqual([row['seq'] for row in public], list(range(1, len(public) + 1)))
        for hidden in ('STATUS_CHANGED', 'FAULT_ACTIVATED', 'event_id', 'actual_value', 'fault'):
            self.assertNotIn(hidden, json.dumps(public))
        previous_id = events[0]['event_id']
        city.reset('event-next')
        self.assertEqual(city.events()['events'], [])
        city.sensor_fault('PRT-WLV-01', 'DEAD', {})
        self.assertNotEqual(city.events()['events'][0]['event_id'], previous_id)

    def test_status_recovery_waits_for_sample_and_initial_unavailable_is_not_transition(self):
        city = LiveCity('status-policy')
        self.addCleanup(city.close)
        city.sensor_fault('PRT-WLV-01', 'DEAD', {})
        for _ in range(5):
            city.step()
        self.assertFalse(any(e['type'] == 'STATUS_CHANGED' for e in city.events()['events']))
        city.sensor_repair('PRT-WLV-01')
        city.step()
        self.assertFalse(any(e['type'] == 'STATUS_CHANGED' for e in city.events()['events']))
        for _ in range(4):
            city.step()
        changes = [e for e in city.events()['events'] if e['type'] == 'STATUS_CHANGED']
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]['details']['to'], 'OK')
