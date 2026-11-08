# Triplet 02 · City Brain Convergence (TSYP14, Phase 1)

A city made of three layers, built by three teams:

| Layer | Folder | Role | Status in this repo |
|---|---|---|---|
| **Twin** | `twin/` | simulates the city: 16 sensors, actuators, roads, PM2.5 | **included; runs alone** (the Twin team's repo `CS-TSYP-TWIN-LAYER`) |
| **Guardian** | `guardian/` | judges every reading TRUSTED or UNTRUSTED and quarantines bad devices | **included; runs alone**, 122 tests (the Guardian team's repo `guardian_layer`) |
| **City Brain** | `brain/` | decides what to do and commands the Twin's actuators | **complete; runs alone** |

```text
 Twin ──readings──▶ Guardian ──verdict──▶ City Brain ──signed command──▶ Twin actuators
   └──────────────────── readings (polled) ──────▶┘
```
**Invariant:** no actuator command leaves the Brain without a TRUSTED Guardian verdict, apart from operator-confirmed emergency calls.

## Quick start: City Brain alone (R1)
```bash
cd brain && pip install -r requirements.txt && python run_local.py    # http://127.0.0.1:8300/city
python scenarios/run.py --all                                           # from the repo root: 13 scenarios, logs in logs/
```
On Windows, double-click `brain/SETUP_AND_RUN.cmd`, then `RUN_ALL_SCENARIOS.cmd`. With Docker: `docker compose up brain`.

## The three Phase 1 rules
| Rule | Where |
|---|---|
| **R1** Each layer runs alone on mocks, with a one-page Interface Card | `brain/INTERFACE.md`; built-in simulators at `/sim/twin` and `/sim/guardian`, also in `brain/mocks/` |
| **R2** One trigger per scenario, and every run writes a log | `scenarios/<id>.py` or `scenarios/run.py --scenario <id>` → `logs/<id>-<UTC>.log` |
| **R3** Live timeline: reading → verdict → decision → action, with timestamps | `http://127.0.0.1:8300/timeline` and `GET /api/timeline` |

## Repository
```text
triplet-02/
├── README.md  INTEGRATION.md  TESTBOOK.md  docker-compose.yml  .env.example
├── .github/   CODEOWNERS, workflows/ci.yml (lint + tests + all scenarios)
├── scenarios/ run.py + one trigger script per scenario
├── logs/      one log per scenario run (committed)
├── twin/      Twin layer
├── brain/     City Brain: README, INTERFACE.md, src/, mocks/, tests/
├── guardian/  Guardian layer: README, INTERFACE.md, src/, mocks/, tests/, scenarios/, logs/
└── docs/      report-brain.pdf, diagrams/
```

## Merging
The three layers are now in this repository. By default each one still runs alone on mocks (R1). To connect them, change configuration only (see `INTEGRATION.md`). The Guardian's own scenarios and logs are in `guardian/scenarios` and `guardian/logs`.
