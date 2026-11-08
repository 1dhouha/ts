# Twin Layer Demo Guide

## Requirements

- Python 3.12 or newer.
- For the standalone dashboard, the dashboard launcher uses Python modules from `src`; install the project dependencies from `requirements.lock` if they are not already available.
- Use a terminal opened in `triplet-07/twin`.

## Start the dashboard

```powershell
python tools/run_dashboard.py
```

Choose an operator passphrase when prompted. The launcher prints a local URL (port 8765 by default). Open that URL in a browser and use the same passphrase for operator access. The reported view does not require the operator passphrase. Enter `quit` in the launcher terminal or press Ctrl+C to stop.

If the default port is unavailable, use another port:

```powershell
python tools/run_dashboard.py --port 8766
```

The dashboard launcher creates a local SQLite state file at `logs/twin-state.sqlite3` by default. Keep generated runtime data out of screenshots and commits unless it is intended evidence.

## Run a scenario with its own shared dashboard

The scenario entry point starts the dashboard and MCP server around the same runtime, starts the selected scenario, and prints the dashboard URL. Run it directly rather than starting a second dashboard owner:

```powershell
python scenarios/run.py --scenario scn-03
```

Use the scenario catalog to select the appropriate ID and interpret its result. Scenario identifiers include `t5`/`SCN-01` and `scn-02` through `scn-06`. The runner prompts for the operator passphrase and keeps the terminal open while the scenario runs. Type `quit` or press Ctrl+C to stop.

## Recommended presentation: SCN-03

SCN-03 is useful for showing failure-aware command handling:

1. Run `python scenarios/run.py --scenario scn-03` and open the printed dashboard URL.
2. Open the sensor and event/timeline views.
3. Show the frozen traffic reading, failed temperature sensor, and available backup reading.
4. Show the priority command being rejected while Guardian approval is pending.
5. Point out that the unrelated industrial backup reading does not authorize the traffic-signal command.

State clearly that SCN-03 uses deterministic simulated inputs and a local Twin role call. It does not run an autonomous Brain or Guardian service decision.

## Presentation tips

- Use the same scenario ID when explaining the UI and the saved evidence.
- Trace a single correlation ID from observation through the resulting event/command outcome.
- Distinguish `APPLIED`, `REJECTED`, and `PENDING` outcomes; do not present a pending marker as a Guardian approval.
- If a step fails, show the saved campaign report and explain the status rather than implying a successful live integration.
