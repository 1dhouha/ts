# Integration · how the three layers connect

> **Status: T5 spoof rejection and the SCN-05 trusted traffic dispatch were verified in isolated three-service Compose runs.** SCN-03/04/06 did not complete their described autonomous cross-layer workflows; details are in `twin/docs/KNOWN_LIMITATIONS.md`.

## Data flow
| Step | From → To | Protocol | Message |
|---|---|---|---|
| 1 | Twin → Brain | REST `GET /api/v1/sensors` (or MCP `get_sensor_readings`) | public reading `twin-reading-0.2-local` |
| 2 | Twin → Guardian | Guardian polls the Twin itself (`GUARDIAN_PROFILE=twin`) | same readings |
| 3 | Guardian → Brain | REST `GET /verdicts/{run_id[-12:]-sensor_id-seq}` | `TRUSTED` / `UNTRUSTED` + reason codes |
| 4 | Brain → Twin | REST `POST /api/v1/commands` (or MCP `send_actuator_command`), Bearer + HMAC signature | command with idempotent `request_id` and verdict `context` |
| 5 | Brain → Guardian | `POST /timeline/events` | Brain decisions on the shared timeline |

Shared geography: the Brain uses the Twin's catalog of 16 sensors and the Twin's map. All sensor coordinates were checked to be **identical** to the live Twin dashboard.

## Ports
| Service | Port |
|---|---|
| City Brain (and its simulators at `/sim/*`) | 8300 |
| Twin dashboard and REST/MCP API | 8000 (`/` dashboard, `/api/v1/*` REST, `/mcp/` MCP) |
| Guardian | 8002 |

## Switching the Brain to the real partners (config only)
```env
SIMULATOR=false
MOCK_MODE=false
TWIN_URL=http://<twin-host>:8000        TWIN_TRANSPORT=mcp   TWIN_MCP_URL=http://<twin-host>:8000/sse
TWIN_TOKEN_BRAIN=<Twin's brain-role token>
GUARDIAN_URL=http://<guardian-host>:8002   GUARDIAN_MODE=lookup
GUARDIAN_TOKEN_BRAIN=<Guardian read token>  GUARDIAN_TIMELINE_TOKEN=<Guardian timeline token>
```
Details and the Twin prerequisite (its sensor-physics fix) are in `brain/INTEGRATION.md`.

The local jury Compose stack starts with demo credentials and binds its ports to loopback.
For the authenticated Twin dashboard, use passphrase `jury-city-dashboard-2026`. For any
deployment beyond this local demo, override the credentials with fresh values in a private,
uncommitted `.env` file. The Twin serves its dashboard on the same LiveCity owner as REST
and MCP; its scenario control starts scenarios in that runtime.

## Merge-day checklist
1. `GET /api/contract` on the Brain shows the partner URLs and `mock_mode: false`.
2. The Brain header shows Twin and Guardian connected, with the Twin's run id.
3. Rerun T5 and SCN-02 … SCN-06 from the Twin side and compare each one with "Expected: Brain" in `TESTBOOK.md`.
4. Wrong-token drill: a wrong `TWIN_TOKEN_BRAIN` must give `NOT_AUTHORIZED`.
5. Signature drill: the Twin team decides whether to verify `X-CityBrain-*` signatures (the Brain already sends them).

T5 and the SCN-05 traffic command have been exercised end to end in isolated Compose stacks. The SCN-05 air fixture is not a registered sensor. Other scenarios still need separate checks against `TESTBOOK.md`.
