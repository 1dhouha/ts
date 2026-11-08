import unittest

from city_live.runtime import LiveCity
from city_live.records import LiveError


class ScenarioTests(unittest.TestCase):
    def test_rush_accident_power_water_pollution_and_physical_recovery(self):
        city = LiveCity('domains', seed=3)
        self.addCleanup(city.close)
        city.traffic('morning_rush')
        for _ in range(40):
            city.step()
        self.assertGreater(city.diagnostics()['physical_state']['roads']['R-LOW']['arrival_rate_veh_s'], .8)
        city.incident('accident', {'target': 'R-LOW'})
        before = city.diagnostics()['physical_state']['roads']['R-LOW']['queue_vehicles']
        for _ in range(10):
            city.step()
        self.assertGreater(city.diagnostics()['physical_state']['roads']['R-LOW']['queue_vehicles'], before)
        city.incident('power_outage')
        self.assertFalse(city.diagnostics()['power']['hospital_powered'])
        city.incident('heavy_rain')
        city.incident('pollution')
        for _ in range(500):
            city.step()
        flooded = city.diagnostics()
        self.assertGreater(flooded['physical_state']['water']['surface_volume_m3'], 200)
        self.assertGreater(flooded['physical_state']['air']['Z-INDUSTRIAL']['pm25_ug_m3'], 100)
        city.incident('power_restore')
        city.incident('rain_clear')
        city.incident('pollution_clear')
        city.incident('road_clear', {'target': 'R-LOW'})
        city.incident('pump_on')
        city.traffic('low')
        for _ in range(500):
            city.step()
        after = city.diagnostics()
        self.assertTrue(after['power']['hospital_powered'])
        self.assertLess(after['physical_state']['water']['surface_volume_m3'],
                        flooded['physical_state']['water']['surface_volume_m3'])
        self.assertLess(after['physical_state']['air']['Z-INDUSTRIAL']['pm25_ug_m3'],
                        flooded['physical_state']['air']['Z-INDUSTRIAL']['pm25_ug_m3'])

    def test_same_tick_order_and_preset_repeatability(self):
        city = LiveCity('order')
        self.addCleanup(city.close)
        city.schedule('off', 0, 'power_outage', run_id='order')
        city.schedule('on', 0, 'power_restore', run_id='order')
        city.step()
        self.assertTrue(city.diagnostics()['power']['hospital_powered'])
        self.assertEqual([a['action_id'] for a in city.actions()], ['off', 'on'])
        city.run_scenario('combined_crisis')
        for _ in range(100):
            city.step()
        self.assertTrue(all(a['status'] == 'COMPLETED' for a in city.actions()))

    def test_command_effect_distinction_partial_stuck_and_unavailable(self):
        city = LiveCity('actuator')
        self.addCleanup(city.close)
        result = city.command('A-VALVE-01', .8, effective=.42, command_id='valve-1')
        self.assertEqual(result['status'], 'PARTIAL')
        self.assertEqual(city.diagnostics()['physical_state']['valve']['effective_opening_fraction'], .42)
        self.assertEqual(city.command('A-VALVE-01', .8, effective=.42, command_id='valve-1'), result)
        failed = city.command('A-PUMP-01', True, available=False, command_id='pump-1')
        self.assertEqual(failed['status'], 'FAILED')
        self.assertEqual(failed['error'], 'DEVICE_UNAVAILABLE')
        self.assertFalse(city.diagnostics()['physical_state']['pump']['effective_enabled'])
        with self.assertRaisesRegex(LiveError, 'UNSUPPORTED_DEVICE'):
            city.command('PRT-PMP-01', True, command_id='unsupported')

    def test_pending_budget_and_automatic_budget_stop(self):
        city = LiveCity('budget', action_limit=2, tick_budget=2)
        self.addCleanup(city.close)
        city.schedule('a', 0, 'power_outage', run_id='budget')
        city.schedule('b', 0, 'power_restore', run_id='budget')
        with self.assertRaisesRegex(LiveError, 'BUDGET_EXHAUSTED'):
            city.schedule('c', 0, 'pump_on', run_id='budget')
        city.step()
        city.step()
        with self.assertRaisesRegex(LiveError, 'BUDGET_EXHAUSTED'):
            city.step()
        self.assertEqual(city.status()['state'], 'BUDGET_EXHAUSTED')

    def test_existing_grid_switch_and_signal_priority_preserve_core_meaning(self):
        city = LiveCity('switches')
        self.addCleanup(city.close)
        result = city.command('A-SW-ESS', False, command_id='ess-off')
        self.assertEqual(result['status'], 'COMPLETED')
        self.assertFalse(city.diagnostics()['power']['hospital_powered'])
        city.command('A-SW-ESS', True, command_id='ess-on')
        self.assertTrue(city.diagnostics()['power']['hospital_powered'])
        city.command('A-SIG-01', 'priority', command_id='priority')
        self.assertEqual(city.diagnostics()['traffic']['R-LOW']['capacity_veh_s'], .75)
