import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from city_live.cli import execute, console
from city_live.runtime import LiveCity
from city_live.records import LiveError


class ConsoleTests(unittest.TestCase):
    def setUp(self):
        self.city = LiveCity('console')
        self.addCleanup(self.city.close)

    def test_operator_flow_and_safe_export(self):
        execute(self.city, 'traffic morning_rush')
        execute(self.city, 'incident accident R-LOW')
        execute(self.city, 'step 10')
        self.assertEqual(execute(self.city, 'status')['sim_tick'], 10)
        execute(self.city, 'sensor fault PRT-WLV-01 FALSE_READING 180')
        execute(self.city, 'step 5')
        self.assertEqual(execute(self.city, 'sensor compare PRT-WLV-01')['reported_value'], 180)
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / 'safe.json'
            execute(self.city, f'export "{output.as_posix()}"')
            payload = output.read_text(encoding='utf-8')
            self.assertNotIn('actual_value', payload)
            self.assertNotIn('fault', payload)
            self.assertGreater(len(json.loads(payload)['observations']), 0)
        execute(self.city, 'reset another')
        self.assertEqual(self.city.status()['sim_tick'], 0)

    def test_invalid_input_is_safe_and_console_keeps_processing(self):
        source = io.StringIO('speed nope\nincident accident R-NO\n__import__("os")\nstatus\nquit\n')
        target = io.StringIO()
        console(self.city, source, target, interactive=False)
        rows = [json.loads(line) for line in target.getvalue().splitlines()]
        self.assertFalse(rows[0]['ok'])
        self.assertFalse(rows[1]['ok'])
        self.assertFalse(rows[2]['ok'])
        self.assertTrue(rows[3]['ok'])

    def test_launch_from_another_working_directory_and_eof_shutdown(self):
        tool = Path(__file__).resolve().parents[1] / 'tools/run_live_city.py'
        with tempfile.TemporaryDirectory(prefix='city space é ') as folder:
            result = subprocess.run([sys.executable, '-I', '-S', '-X', 'utf8', str(tool)],
                input='start\npause\nstep 5\nstatus\n', text=True, capture_output=True,
                cwd=folder, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(rows[-1]['data']['sim_tick'], 5)

    def test_command_family_validation(self):
        for line in ('speed', 'step -1', 'sensor fault PRT-WLV-01 NOISE nan',
                     'sensor status MISSING', 'schedule a nope power_outage', 'reset', 'status extra'):
            with self.subTest(line=line), self.assertRaises(LiveError):
                execute(self.city, line)
