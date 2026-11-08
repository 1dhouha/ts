# Triplet 07 · City Brain Convergence (TSYP14, Phase 1)

A city made of three layers, built by three teams:

| Layer | Folder | Role | Status in this repo |
|---|---|---|---|
| **Twin** | twin/ | local city simulation and interface boundary | source copied from CS-TSYP-TWIN-LAYER; partner integration not verified |
| **Guardian** | `guardian/` | judges every reading TRUSTED or UNTRUSTED and quarantines bad devices | placeholder (the Guardian team's repo `guardian_layer`) |
| **City Brain** | `brain/` | decides what to do and commands the Twin's actuators | **complete; runs alone** |

```text
 Twin ──readings──▶ Guardian ──verdict──▶ City Brain ──signed command──▶ Twin actuators
   └──────────────────── readings (polled) ──────▶┘
```
**Intended invariant, not verified end to end:** Brain actuator requests should follow Guardian policy before reaching Twin controls.

## Quick start: City Brain alone (R1)
```bash
cd brain && pip install -r requirements.txt && python run_local.py    # http://127.0.0.1:8300/city
python scenarios/run.py --all                                           # from the repo root: 13 scenarios, logs in logs/
```
On Windows, double-click `brain/SETUP_AND_RUN.cmd`, then `RUN_ALL_SCENARIOS.cmd`. With Docker: `docker compose up brain`.

## Jury demo: full project with Docker
Install and start Docker Desktop, clone this repository, then run this from `triplet-07/`:

```powershell
docker compose up --build
```

Compose builds and starts Twin, Guardian, and City Brain without a separate `.env` setup.
Open the Twin dashboard at `http://localhost:8000` (passphrase: `jury-city-dashboard-2026`),
City Brain at `http://localhost:8300`, and Guardian at `http://localhost:8002`.
The built-in demo credentials are for this local loopback-only stack; deployments should
override them with private credentials.

## The three Phase 1 rules
| Rule | Where |
|---|---|
| **R1** Each layer runs alone on mocks, with a one-page Interface Card | `brain/INTERFACE.md`; built-in simulators at `/sim/twin` and `/sim/guardian`, also in `brain/mocks/` |
| **R2** One trigger per scenario, and every run writes a log | `scenarios/<id>.py` or `scenarios/run.py --scenario <id>` → `logs/<id>-<UTC>.log` |
| **R3** Live timeline: reading → verdict → decision → action, with timestamps | `http://127.0.0.1:8300/timeline` and `GET /api/timeline` |

## Repository
```text
triplet-07/
├── README.md  INTEGRATION.md  TESTBOOK.md  docker-compose.yml  .env.example
├── .github/   CODEOWNERS, workflows/ci.yml (lint + tests + all scenarios)
├── scenarios/ run.py + one trigger script per scenario
├── logs/      one log per scenario run (committed)
├── twin/      Twin layer (to be added by the Twin team)
├── brain/     City Brain: README, INTERFACE.md, src/, mocks/, tests/
├── guardian/  Guardian layer (to be added by the Guardian team)
└── docs/      report-brain.pdf, diagrams/
```

## Merging
The layers are **not merged**. The Brain is ready: pointing it at the real Twin and Guardian only takes configuration (see `INTEGRATION.md`). We merge only when the team decides.
