# Connecting Guardian to the Twin team's layer

Their repo: `dhouhaichaouii/CS-TSYP-TWIN-LAYER` (contract `twin-interface-0.1`). Their Twin only **answers requests**, so Guardian
polls it and acts through its two tools. Everything below is already built and tested against a stand-in that copies their
REST shape (`mocks/twin_rest_sim.py`); it has **not** yet been run against their live Twin (see "Still to confirm").

## 1. Connect, step by step (Windows)
1. Ask the Twin owner for: their **guardian token** (`TWIN_TOKEN_GUARDIAN`, sent privately, never in a group chat), the Twin's
   **address** (for example `http://192.168.1.20:8000`), and how they start the live simulation (see section 4).
2. In the project folder, edit `.env` (copy `.env.example` first):
   ```
   GUARDIAN_TOKEN=<our own long random token>
   GUARDIAN_DEMO=
   GUARDIAN_PROFILE=twin
   TWIN_TOKEN_GUARDIAN=<their guardian token>
   TWIN_URL=http://<their-ip>:8000
   ```
   If the Twin runs on the **same computer** as Docker Desktop, use `TWIN_URL=http://host.docker.internal:8000`
   (inside the container, `localhost` is the container itself).
3. Check their Twin first, from PowerShell (the token goes in a header, they refuse it in a URL):
   ```
   curl.exe http://<their-ip>:8000/health/ready
   curl.exe -H "Authorization: Bearer <their guardian token>" http://<their-ip>:8000/api/v1/sensors
   ```
   The first must say `READY`. The second must return `"ok":true` with readings (an empty list means their simulation is not started).
4. `docker compose up --build`, then open `http://localhost:8002/timeline?token=<GUARDIAN_TOKEN>` and
   `curl.exe http://localhost:8002/health` : look at `twin.last_ok_s_ago` (a few seconds) and `twin.errors_in_a_row` (0).
5. Without Docker: `set GUARDIAN_PROFILE=twin`, `set TWIN_TOKEN_GUARDIAN=...`, `set TWIN_URL=...`, `set GUARDIAN_TOKEN=...`, then `python demo.py`.
6. Keep the first joint run's `logs/guardian-live.log` as R4 evidence.

## 2. What Guardian does with their data
| Their data | What we do |
|---|---|
| `GET /api/v1/sensors` envelope `{ok, run_id, data:{readings}}` | poll every second, skip a sample already judged (same `seq`/`sim_tick`) |
| `reported_value`, `type`, `zone_id`, `sensor_id`, `unit` | mapped in `config.twin.yaml`; `m` is converted to `cm` |
| identical wall-clock `timestamp` on many readings | not trusted: stamped on arrival, ordered by `seq` |
| `backup_ids` (PRT-WLV-01/02, IND-TMP-01/02) | redundant-pair check: if they disagree, the one that moved away from its own history is the culprit |
| `status` `OFFLINE` / `available:false` | not judged (outage or already isolated), `device_unavailable` event |
| `status` `FAILED` / no value | quarantined (`SENSOR_FAILED`), the Brain uses the backup |
| a new `run_id` | their simulation restarted: numbering and trust start over (`run_reset`) |
| a verdict that needs containment | `POST /api/v1/isolate`; restore with `POST /devices/{id}/restore` here, which calls `/api/v1/restore` |

## 3. Failure drills (keep the logs, R4 asks for them)
| Drill | Do | Expect |
|---|---|---|
| Twin down | stop their Twin | Guardian stays up; `/health` shows `errors_in_a_row` rising; `poll_error` events; verdicts already issued stay valid; reading resumes when they return |
| Wrong token | put a wrong `TWIN_TOKEN_GUARDIAN` | `poll_error: the Twin refused our token (HTTP 401)`, no token in the log |
| Their restart | restart their Twin | `run_reset` event, no false alarms |
| Fake flood | their scenario `t5` (`python scenarios/run.py --scenario t5` on their side) | first fake reading UNTRUSTED, `PRT-WLV-01` isolated within about 5 s |
| Real flash flood | their `scn-02` | nothing flagged |
| Restore | `curl.exe -X POST -H "Authorization: Bearer <GUARDIAN_TOKEN>" localhost:8002/devices/PRT-WLV-01/restore` | sensor back online at the Twin, on probation here |

## 4. What is confirmed and what is still open
| Question | Answer | Source |
|---|---|---|
| Water-level unit | **cm** | confirmed by the Twin team (the `m` conversion stays as a safety net for old logs) |
| Seconds per tick | **1 tick = 1000 ms**, so `seconds_per_tick: 1.0` is right | their `docs/PLAN_AND_STEPS.md` ("One Monotonic Clock") |
| Should a `FAILED` sensor be quarantined? | **Yes**, their SCN-03 expects both the dead and the stuck sensor to be UNTRUSTED and quarantined | their `scenarios/scn_03_sensor_malfunction.json` |
| `seq` per sensor or global? | not answered; both work (we only need it to increase), a decrease without a new `run_id` looks like a replay | |
| **Does their live simulation actually run and advance?** | **OPEN, ask them today** | in my test of their `src/twin_app.py` (Python 3.13) `/health/ready` said `livecity DOWN`, `lifecycle DOWN`, and `sim_tick` stayed at 1 with an empty readings list. This may only be my environment, their README asks for Python 3.12 |

Ask them to run these two commands on their machine and send you the output:
```
curl.exe http://<their-ip>:8000/health/ready
curl.exe -H "Authorization: Bearer <guardian token>" http://<their-ip>:8000/api/v1/sensors
```
and again 10 seconds later. `READY` and a `sim_tick` that grows with a non-empty `readings` list means they are ready for Guardian.
If `sim_tick` does not move, ask how they start the simulation (their dashboard operator controls, or `python -X utf8 tools/run_twin.py --dev`, or a scenario run such as `python scenarios/run.py --scenario t5`).

## 5. Known limits of this integration
- Single sensors (for example `DTN-WLV-01`, `RES-*`) have no backup: only range, rate and frozen checks apply.
- If **both** sensors of a pair are compromised and agree, Guardian cannot tell. A physical cross-check (water level against pump flow or pressure) is not built.
- Rate limits and frozen windows in `config.twin.yaml` are conservative guesses, to be tuned on their live data.
- The Twin has no rollback tool, so `rollback_reading` is not available.
