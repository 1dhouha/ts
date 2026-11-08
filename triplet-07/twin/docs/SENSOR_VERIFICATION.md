# Sensor fix verification

Engineering completion: 29 October 2026. Supervisor authorized publication to this
repository with commit message `fixing the sensors`. No teammate/partner sign-off
or complete triplet readiness is implied.

Reviewed behavior sources are unchanged from the final local candidate with
SHA-256 manifest digest
`3688eef11946fa5946f0fb8e4f7c04665e8afd779f9a777663bf0790c4d8ae2e`.
Repository packaging also replaces one pre-existing test reference to a missing
parent-workspace document with an exact section-6 excerpt in
`fixtures/effect_receipt_example.md`. All request/receipt assertions remain intact.
A clean-checkout core run first reproduced this FileNotFoundError; the portable
fixture removes that external dependency. No core behavior is changed. This manifest refers
to the original workspace, not a Git tree hash.

Executed in the original workspace with Python 3.12.14 and bundled Node 24.22.1:

- 117 original core tests passed, module executable-line coverage 93–100%.
- 60 live tests passed, coverage 90–100%; new sensor physical model 100%.
- 36 dashboard/request checks passed, coverage 92–100%.
- Four Node checks and seven real Chrome browser runners passed, including all
  sixteen healthy numeric sources, replacement, reported-view privacy and a
  repeated 1340-tick city demonstration.
- A finite 1800-tick run produced exactly the expected sample counts for all
  sixteen sensors and stopped at its explicit budget.
- Two actual independent combined review rounds completed. The first identified
  a browser-fixture tick-budget mismatch and reported-map retired sensor markers;
  both were corrected, regression-tested and independently verified in round two.
  No unresolved verified BLOCKER/HIGH/MEDIUM finding remained.

Reproduce the Python checks from this repository folder (Python 3.12.14 matches
recorded replay fixtures):

```powershell
python -X utf8 tools/run_checks.py --evidence verification/core
python -X utf8 tools/run_live_checks.py --evidence verification/live
python -X utf8 tools/run_dashboard_checks.py --evidence verification/dashboard
```

Browser runners under `dashboard_tests/` accept the Python executable, an existing
Node module directory containing Playwright, a Chrome executable, and an evidence
folder. No installation was performed as part of this change.

Only these sixteen sensor positions are active; PDF-compatible replacement IDs
retire old IDs while keeping sixteen active positions. The selected inventory
excludes original camera, power-load, air-quality and emergency-call sensor
families, so full original whole-Twin coverage is not claimed. New linked actuator
command APIs, automatic backup selection, durable registration across process
restarts, Guardian/Brain policy and real partner integration remain separate work.
The simple physical parameters are illustrative. Core-only replay excludes the
extension; deterministic live repeat covers it. Reported mode retains historical
readings and has no sensor-map markers; operator mode uses the active registry.

Clean repository checkout verification before push: all 117 core tests, 60 live
tests and 27 dashboard tests passed after the portable contract-fixture fix.
The fixture preserves the original documented request/receipt assertions.
