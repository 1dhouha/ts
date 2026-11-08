import json
import threading
import time
import unittest

from city_live.runtime import LiveCity
from city_live.records import LiveError


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.city = LiveCity('test-run', seed=42)
        self.addCleanup(self.city.close)

    def test_manual_step_order_scheduled_effect_and_run_isolation(self):
        city = self.city
        city.schedule('one', 0, 'power_outage', run_id='test-run')
        city.step()
        self.assertEqual(city.status()['sim_tick'], 1)
        self.assertFalse(city.diagnostics()['power']['hospital_powered'])
        self.assertEqual(city.actions()[0]['status'], 'COMPLETED')
        old = city.public.observations()
        city.reset('second-run')
        self.assertEqual(city.status()['sim_tick'], 0)
        self.assertEqual(city.actions(), [])
        self.assertEqual(city.public.observations(), [])
        self.assertTrue(city.diagnostics()['power']['hospital_powered'])
        with self.assertRaisesRegex(LiveError, 'OLD_RUN'):
            city.schedule('old', 0, 'power_outage', run_id='test-run')
        self.assertEqual(old, [])

    def test_pacing_pause_resume_speed_and_shutdown(self):
        city = self.city
        city.set_speed(10)
        city.start()
        deadline = time.monotonic() + 3
        while city.status()['sim_tick'] < 2 and time.monotonic() < deadline:
            time.sleep(.02)
        city.pause()
        tick = city.status()['sim_tick']
        self.assertGreaterEqual(tick, 2)
        time.sleep(.15)
        self.assertEqual(city.status()['sim_tick'], tick)
        city.step()
        self.assertEqual(city.status()['sim_tick'], tick + 1)
        city.resume()
        with self.assertRaisesRegex(LiveError, 'INVALID_STATE'):
            city.step()
        city.stop()
        self.assertEqual(city.status()['state'], 'STOPPED')
        city.close()
        self.assertEqual(city.status()['state'], 'CLOSED')

    def test_speed_does_not_change_simulation_or_observations(self):
        other = LiveCity('other', seed=42)
        self.addCleanup(other.close)
        self.city.set_speed(.5)
        other.set_speed(10)
        for city in (self.city, other):
            city.traffic('morning_rush')
            for _ in range(20):
                city.step()
        self.assertEqual(self.city.diagnostics()['physical_state'],
                         other.diagnostics()['physical_state'])
        a = [{k: v for k, v in o.items() if k != 'run_id'} for o in self.city.public.observations()]
        b = [{k: v for k, v in o.items() if k != 'run_id'} for o in other.public.observations()]
        self.assertEqual(a, b)

    def test_invalid_inputs_and_history_gap_are_explicit(self):
        for value in (True, 0, -1, float('nan'), '5', 100):
            with self.assertRaises(LiveError):
                self.city.set_speed(value)
        tiny = LiveCity('tiny', history_limit=4)
        self.addCleanup(tiny.close)
        for _ in range(10):
            tiny.step()
        with self.assertRaisesRegex(LiveError, 'HISTORY_GAP'):
            tiny.public.events_since('tiny', 0)
        with self.assertRaisesRegex(LiveError, 'OLD_RUN'):
            tiny.public.events_since('old', 0)
        self.assertLessEqual(len(tiny.public.observations()), 4)

    def test_public_handle_has_no_private_writer_and_returns_detached_data(self):
        for _ in range(5):
            self.city.step()
        handle = self.city.public
        self.assertFalse(hasattr(handle, 'diagnostics'))
        self.assertFalse(hasattr(handle, 'step'))
        original = handle.observations()
        original[0]['reported_value'] = -999
        self.assertNotEqual(handle.observations()[0]['reported_value'], -999)
        payload = json.dumps(handle.observations())
        for key in ('actual_value', 'delta', 'fault', 'seed', 'physical_state', 'config_digest'):
            self.assertNotIn(key, payload)

    def test_concurrent_operator_steps_are_serialized(self):
        errors = []
        def work():
            try:
                for _ in range(10):
                    self.city.step()
            except Exception as error:
                errors.append(error)
        threads = [threading.Thread(target=work) for _ in range(3)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(self.city.status()['sim_tick'], 30)

    def test_core_replay_uses_real_committed_transitions(self):
        from city_core.replay import replay_recording
        from city_core.transitions import plain
        self.city.incident('power_outage')
        for _ in range(4):
            self.city.step()
        replay = replay_recording(self.city.export_private_replay(), 'copy')
        self.assertTrue(replay.ok, replay)
        self.assertEqual(plain(replay.value.observations.read_private('copy').value.physical_state),
                         self.city.diagnostics()['physical_state'])

    def test_action_idempotency_cancellation_and_validation(self):
        city = self.city
        one = city.schedule('act-1', 3, 'power_outage', run_id='test-run')
        self.assertEqual(city.schedule('act-1', 3, 'power_outage', run_id='test-run'), one)
        with self.assertRaisesRegex(LiveError, 'IDENTITY_CONFLICT'):
            city.schedule('act-1', 4, 'power_outage', run_id='test-run')
        city.cancel('act-1', run_id='test-run')
        for _ in range(5):
            city.step()
        self.assertTrue(city.diagnostics()['power']['hospital_powered'])
        with self.assertRaises(LiveError):
            city.schedule('bad', 4, 'accident', {'target': 'R-NO'}, run_id='test-run')
