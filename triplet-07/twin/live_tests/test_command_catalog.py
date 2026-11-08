"""Focused contracts for command translation and the runtime device catalog."""
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from city_live.commands import (
    build_command_effect,
    build_legacy_command_effect,
    preview_command_effect,
)
from city_live.device_catalog import DeviceCatalog, load_catalog_file
from city_live.records import LiveError


class CommandTranslationTests(unittest.TestCase):
    def assert_error(self, code, fn):
        with self.assertRaises(LiveError) as caught:
            fn()
        self.assertEqual(caught.exception.code, code)

    def test_pump_and_valve_commands_validate_values_and_shapes(self):
        self.assertEqual(build_command_effect('A-PUMP-01', 'start', {'speed_pct': 35}), {
            'tag': 'pump_speed', 'target_id': 'P-03', 'speed_fraction': .35})
        self.assertEqual(build_command_effect('PRT-PMP-01', 'STOP', {}), {
            'tag': 'pump_speed', 'target_id': 'P-03', 'speed_fraction': 0.0})
        self.assertEqual(build_command_effect('A-VALVE-01', 'set_opening', {'open_pct': 25}), {
            'tag': 'valve_position', 'target_id': 'V-12', 'opening_fraction': .25})
        invalid_cases = (
            (None, 'start', {}, 'INVALID_ARGUMENT'),
            ('A-PUMP-01', '', {}, 'INVALID_ARGUMENT'),
            ('A-PUMP-01', 'start', [], 'INVALID_ARGUMENT'),
            ('A-PUMP-01', 'start', {'speed_pct': True}, 'INVALID_ARGUMENT'),
            ('A-PUMP-01', 'start', {'speed_pct': 101}, 'INVALID_ARGUMENT'),
            ('A-PUMP-01', 'stop', {'extra': 1}, 'INVALID_ARGUMENT'),
            ('A-PUMP-01', 'restart', {}, 'UNSUPPORTED_COMMAND'),
            ('A-VALVE-01', 'open', {'open_pct': 20}, 'UNSUPPORTED_COMMAND'),
            ('A-VALVE-01', 'set_opening', {'open_pct': False}, 'INVALID_ARGUMENT'),
            ('A-VALVE-01', 'set_opening', {'open_pct': -1}, 'INVALID_ARGUMENT'),
        )
        for device, command, params, code in invalid_cases:
            with self.subTest(device=device, command=command, params=params):
                self.assert_error(code, lambda: build_command_effect(device, command, params))

    def test_signal_commands_support_explicit_modes_and_all_red_only(self):
        self.assertEqual(build_command_effect('A-SIG-01', 'set_mode', {'mode': 'PRIORITY'})['mode'], 'priority')
        self.assertEqual(build_command_effect('J-D2', 'set_priority_corridor', {})['mode'], 'priority')
        self.assertEqual(build_command_effect('J-D1', 'set_phase', {'phase': 'ALL_RED'})['mode'], 'stop')
        for phase in ('green_ns', 'green_ew', 'flashing'):
            self.assert_error('UNSUPPORTED_COMMAND',
                              lambda p=phase: build_command_effect('J-D1', 'set_phase', {'phase': p}))
        self.assert_error('INVALID_ARGUMENT', lambda: build_command_effect('J-D1', 'set_mode', {'mode': 1}))
        self.assert_error('INVALID_ARGUMENT', lambda: build_command_effect('J-D1', 'set_mode', {'mode': 'go'}))
        self.assert_error('INVALID_ARGUMENT', lambda: build_command_effect('J-D1', 'set_phase', {'phase': True}))
        self.assert_error('INVALID_ARGUMENT', lambda: build_command_effect('J-D1', 'set_priority_corridor', {'x': 1}))
        self.assert_error('UNSUPPORTED_COMMAND', lambda: build_command_effect('J-D1', 'unknown', {}))

    def test_feeder_and_responder_commands_preserve_explicit_effect_shapes(self):
        self.assertEqual(build_command_effect('A-SW-ESS', 'connect', {}), {
            'tag': 'feeder_position', 'target_id': 'F-ESS', 'closed': True})
        self.assertFalse(build_command_effect('F-IND', 'disconnect', {})['closed'])
        dispatched = build_command_effect('A-DISPATCH-01', 'dispatch', {
            'destination_node_id': 'J-D1', 'route_road_ids': ['R-LOW']})
        self.assertEqual(dispatched, {
            'tag': 'reserve_responder', 'target_id': 'AMB-01',
            'mission_ref': 'm-amb-01-01', 'destination_node_id': 'J-D1',
            'route_road_ids': ['R-LOW']})
        released = build_command_effect('AMB-01', 'release', {'mission_ref': 'm-1'})
        self.assertEqual(released, {'tag': 'release_responder', 'target_id': 'AMB-01', 'mission_ref': 'm-1'})
        self.assert_error('UNSUPPORTED_COMMAND', lambda: build_command_effect('F-ESS', 'toggle', {}))
        self.assert_error('UNSUPPORTED_DEVICE', lambda: build_command_effect('missing', 'start', {}))
        self.assert_error('UNSUPPORTED_DEVICE', lambda: build_command_effect('RES-TLT-01', 'set_mode', {'mode': 'normal'}))
        for params in (
            {'destination_node_id': 'J-D1'},
            {'destination_node_id': 'J-D1', 'route_road_ids': 'R-LOW'},
            {'destination_node_id': 'J-D1', 'route_road_ids': ['R-LOW'] * 6},
            {'destination_node_id': 'J-D1', 'route_road_ids': [None]},
            {'destination_node_id': 'J-D1', 'route_road_ids': [], 'extra': True},
        ):
            self.assert_error('INVALID_ARGUMENT', lambda p=params: build_command_effect('AMB-01', 'dispatch', p))
        self.assert_error('INVALID_ARGUMENT', lambda: build_command_effect('AMB-01', 'release', {}))
        self.assert_error('INVALID_ARGUMENT', lambda: build_command_effect('AMB-01', 'release', {'mission_ref': []}))
        self.assert_error('UNSUPPORTED_COMMAND', lambda: build_command_effect('AMB-01', 'arrive', {}))

    def test_preview_returns_stable_error_codes(self):
        effect, error = preview_command_effect('A-VALVE-01', 'set_opening', {'open_pct': 40})
        self.assertEqual(effect['opening_fraction'], .4)
        self.assertIsNone(error)
        self.assertEqual(preview_command_effect('unknown', 'start', {}), (None, 'UNSUPPORTED_DEVICE'))
        self.assertEqual(preview_command_effect('A-PUMP-01', 'start', {'speed_pct': True}),
                         (None, 'INVALID_ARGUMENT'))

    def test_legacy_aliases_translate_and_reject_invalid_requests(self):
        effect, actual = build_legacy_command_effect('A-VALVE-01', .8, .3)
        self.assertEqual((effect['opening_fraction'], actual), (.3, .3))
        effect, actual = build_legacy_command_effect('A-PUMP-01', True, False)
        self.assertEqual((effect['speed_fraction'], actual), (0.0, False))
        self.assertEqual(build_legacy_command_effect('A-SIG-02', 'normal', 'stop')[0]['mode'], 'stop')
        self.assertTrue(build_legacy_command_effect('A-SW-URBAN', False, True)[0]['closed'])
        for args in (
            ('A-PUMP-01', 1, False), ('A-SW-ESS', True, 1),
            ('A-VALVE-01', True, .5), ('A-SIG-01', 'green', 'stop'),
            ('not-a-device', True, True),
        ):
            self.assert_error('INVALID_ARGUMENT' if args[0] in ('A-PUMP-01', 'A-SW-ESS', 'A-VALVE-01')
                              else 'UNSUPPORTED_COMMAND' if args[0] == 'A-SIG-01'
                              else 'UNSUPPORTED_DEVICE',
                              lambda a=args: build_legacy_command_effect(*a))

    def test_unknown_catalog_family_fails_closed(self):
        row = SimpleNamespace(supported=True, target_id='X-1', family='unknown')
        catalog = SimpleNamespace(actuator_row=lambda _device: row)
        with patch('city_live.commands.load_catalog', return_value=catalog):
            self.assert_error('UNSUPPORTED_DEVICE', lambda: build_command_effect('X-1', 'run', {}))


class DeviceCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = Path(__file__).resolve().parents[1] / 'src' / 'city_live' / 'data' / 'device_catalog.json'
        cls.payload = json.loads(path.read_text(encoding='utf-8'))

    def assert_error(self, code, fn):
        with self.assertRaises(LiveError) as caught:
            fn()
        self.assertEqual(caught.exception.code, code)

    def test_queries_expose_known_rows_and_safe_missing_results(self):
        catalog = DeviceCatalog(self.payload)
        self.assertEqual(catalog.version, 'b-city-device-catalog-1')
        self.assertEqual(catalog.sensor('PRT-WLV-01').unit, 'cm')
        self.assertTrue(catalog.supported_sensor('PRT-WLV-01'))
        self.assertFalse(catalog.supported_sensor('unknown'))
        self.assertIsNone(catalog.sensor_row('unknown'))
        self.assertIs(catalog.actuator('A-PUMP-01'), catalog.actuator('P-03'))
        self.assertIsNone(catalog.actuator_row('unknown'))
        self.assertFalse(catalog.actuator('RES-TLT-01').supported)
        self.assert_error('SENSOR_NOT_FOUND', lambda: catalog.sensor('unknown'))
        self.assert_error('UNSUPPORTED_DEVICE', lambda: catalog.actuator('unknown'))
        with self.assertRaises(TypeError):
            catalog.zones['NEW'] = 'Z-NEW'

    def test_command_effect_covers_each_supported_effect_family(self):
        catalog = DeviceCatalog(self.payload)
        effect, actual = catalog.command_effect('A-PUMP-01', True, False)
        self.assertEqual((effect['speed_fraction'], actual), (0.0, False))
        effect, actual = catalog.command_effect('A-VALVE-01', .8, .3)
        self.assertEqual((effect['opening_fraction'], actual), (.3, .3))
        effect, actual = catalog.command_effect('A-SIG-01', 'normal', 'priority')
        self.assertEqual((effect['mode'], actual), ('priority', 'priority'))
        effect, actual = catalog.command_effect('A-SW-ESS', False, True)
        self.assertEqual((effect['closed'], actual), (True, True))
        self.assert_error('UNSUPPORTED_DEVICE', lambda: catalog.command_effect('RES-TLT-01', True, False))
        self.assert_error('INVALID_ARGUMENT', lambda: catalog.command_effect('A-PUMP-01', 1, False))
        self.assert_error('INVALID_ARGUMENT', lambda: catalog.command_effect('A-VALVE-01', 2, .5))
        self.assert_error('UNSUPPORTED_COMMAND', lambda: catalog.command_effect('A-SIG-01', 'go', 'stop'))
        self.assert_error('INVALID_ARGUMENT', lambda: catalog.command_effect('A-SW-ESS', True, 1))
        self.assert_error('UNSUPPORTED_COMMAND', lambda: catalog.command_effect('AMB-01', True, False))

    def test_bad_sensor_registry_and_duplicate_actuator_alias_are_rejected(self):
        duplicate_sensor = json.loads(json.dumps(self.payload))
        duplicate_sensor['sensors'][1]['sensor_id'] = duplicate_sensor['sensors'][0]['sensor_id']
        self.assert_error('DUPLICATE_ID', lambda: DeviceCatalog(duplicate_sensor))

        duplicate_alias = json.loads(json.dumps(self.payload))
        duplicate_alias['actuators'][0]['aliases'] = ['P-03']
        self.assert_error('INVALID_REGISTRY', lambda: DeviceCatalog(duplicate_alias))

    def test_file_loader_uses_the_supplied_catalog_path(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'catalog.json'
            path.write_text(json.dumps(self.payload), encoding='utf-8')
            catalog = load_catalog_file(path)
        self.assertEqual(catalog.actuator('A-PUMP-01').target_id, 'P-03')


if __name__ == '__main__':
    unittest.main()
