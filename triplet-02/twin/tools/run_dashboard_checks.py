"""Dashboard HTTP/unit/launcher/request checks and executable-line coverage."""
import argparse
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import re
import sys
import trace
import unittest

PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE / 'src'))


def run(evidence):
    evidence.mkdir(parents=True, exist_ok=True)
    output = io.StringIO()
    tracer = trace.Trace(count=True, trace=False, ignoredirs=[sys.base_prefix,
        str(PACKAGE / 'dashboard_tests'), str(PACKAGE / 'tools'),
        str(PACKAGE / 'src/city_core')])

    def exercise():
        suite = unittest.defaultTestLoader.discover(str(PACKAGE / 'dashboard_tests'))
        suite.addTests(unittest.TestLoader().discover(str(PACKAGE / 'live_tests'), pattern='test_*requests.py'))
        return unittest.TextTestRunner(stream=output, verbosity=2).run(suite)

    # stdlib trace does not collect request-handler worker threads. Install the
    # same local trace for new threads so HTTP coverage measures real handlers.
    import threading
    previous_trace = threading.gettrace()
    threading.settrace(tracer.globaltrace)
    try:
        result = tracer.runfunc(exercise)
    finally:
        threading.settrace(previous_trace)
    coverage_output = io.StringIO()
    with redirect_stdout(coverage_output):
        tracer.results().write_results(show_missing=True, summary=True, coverdir=str(evidence / 'coverage'))
    summary = coverage_output.getvalue()
    rows = re.findall(r'^\s*(\d+)\s+(\d+)%\s+(\S+)\s+\(([^\n]+)\)$', summary, re.M)
    root = PACKAGE / 'src/city_dashboard'
    measured = {Path(path).resolve(): int(percent) for _, percent, _, path in rows
                if Path(path).resolve().is_relative_to(root) or Path(path).name == 'operator_requests.py'}
    expected = {p.resolve() for p in root.glob('*.py') if p.name != '__init__.py'}
    expected.add((PACKAGE / 'src/city_live/operator_requests.py').resolve())
    coverage_ok = set(measured) == expected and all(percent >= 80 for percent in measured.values())
    record = {'python': sys.version.split()[0], 'tests_run': result.testsRun,
              'failures': len(result.failures), 'errors': len(result.errors), 'skipped': len(result.skipped),
              'coverage_kind': 'stdlib trace executable lines, including new HTTP threads; not branch coverage',
              'coverage_exclusions': 'docstring-only __init__.py; launch/check tools (launcher separately subprocess-tested); JS separately checked in Node/browser',
              'module_percent': {str(p.relative_to(PACKAGE)): n for p, n in sorted(measured.items())},
              'coverage_pass': coverage_ok,
              'passed': result.wasSuccessful() and not result.skipped and coverage_ok}
    (evidence / 'tests.txt').write_text(output.getvalue(), encoding='utf-8')
    (evidence / 'coverage.txt').write_text(summary, encoding='utf-8')
    (evidence / 'checks.json').write_text(json.dumps(record, indent=2), encoding='utf-8')
    print(json.dumps(record, indent=2))
    return 0 if record['passed'] else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--evidence', type=Path, required=True)
    raise SystemExit(run(parser.parse_args().evidence.resolve()))
