"""Trace the live extension separately; preserve the original core check runner."""
import argparse
import ast
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
        str(PACKAGE / 'live_tests'), str(PACKAGE / 'tools'), str(PACKAGE / 'src/city_core')])

    def exercise():
        suite = unittest.defaultTestLoader.discover(str(PACKAGE / 'live_tests'))
        return unittest.TextTestRunner(stream=output, verbosity=2).run(suite)

    import threading
    previous_trace = threading.gettrace()
    threading.settrace(tracer.globaltrace)
    try:
        result = tracer.runfunc(exercise)
    finally:
        threading.settrace(previous_trace)
    coverage_output = io.StringIO()
    with redirect_stdout(coverage_output):
        tracer.results().write_results(show_missing=True, summary=True,
                                       coverdir=str(evidence / 'coverage'))
    summary = coverage_output.getvalue()
    rows = re.findall(r'^\s*(\d+)\s+(\d+)%\s+(\S+)\s+\(([^\n]+)\)$', summary, re.M)
    root = PACKAGE / 'src/city_live'
    measured = {Path(path).resolve(): int(percent) for _, percent, _, path in rows
                if Path(path).resolve().is_relative_to(root)}
    expected = {p.resolve() for p in root.glob('*.py') if p.name != '__init__.py'}
    body = ast.parse((root / '__init__.py').read_text(encoding='utf-8')).body
    initializer_ok = all(isinstance(node, (ast.ImportFrom, ast.Assign)) or
        (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
         and isinstance(node.value.value, str)) for node in body)
    coverage_ok = set(measured) == expected and all(percent >= 80 for percent in measured.values())
    record = {'python': sys.version.split()[0], 'tests_run': result.testsRun,
              'failures': len(result.failures), 'errors': len(result.errors), 'skipped': len(result.skipped),
              'coverage_kind': 'stdlib trace executable lines per behavior module including new worker threads; not branch coverage',
              'coverage_exclusions': '__init__.py import/export only AST guard; launch/check/demo tools',
              'module_percent': {str(p.relative_to(PACKAGE)): n for p, n in sorted(measured.items())},
              'coverage_pass': coverage_ok, 'initializer_ok': initializer_ok,
              'passed': result.wasSuccessful() and not result.skipped and coverage_ok and initializer_ok}
    (evidence / 'tests.txt').write_text(output.getvalue(), encoding='utf-8')
    (evidence / 'coverage.txt').write_text(summary, encoding='utf-8')
    (evidence / 'checks.json').write_text(json.dumps(record, indent=2), encoding='utf-8')
    print(json.dumps(record, indent=2))
    return 0 if record['passed'] else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--evidence', type=Path, required=True)
    raise SystemExit(run(parser.parse_args().evidence.resolve()))
