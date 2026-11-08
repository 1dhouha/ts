# Integration · how the three layers connect

> **Status: all three layers are in this repo.** Each layer still runs alone by default. This page explains how to connect them.

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
| Twin dashboard / MCP | 8765 / 8000 |
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
## Guardian side (must match the Brain)
```env
GUARDIAN_PROFILE=twin
TWIN_URL=http://<twin-host>:8000
TWIN_TOKEN_GUARDIAN=<Twin's guardian-role token>
GUARDIAN_TOKEN=<admin token>
GUARDIAN_TOKEN_BRAIN=<same value as the Brain's GUARDIAN_TOKEN_BRAIN>
GUARDIAN_TOKEN_TIMELINE=<same value as the Brain's GUARDIAN_TIMELINE_TOKEN>
```
The names differ on purpose: the Brain calls its timeline token `GUARDIAN_TIMELINE_TOKEN` and the Guardian calls it `GUARDIAN_TOKEN_TIMELINE`. **The value must be identical on both sides.** All four Guardian tokens must differ from each other, or the Guardian refuses to start.

Details and the Twin prerequisite (its sensor-physics fix) are in `brain/INTEGRATION.md`.

## Merge-day checklist
1. `GET /api/contract` on the Brain shows the partner URLs and `mock_mode: false`.
2. The Brain header shows Twin and Guardian connected, with the Twin's run id.
3. Rerun T5 and SCN-02 … SCN-06 from the Twin side and compare each one with "Expected: Brain" in `TESTBOOK.md`.
4. Wrong-token drill: a wrong `TWIN_TOKEN_BRAIN` must give `NOT_AUTHORIZED`.
5. Signature drill: the Twin team decides whether to verify `X-CityBrain-*` signatures (the Brain already sends them).
