# Twin Layer Interface Card — Partner Handoff [R1]

> **Layer 1 — Digital Twin (Convergence City)** · IEEE TSYP14  
> **Audience:** Brain team, Guardian team, Operator dashboard, Jury/Evaluator  
> **Status:** Round 1 matching — 31 October 2026 · Contract version `twin-interface-0.1`

The Twin Layer is the single source of truth for the city. Partners never touch
the simulation core directly: every read or write goes through the **TwinFacade**,
which authenticates the caller, enforces the role matrix and quarantine flags,
strips ground truth, and wraps the result in one standard envelope.

---

## 1. One-command proof

```bash
# Windows
python tools/run_standalone.py

# Any platform
python tools/run_standalone.py --ticks 1150 --run-id demo-001
```

This boots the simulation, the Façade, the JSONL audit log, the Brain mock and
the Guardian mock. Expected result: Guardian isolates the drifting sensor and
Brain dispatches the linked pump.

Other useful commands:

```bash
python tools/interface_dashboard.py     # local REST dashboard (http://127.0.0.1:8080)
python tools/serve_mcp.py --dev          # FastMCP server over SSE (http://127.0.0.1:8000/sse)
python tools/test_interface_smoke.py     # auth + permissions + façade smoke test
python tools/test_mcp.py                 # MCP tool discovery and calls
python tools/test_rest_smoke.py          # REST fallback smoke test
```

When Dashboard and MCP must expose the same live sensor readings, use
`python tools/run_twin.py --dev` instead. It binds both interfaces to one
LiveCity owner. The dashboard-only and MCP-only launchers above each create
their own owner; running them separately does not share runtime sensor state.
To start an existing scenario in that same live runtime, add
`--scenario scn-02` (or `t5`, `scn-03`, `scn-04`, `scn-05`). The equivalent
entry point is `python scenarios/run.py --scenario scn-02`.

---

## 2. Authentication

Every call requires a Bearer token. Tokens are configured per role through
environment variables (production) or the explicit local-development flag:

| Role | Environment variable | Local-dev token (only with `TWIN_ALLOW_INSECURE_DEV_TOKENS=1`) |
| :--- | :--- | :--- |
| `brain` | `TWIN_TOKEN_BRAIN` | `token-brain-secret-2026` |
| `guardian` | `TWIN_TOKEN_GUARDIAN` | `token-guardian-secret-2026` |
| `operator` | `TWIN_TOKEN_OPERATOR` | `token-operator-secret-2026` |
| `evaluator` | `TWIN_TOKEN_EVALUATOR` | `token-evaluator-secret-2026` |

REST example:

```http
GET /api/v1/sensors?zone_id=PRT HTTP/1.1
Authorization: Bearer <your-token>
```

Query/body fallback is also accepted (`?token=...` or `{"token": "..."}`) for
stage failover. Never enable development tokens in production.

---

## 3. Role × tool matrix

| Tool | Brain | Guardian | Operator | Evaluator |
| :--- | :---: | :---: | :---: | :---: |
| `get_sensor_readings` | ✅ | ✅ | ✅ | ✅ |
| `get_city_state` | ✅ | ✅ | ✅ | ✅ |
| `get_events_since` | ✅ | ✅ | ✅ | ✅ |
| `send_actuator_command` | ✅ | — | ✅ | — |
| `isolate_device` | — | ✅ | ✅ | — |
| `restore_device` | — | ✅ | ✅ | — |

Unauthorized calls return `NOT_AUTHORIZED`. A command sent to a quarantined
device returns `GUARDIAN_RESTRICTED`, regardless of role.

---

## 4. Tool catalog

All tools return the same envelope:

```json
{
  "ok": true,
  "run_id": "live-001",
  "data": { },
  "error": null,
  "timestamp": "2026-10-31T12:00:00Z"
}
```

### `get_sensor_readings(token, zone_id?, sensor_id?, sensor_type?)`

Returns the **latest** public reading per sensor (no historical duplicates).

```json
{
  "sim_tick": 40,
  "count": 2,
  "readings": [{
    "sensor_id": "PRT-WLV-01",
    "zone_id": "PRT",
    "type": "water_level",
    "unit": "cm",
    "reported_value": 78.5,
    "status": "OK",
    "available": true,
    "backup_ids": ["PRT-WLV-02"],
    "linked_device_ids": ["PRT-PMP-01"]
  }]
}
```

Sensor types: `water_level`, `traffic_counter`, `air_quality`, `power_load`,
`flow`, `pressure`, `temperature`, and other catalog types. Zones: `PRT`,
`DTN`, `IND`, `RES`, `HSP`.

### `get_city_state(token, zone_id?)`

Returns macro state, active incidents, speed, quarantine count and per-zone
equipment summaries.

### `get_events_since(token, seq=0)`

Cursor-based event polling. Only events with `seq > cursor` are returned.

### `get_service_health()`

Unauthenticated, non-mutating MCP liveness/readiness report for MCP clients,
including stdio clients. HTTP MCP transports also expose `GET /health/live`
and `GET /health/ready`; these paths report only the MCP service and its
required shared facade/LiveCity runtime.

### `send_actuator_command(token, device_id, command, params, request_id, context?)`

Idempotent: replaying the same `request_id` returns the cached outcome and never
applies physics twice. Reusing a `request_id` with different parameters returns
`DUPLICATE_CONFLICT`.

| Family | Example IDs | Supported commands |
| :--- | :--- | :--- |
| Pump | `PRT-PMP-01`, `PRT-PMP-02` | `start`, `stop`, `set_speed` (`speed_pct` 0–100) |
| Valve | `V-12`, `IND-VLV-01`, `DTN-VLV-01` | `set_opening` (`open_pct` 0–100) |
| Signal | `DTN-TLT-01` | `set_phase`, `set_mode`, `set_priority_corridor` |
| Feeder | `F-ESS`, `F-URBAN`, `F-IND` | `open`, `close` |
| Responder | `AMB-01`, `POL-01` | `dispatch`, `release` |

### `isolate_device(token, device_id, reason?)`

Quarantines a device. Its readings become `OFFLINE`/`available=false`, and all
actuator commands to it are rejected with `GUARDIAN_RESTRICTED`.

### `restore_device(token, device_id)`

Lifts the quarantine. Returns `DEVICE_NOT_FOUND` if the device was not isolated.

---

## 5. REST fallback map

| Method | Path | Façade call |
| :--- | :--- | :--- |
| `GET` | `/health/live` | process liveness; HTTP 200 when this process answers |
| `GET` | `/health/ready` | REST readiness; HTTP 503 when required local runtime components are down |
| `GET` | `/health` | backwards-compatible alias for readiness |
| `GET` | `/api/v1/sensors` | `get_sensor_readings` |
| `GET` | `/api/v1/state` | `get_city_state` |
| `GET` | `/api/v1/events?seq=N` | `get_events_since` |
| `GET` | `/api/v1/timeline?seq=N` | `get_timeline` (UI cards) |
| `POST` | `/api/v1/commands` | `send_actuator_command` |
| `POST` | `/api/v1/isolate` | `isolate_device` |
| `POST` | `/api/v1/restore` | `restore_device` |

The REST router is the stage failover path. It calls the exact same Façade and
therefore has identical security and quarantine semantics.

Health routes are unauthenticated and local to the service answering them.
`UP` means liveness; readiness is `READY`, `DEGRADED`, or `DOWN`. `DEGRADED`
means the runtime can serve requests but an optional observability sink
(`EventStore` or `ScenarioLogger`) is not configured; it still returns HTTP
200. If a configured sink is unavailable, the facade is `DOWN` because command
handling may fail while recording its result. `DOWN` also means LiveCity or
city_core is not initialized, operational, or accessible, and returns HTTP
503. Dashboard and MCP readiness check their own bridge/service and shared
LiveCity runtime; they do not depend on the independently hosted REST service.
A health response describes only the service that answered it, not the whole
deployment.

The REST routes retain the standard response envelope with the report under
`data`; Dashboard and MCP HTTP health routes return the report directly. For
example:

```json
{
  "status": "DOWN",
  "service": "dashboard",
  "checks": {
    "dashboard_bridge": {"status": "UP"},
    "livecity": {"status": "DOWN"},
    "city_core": {"status": "DOWN"}
  }
}
```

---

## 6. Error codes

| Code | Meaning |
| :--- | :--- |
| `NOT_AUTHORIZED` | Missing/invalid token or role not permitted |
| `GUARDIAN_RESTRICTED` | Target device is quarantined |
| `DEVICE_NOT_FOUND` | Unknown sensor/actuator/zone |
| `INVALID_PARAM` | Malformed or out-of-range parameter |
| `DUPLICATE_CONFLICT` | `request_id` reused with different parameters |
| `STALE_RUN` | Request targets an old simulation run |
| `DEVICE_UNAVAILABLE` | Device cannot execute the command |
| `INTERNAL_ERROR` | Simulation rejected the effect |

---

## 7. Visibility guarantee

Brain and Guardian receive **only** `reported_value`, `status`, `unit`,
`available`, `backup_ids` and `linked_device_ids`. The following private fields
are removed recursively before any public response or JSONL write:
`actual_value`, `true_value`, `true_level`, `sensor_value`, `delta`, `fault`,
`noise`, `drift_rate`, `lag_ticks`, `bias`, `ground_truth`.

Only the `evaluator` role may see full ground truth. The automated proof is
`tests/security/test_leakage.py` (zero forbidden keys).

---

## 8. Observability outputs

* **Audit log [R2]:** `logs/{scenario}_{run_id}.jsonl`, one JSON object per line,
  flushed and `fsync`-ed after every write. No ground truth is ever written.
* **Live timeline [R3]:** `get_timeline` / `GET /api/v1/timeline` returns
  presentation cards (`badge`, `level`, `title`, `summary`) built from the same
  sanitized events.
* **Ring buffer:** the last 10,000 events are queryable by `seq` cursor.

---

## 9. Partner checklist

1. Set your role token (`TWIN_TOKEN_*`) or use `--dev` locally.
2. Call `GET /health`, then `get_sensor_readings` with your token.
3. Poll `get_events_since(seq)` to maintain your cursor.
4. Send commands with a **new** `request_id` per intent; retries reuse it.
5. On `GUARDIAN_RESTRICTED`, do not retry the same device — choose a backup or
   ask the Guardian to restore it.
6. Never expect `actual_value`/`delta`/`fault` in public responses.

For remote connection setup, private token handoff, and partner smoke checks,
see the [Remote Partner Testing Guide](PARTNER_TESTING.md).

---

## 10. Existing scenario orchestration

The five scenario JSON files are driven by one `LiveCity` scenario runner.
Scenario start, seeded sensor inputs, Twin readings, partner calls, and command
outcomes share a scenario correlation ID; observed sensor event IDs are included
in associated Brain decision and command-result lifecycle records. Scenario
events are routed to the existing event store, JSONL audit logger, and private
operator dashboard frame.

The runner does not generate Brain decisions or Guardian verdicts. Brain
commands and Guardian isolation/restoration must arrive through their existing
authenticated interfaces. Guardian remains veto-only because the current
contract has no request-bound positive approval response; a pending status is
not approval. An accepted actuator command is reported as executed only after
the existing TwinFacade and LiveCity command path returns its CityCore effect.
The existing command request ID remains the idempotency key.

The operator dashboard also exposes a session-authenticated, loopback-only
`POST /api/operator/scenario` route accepting exactly `{"scenario_id":"t5"}`.
This is a local dashboard control and does not change MCP tools or the partner
REST routes. SCN-05's `RES-AIR-TEST-01` remains fixture-only and is never added
to the sensor registry. In SCN-04, `PRT-PMP-02` currently shares the same
CityCore physical target as `PRT-PMP-01`, so it is not an eligible fallback
after that target is quarantined.
