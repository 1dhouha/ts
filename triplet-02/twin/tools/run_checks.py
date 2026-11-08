"""Run standard-library tests and trace coverage without installing tools."""
import argparse
import ast
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import re
import sys
import tomllib
import trace
import unittest

PACKAGE = Path(__file__).resolve().parents[1]
ROOT = PACKAGE.parent


def run(evidence):
    evidence.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(PACKAGE / "src"))
    minimum = tomllib.loads((PACKAGE / "pyproject.toml").read_text())["tool"][
        "city_core_tests"]["minimum_module_line_coverage"]
    output = io.StringIO()
    tracer = trace.Trace(count=True, trace=False, ignoredirs=[
        sys.base_prefix, str(PACKAGE / "tests"), str(PACKAGE / "dashboard_tests"),
        str(PACKAGE / "live_tests"), str(PACKAGE / "tools"),
        str(PACKAGE / "src/city_live")])

    def exercise():
        suite = unittest.defaultTestLoader.discover(str(PACKAGE / "tests"),
                                                   top_level_dir=str(PACKAGE))
        suite.addTests(unittest.TestLoader().discover(str(PACKAGE / "dashboard_tests")))
        suite.addTests(unittest.TestLoader().discover(
            str(PACKAGE / "live_tests"), pattern="test_*requests.py"))
        suite.addTests(unittest.TestLoader().discover(
            str(PACKAGE / "live_tests"), pattern="test_durable_idempotency.py"))
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
                                       coverdir=str(evidence / "coverage"))
    summary = coverage_output.getvalue()
    rows = re.findall(r"^\s*(\d+)\s+(\d+)%\s+(\S+)\s+\(([^\n]+)\)$", summary, re.M)
    measured = {
        Path(path).resolve(): int(percent)
        for _, percent, _, path in rows
        if Path(path).resolve().is_relative_to(PACKAGE / "src/city_core")
    }
    # stdlib trace intentionally omits __init__.py from coverage reports.
    # Keep this exclusion limited to the current import/export-only initializer.
    initializer = PACKAGE / "src/city_core/__init__.py"
    body = ast.parse(initializer.read_text(encoding="utf-8")).body
    initializer_ok = all(isinstance(node, ast.ImportFrom) or
                         (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
                          and isinstance(node.value.value, str)) or
                         (isinstance(node, ast.Assign) and len(node.targets) == 1
                          and isinstance(node.targets[0], ast.Name)
                          and node.targets[0].id == "__all__"
                          and isinstance(node.value, ast.List)
                          and all(isinstance(v, ast.Constant) and isinstance(v.value, str)
                                  for v in node.value.elts)) for node in body)
    expected = {p.resolve() for p in (PACKAGE / "src/city_core").glob("*.py")
                if p.name != "__init__.py"}
    coverage_ok = set(measured) == expected and all(
        p >= minimum for p in measured.values())
    record = {"python": sys.version.split()[0], "tests_run": result.testsRun,
              "failures": len(result.failures), "errors": len(result.errors),
              "skipped": len(result.skipped), "coverage_tool": "Python stdlib trace",
              "coverage_kind": "executable lines per module; not branch coverage",
              "minimum": minimum, "coverage_pass": coverage_ok,
              "initializer_import_export_only": initializer_ok,
              "coverage_scope": "src/city_core modules; the configured CityCore coverage gate",
              "coverage_exclusion": "__init__.py: stdlib trace omits it; AST permits imports/exports/docstring only",
              "module_percent": {p.relative_to(ROOT).as_posix(): n
                                 for p, n in sorted(measured.items())},
              "passed": result.wasSuccessful() and not result.skipped and coverage_ok and initializer_ok}
    (evidence / "tests.txt").write_text(output.getvalue(), encoding="utf-8")
    (evidence / "coverage.txt").write_text(summary, encoding="utf-8")
    (evidence / "checks.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    print(output.getvalue(), end="")
    print(summary, end="")
    print(json.dumps(record, indent=2))
    return 0 if record["passed"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", type=Path, required=True)
    raise SystemExit(run(parser.parse_args().evidence.resolve()))
