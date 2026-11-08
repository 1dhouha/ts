# City Brain layer 

This layer sees what the city's sensors report and asks the Guardian whether each reading can be trusted. It then decides what to do and tells the Twin's actuators to do it: pumps, valves, traffic signals, power feeders and emergency units. **No actuator command is ever sent without a TRUSTED Guardian verdict**, apart from emergency calls, which an operator confirms.


## Run it alone (R1)
One program on one port. The built-in Twin and Guardian simulators run inside it at `/sim/twin` and `/sim/guardian`, so it needs no partner, database or Redis.

```bash
cd brain
python -m venv .venv && . .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python run_local.py                                 # http://127.0.0.1:8300/city
```
On Windows, double-click `SETUP_AND_RUN.cmd`. With Docker, run `docker compose up brain` from the repo root.

| Page | What it shows |
|---|---|
| `/city` | **Cortex**: the live map, six actuators that blink when they act, PM2.5 auras and scenario buttons |
| `/timeline` | **R3** timeline: reading → Guardian verdict → Brain decision → actuator action, with timestamps |
| `/docs` | OpenAPI for every endpoint |

## Scenarios (R2)
From the repo root, `python scenarios/run.py --scenario t5` runs one scenario and `--all` runs all 13. Each run writes `logs/<id>-<UTC>.log` with the timeline events, the decisions and a PASS/FAIL verdict for each expected output. The scenarios are described in `../TESTBOOK.md`.

## What is inside
| Folder | Role |
|---|---|
| `src/city_brain/ingestion/` | polls the Twin (REST or MCP) without blocking on verdict lookups |
| `src/city_brain/adapters/` | Twin reading → Brain reading, plus input validation with clear reason codes |
| `src/city_brain/detection/` | incident rules based on each sensor's `normal_limits` from the Twin catalog |
| `src/city_brain/decision/` | city model (RandomForest), air-quality model (classifier + 10-min forecast) and catalog rules |
| `src/city_brain/pipeline/` | trust gate, actuator choice, backups, recovery, offline vault re-sends |
| `src/city_brain/clients/` | Twin and Guardian contract clients: **the only code that talks to partners** |
| `src/city_brain/security/` | command shield (allowlist, ranges, rate limit) and HMAC request signing |
| `src/city_brain/offline/` | sealed vault for decisions that could not be sent |
| `src/city_brain/observability/` | R3 timeline |
| `src/city_brain/simulator/` | built-in Twin and Guardian simulators (copies of their interfaces) |
| `mocks/` | the same simulators, runnable as separate services |
| `tests/` | 74 tests (contracts, scenarios end to end, air model, vault, shield, validation, timeline) |

`python -m pytest -q` runs the suite. With `TWIN_REPO=<Twin checkout>` it adds byte-for-byte catalog checks and checks every command against the Twin's own code.
