# Guardian layer (v0.5)

Answers one question: **is this data telling the truth?** Validates every reading, cross-checks it against
peer sensors, keeps a per-device trust score and issues a TRUSTED / UNTRUSTED verdict with reason codes.

## Quick start (VS Code terminal, from the project folder)
```bash
python -m venv .venv
# activate:  Windows PowerShell: .venv\Scripts\Activate.ps1   |  Windows cmd: .venv\Scripts\activate   |  Mac/Linux: source .venv/bin/activate
pip install -r requirements.txt
python -m pytest -q                 # 107 tests should pass
python -m scenarios.run all         # runs the 14 scenarios, writes logs/
python demo.py                      # starts the Guardian + live timeline, opens http://localhost:8002/timeline
```
(On Windows, if `python` is not found, use `py` instead.)

## 2D city view (v0.6)
`python demo.py`, then open **http://localhost:8002/city**. The City Brain team's map with every Guardian sensor drawn on it:
green = trusted, amber = reading refused, red = quarantined, grey = silent, purple pulse = the City Brain acted on that sensor.
Buttons T1 to T14 run the scenarios on the map; click a sensor for its value, trust score, reason codes and a live chart.
Files: `src/guardian/city.py`, `src/guardian/static/city.html`, `src/guardian/static/city-map.svg` (copied from the City Brain repo).
A new sensor needs one line in the `POS` table at the top of `city.html` to appear on the map.

## Integration day helper
Save a partner's real sample message to a file, then: `python tools/send_sample.py default twin_sample.json`
It posts to `/ingest/default`, shows the verdict or the exact reason it was refused (missing field, bad unit, wrong timestamp).
Add a profile for that partner in `config.yaml` (`adapters.partners.<name>`) and use its name instead of `default`.

## Run
```bash
pip install -r requirements.txt
PYTHONPATH=src:. python -m pytest -q            # 107 tests
PYTHONPATH=src:. python -m scenarios.run t2     # one command per scenario; writes logs/t2-*.log (R2)
PYTHONPATH=src:. uvicorn --factory guardian.main:app --port 8002 --no-access-log
```
Scenarios (see TESTBOOK.md): `t1` calm, `t2` fake flood, `t2b` noisy spoof, `t3` frozen meter, `t4` camera dropout, `t5` fire with a compromised monitor,
`t6` replay, `t7` Brain wants a quarantined device, `t8` Guardian unreachable, `t9` real incident + attack, `t10` message flood, `t11` fake-device injection, `t12` future-timestamp poisoning, `t13` the Twin team's fake flood at the port (through their REST contract), `t14` their real flash flood (must NOT be flagged). `python -m scenarios.run all` runs them all.

## Demo in one command
```bash
docker compose up --build        # or: PYTHONPATH=src:. uvicorn --factory guardian.main:app --port 8002 --no-access-log
# open http://localhost:8002/timeline  (add ?token=... if GUARDIAN_TOKEN is set)
```
The page shows reading, verdict and Brain decision with timestamps, and has one button per scenario (R2, R3), so Guardian
can be demonstrated alone on its mocks. Partners post their own events to `POST /timeline/events` and appear on the same screen.
Docker files are written but were not run in the authoring environment: test `docker compose up` once on your machine.

## What is in place
- `src/guardian/engine.py`: checks = range, implausible rate, frozen value, replay/out-of-order, cross-sensor vs peers
  (peers = same sensor_type + zone, learned from traffic, so no registry is required).
- Trust score with decay, slow recovery, quarantine with hysteresis (below 0.3 quarantine, back at 0.8).
- Replays are rejected **without** lowering the device's trust: an attacker cannot degrade a healthy sensor by replaying it.
- `src/guardian/api.py`: `POST /observations`, `GET /verdicts/{observation_id}`, `GET /devices/{id}/trust`, `GET /health`,
  bearer token from env, clear JSON errors.
- `mocks/mock_twin.py`: deterministic fault-injecting mock Twin (R1). `containment.py`: Twin-tool stand-in.

## Adapters (interoperability layer)
- **Format adapter** (`adapters/format.py`): any partner payload (JSON, XML, JSON string holding XML) -> one canonical Observation.
  Format detection, per-partner field mapping and unit conversion from `config.yaml` (`adapters.partners.<name>`), timestamps as ISO or epoch.
  XML is parsed with `defusedxml` (entity/DTD attacks refused). A payload that cannot be normalized is **UNTRUSTED by default**
  (`MALFORMED_PAYLOAD` + the specific reason), never dropped or guessed, and the named device's trust is left untouched.
- **Protocol adapters** (`adapters/protocol.py`): `HttpPollAdapter` (pull), `CsvReplayAdapter` (replay). HTTP push = `POST /ingest/{partner}`.
  MCP and MQTT adapters plug into the same interface (planned).
- `pipeline.py`: protocol adapter -> format adapter -> engine, in one place.
- Extra endpoints: `POST /ingest/{partner}` (raw partner body), `POST /trust/verdict` (alias of `GET /verdicts/{id}` for partners that prefer a body).
- Event log lines use `timestamp`, `layer`, `event`, `observation_id`, matching the shared timeline event shape.

## Containment and pull mode
- `containment.HttpTwinClient`: quarantine / isolate / rollback calls to the Twin over HTTP. Tool paths are configurable
  (`containment.paths`), it uses its **own token** (`GUARDIAN_CONTAINMENT_TOKEN`), never raises into the pipeline, retries once,
  queues failures and retries them (`flush_pending`). If the Twin is down the verdict still says UNTRUSTED / QUARANTINE.
- Pull mode: set `polling.url` in `config.yaml`; `pipeline.poll_loop` feeds the same core as the push routes.
- Restart note: device trust is held in memory; persistence (SQLite) is not built yet, so a restart resets trust.

## Measured on the mock (simulated seconds from fault onset; zero false positives in every scenario)
| Scenario | Fault onset | First UNTRUSTED reading | Quarantine | Reason |
|---|---|---|---|---|
| T2 constant spoof | 10 s | +4 s | +7 s | FROZEN_VALUE |
| T2b noisy spoof | 10 s | +7 s | +9 s | CROSS_SENSOR_CONTRADICTION |
| T3 frozen meter | 15 s | +4 s | +7 s | FROZEN_VALUE |
| T13 Twin's T5 (fake 180 cm vs backup 40 cm) | tick 5 | **+0 s** (the first fake reading) | +5 s | BACKUP_DISAGREEMENT, IMPLAUSIBLE_RATE |
A reading that fails a check is itself UNTRUSTED at once, even before the device's score drops, so City Brain never acts on a reading Guardian has just flagged.

## Next
1. `main.py` + Dockerfile + docker-compose. 2. Real Twin client (MCP + HTTP). 3. Push verdict stream. 4. Timeline events to the shared screen.
5. More scenarios (dropout, network flooding, Guardian down). 6. Precision/recall table for the report.

## Security hardening (v0.2)
Full threat table: **SECURITY.md**. Summary:
- Auth fails closed (no token, no start; `GUARDIAN_DEMO=1` is the explicit opt-out), constant-time token check, same token on the timeline.
- Device registry (`security.registry`: open / prefix / strict), device and per-zone caps, new devices cannot vote as peers until they have history.
- Refuses timestamps from the future, rate-limits floods per device, and never lowers the trust of a device because of a forged message (replay, flood, future timestamp).
- Body size limit (413), strict id format, bounded memory (verdicts, devices, containment retry queue), `defusedxml`.
- Docker: non-root, read-only filesystem, no capabilities, health check.

## Docker (one command)
```bash
copy .env.example .env      # Windows (Mac/Linux: cp). Edit GUARDIAN_TOKEN in .env
docker compose up --build
# open http://localhost:8002/timeline?token=<your token>
```
Without a token, Guardian refuses to start. For a local demo with no auth set `GUARDIAN_DEMO=1` in `.env`.

## Connecting to the Twin team's layer (v0.3)
Their Twin only answers requests, so Guardian polls it with the `guardian` token and isolates through their tools. Full guide,
the questions to confirm with them and the failure drills: **INTEGRATION_TWIN.md**.
```bash
# .env (copy .env.example)         GUARDIAN_PROFILE=twin   TWIN_TOKEN_GUARDIAN=<their token>   TWIN_URL=http://<their-ip>:8000
docker compose up --build          # or:  GUARDIAN_PROFILE=twin python demo.py
curl localhost:8002/health         # shows twin.last_ok_s_ago and errors_in_a_row
```
Without those variables Guardian runs on our own mocks exactly as before.

## What v0.4 adds (limitations reduced)
- **Slow drift detection** (`SLOW_DRIFT`): catches tiny steps that each pass the rate check, using the peer median or the backup as reference. A real surge that moves every sensor is not flagged.
- **Trust state survives a restart** (`logs/guardian-state.json`, atomic write, owner-only, hostile file skipped).
- **Narrow tokens**: `GUARDIAN_TOKEN_BRAIN` reads verdicts only, `GUARDIAN_TOKEN_TWIN` sends readings only, `GUARDIAN_TOKEN` is admin.

## v0.5: found by running against the real Twin and Brain
- `frozen_requires_motion` (twin profile): a constant value is only `FROZEN_VALUE` when peers or backup moved; fixes false quarantines of healthy sensors on the Twin's calm, noise-free values.
- `GUARDIAN_TOKEN_TIMELINE`: lets the City Brain post to the shared timeline without the admin token.
- 120 tests; scenarios T1 to T14 unchanged.
