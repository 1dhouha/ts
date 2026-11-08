from pathlib import Path
import os
import socket
import subprocess
import sys
import tempfile
import unittest

LAUNCHER = Path(__file__).resolve().parents[1] / 'tools/run_dashboard.py'


class LauncherTests(unittest.TestCase):
    def test_launcher_exists_and_cleans_up_on_eof(self):
        self.assertTrue(LAUNCHER.exists(), 'D02 requires a local launch entrypoint')
        with tempfile.TemporaryDirectory(prefix='dashboard-launch-state-') as state_dir:
            environment = os.environ.copy()
            environment['TWIN_STATE_DB_PATH'] = str(Path(state_dir)/'twin.sqlite3')
            result = subprocess.run(
                [sys.executable, '-X', 'utf8', str(LAUNCHER), '--port', '0'],
                input='one-time-test-passphrase!\n', text=True,
                capture_output=True, timeout=8, env=environment,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('http://127.0.0.1:', result.stdout)
        self.assertNotIn('one-time-test-passphrase!', result.stdout + result.stderr)
        self.assertIn('Owner stopped', result.stdout)

    def test_launcher_port_conflict_is_clean_and_safe(self):
        self.assertTrue(LAUNCHER.exists(), 'D02 requires a local launch entrypoint')
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            sock.listen()
            with tempfile.TemporaryDirectory(prefix='dashboard-launch-state-') as state_dir:
                environment = os.environ.copy()
                environment['TWIN_STATE_DB_PATH'] = str(Path(state_dir)/'twin.sqlite3')
                result = subprocess.run(
                    [sys.executable, str(LAUNCHER), '--port', str(sock.getsockname()[1])],
                    input='one-time-test-passphrase!\n', text=True,
                    capture_output=True, timeout=8, env=environment,
                )
        self.assertEqual(result.returncode, 2)
        self.assertIn('PORT_UNAVAILABLE', result.stderr)
        self.assertNotIn('Traceback', result.stderr)

    def test_short_secret_fails_before_start(self):
        self.assertTrue(LAUNCHER.exists(), 'D02 requires a local launch entrypoint')
        result = subprocess.run([sys.executable, str(LAUNCHER), '--port', '0'], input='short\n',
                                text=True, capture_output=True, timeout=8)
        self.assertEqual(result.returncode, 2)
        self.assertNotIn('http://', result.stdout)
