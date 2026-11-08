"""A-only integration preparation; these tests contain no B/C implementations."""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

from tools.check_handoff import run_preflight


class HandoffReadinessTests(unittest.TestCase):
    def test_private_packet_distinguishes_preflight_from_real_integration(self):
        packet = run_preflight()
        self.assertEqual(packet['visibility'], 'PRIVATE_A_EVALUATOR_ONLY')
        self.assertEqual(packet['scope'], 'A_CORE_PREFLIGHT_ONLY')
        self.assertEqual(packet['actual_bc_compatibility'], 'UNVERIFIED')
        self.assertEqual(packet['whole_twin_r1_r2_r3'], 'UNVERIFIED')
        self.assertEqual(packet['phase10_gate'], 'BLOCKED_REAL_BC_PREREQUISITES')
        self.assertTrue(packet['a_checks_passed'])
        self.assertEqual(set(packet['cases']), {'movement_applied', 'no_movement_submitted'})
        self.assertEqual(set(packet['checks']), {'reported_value_rejected', 'metadata_allowlist',
                         'reset_old_run', 'duplicate_conflict', 'private_replay'})

    def test_independent_water_threshold_and_metadata_oracles(self):
        packet = run_preflight()
        moved, held = (packet['cases'][key] for key in
                       ('movement_applied', 'no_movement_submitted'))
        # 199.4 + 0.2 rain - 0.2 gravity; open inlet adds 0.6 m3.
        self.assertAlmostEqual(moved['surface_m3'], 199.4)
        self.assertAlmostEqual(held['surface_m3'], 200.0)
        self.assertFalse(moved['low_road_flooded'])
        self.assertTrue(held['low_road_flooded'])
        self.assertEqual((moved['valve_opening'], held['valve_opening']), (0.0, 1.0))
        self.assertEqual((moved['tick'], held['tick']), (1, 1))
        self.assertEqual((moved['revision'], held['revision']), (2, 1))
        for case in (moved, held):
            self.assertEqual(set(case['metadata']), {'context'})
            self.assertEqual(set(case['token']), {'context', 'correlation_id', 'token_id'})
            self.assertEqual(case['metadata']['context'], case['token']['context'])
            self.assertTrue(case['hospital_powered'])
        self.assertEqual(packet['checks']['reset_old_run']['old_effect_code'], 'OLD_RUN')
        self.assertAlmostEqual(packet['checks']['reset_old_run']['reset_valve_opening'], 1 / 3)
        self.assertEqual(packet['checks']['duplicate_conflict']['conflict_code'], 'IDENTITY_CONFLICT')

    def test_cli_emits_one_explicitly_private_json_packet(self):
        tool = Path(__file__).parents[2] / 'tools/check_handoff.py'
        env = {key: value for key, value in os.environ.items() if key != 'PYTHONPATH'}
        result = subprocess.run([sys.executable, '-X', 'utf8', str(tool)], env=env,
                                cwd=tool.parent, capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, '')
        packet = json.loads(result.stdout)
        self.assertEqual(packet, run_preflight())


if __name__ == '__main__':
    unittest.main()
