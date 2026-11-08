# Verification and Evidence

## Evidence snapshot

The repository contains multiple dated evidence runs. The newest campaign inspected while preparing this documentation is [`../test-evidence/scenario-campaign-20261108T074306.489418Z-f1e94dce/campaign.json`](../test-evidence/scenario-campaign-20261108T074306.489418Z-f1e94dce/campaign.json), started November 8, 2026.

| Campaign part | Recorded result |
|---|---|
| Six individual scenarios | PASS each |
| Sequential scenario campaign | PASS |
| Cross-scenario checks | PASS |
| Failure and recovery checks | **BLOCKED** |

The campaign report lists absent Brain/Guardian service processes and absent Kafka as limitations. It explicitly describes campaign calls as controlled local TwinFacade calls. See the full JSON for individual checks, event IDs, audit lines, dashboard results, and limitations.

A separate validation snapshot at [`../test-evidence/validation-20261107T175933Z/checks.json`](../test-evidence/validation-20261107T175933Z/checks.json) records 127 tests, zero failures/errors/skips, and a passing executable-line coverage gate on Python 3.12.16. It is historical evidence from November 7, 2026, not proof that the current checkout passes. Coverage is executable-line coverage, not branch coverage.

## Reproduce checks

Run from `triplet-07/twin` and save new evidence in a new, dated directory. Do not overwrite the existing evidence run.

```powershell
python tools/run_scenario_campaign.py --evidence-root test-evidence
python tools/run_live_checks.py --evidence <new-evidence-directory>
python tools/run_dashboard_checks.py --evidence <new-evidence-directory>
python tools/run_checks.py --evidence <new-evidence-directory>
```

Replace `<new-evidence-directory>` with a new path for each run; do not reuse an existing evidence directory. The scenario campaign creates a unique timestamped subdirectory under its evidence root. Keep the stdout summaries and generated JSON together with the exact source revision and environment details. These are reproduction instructions; this documentation change has not run the checks.

## Evidence discipline

- Record the commit/revision, date, Python version, commands, exit status, and generated artifact path.
- Report PASS, FAIL, BLOCKED, SKIPPED, or NOT TESTED as recorded; do not convert BLOCKED into PASS.
- Keep fixture-based results labeled as simulated.
- Separate Twin-local checks from end-to-end partner-service tests.
- Re-run verification on the final submitted code before quoting results to a jury.
