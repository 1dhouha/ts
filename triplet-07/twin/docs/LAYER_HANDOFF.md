# Twin Layer connection guide for partner layers

This guide is for Brain, Guardian, Operator, and Evaluator clients connecting
to the City Twin. The Twin exposes the same simulation through a REST API and
an MCP tool server. Use whichever client transport your layer supports. Both
transports share one running simulation when they connect to the same service.

## 1. Choose the service address

Use the address supplied by the Twin owner:

| Where the Twin runs | Base URL example | MCP endpoint |
| --- | --- | --- |
| Same computer | `http://localhost:8000` | `http://localhost:8000/mcp/` |
| Team private network | `http://<twin-host>:8000` | `http://<twin-host>:8000/mcp/` |
| Render cloud service | `https://<service>.onrender.com` | `https://<service>.onrender.com/mcp/` |

For a local Docker run, the Twin owner starts the service from the repository
root with `docker compose up --build`. The owner supplies each layer with its
own role token and the base URL. For cloud setup, see
[Cloud Deployment](CLOUD_DEPLOYMENT.md); for private-network setup, see
[Partner Testing](PARTNER_TESTING.md).

First check that the service is ready. This endpoint does not need a token:

```text
GET <base-url>/health/ready
```

Proceed when it returns HTTP 200 and `"status":"READY"`. The local service
uses one simulation owner shared by REST and MCP. Connect all layers to the
same running instance; a separate process has separate city state.

## 2. Find the local token or use your assigned credential

For a quick localhost demo launched with
`python -X utf8 tools/run_twin.py --dev`, the server enables the built-in
development tokens below. Each layer uses only its own token:

| Layer | Local development token |
| --- | --- |
| Brain | `token-brain-secret-2026` |
| Guardian | `token-guardian-secret-2026` |
| Operator | `token-operator-secret-2026` |
| Evaluator | `token-evaluator-secret-2026` |

These values are in `src/twin_interface/auth.py` and work only when the owner
starts the development launcher with `--dev`. They are public test credentials:
use them only for a local demo, never for a cloud or shared deployment. The
dashboard's browser passphrase is chosen separately when the local demo starts;
it is not one of these API tokens.

For a production-style localhost run using Docker Compose or `python src/twin_app.py`,
there are no default tokens. The Twin owner creates a private environment file
outside the repository (Docker instructions use `%USERPROFILE%\twin-layer.env`)
and sets `TWIN_TOKEN_BRAIN`, `TWIN_TOKEN_GUARDIAN`, `TWIN_TOKEN_OPERATOR`, and
`TWIN_TOKEN_EVALUATOR` there. The owner privately gives each team only its
matching value. Cloud testers likewise receive their role token privately
from the Twin owner or deployment administrator; it is stored in the hosting
platform's secret settings, not in the repository.

## 3. Use the layer's own credential

Every REST request and every MCP session must send the Bearer token issued for
that layer. The token belongs in the HTTP `Authorization` header, never in a
URL, request body, or MCP tool argument.

| Layer | Token setting | Intended operations |
| --- | --- | --- |
| Brain | `TWIN_TOKEN_BRAIN` | Read sensors, city state, and events; send simulated actuator commands |
| Guardian | `TWIN_TOKEN_GUARDIAN` | Read sensors, city state, and events; isolate or restore devices |
| Operator | `TWIN_TOKEN_OPERATOR` | Authorized manual commands; isolate or restore devices |
| Evaluator | `TWIN_TOKEN_EVALUATOR` | Read sensors, city state, and events to assess outcomes |

The Twin enforces these role permissions. A missing/invalid token returns
HTTP 401; a valid token for a disallowed action returns HTTP 403. Do not copy
another layer's credential into your client.

## 4. REST API usage

Send JSON requests to the base URL with this header:

```http
Authorization: Bearer <your-role-token>
```

Common reads:

| Request | Use |
| --- | --- |
| `GET /api/v1/sensors` | Latest public sensor readings; optional `zone_id`, `sensor_id`, `sensor_type` query filters |
| `GET /api/v1/state` | City-wide infrastructure summary and active alerts; optional `zone_id` |
| `GET /api/v1/events?seq=0` | Events after the sequence cursor; pass the greatest returned sequence on the next poll |
| `GET /api/v1/timeline?seq=0` | Human-oriented timeline cards after a cursor |

Brain or Operator command example:

```http
POST /api/v1/commands
Content-Type: application/json
Authorization: Bearer <brain-or-operator-token>

{
  "device_id": "PRT-PMP-01",
  "command": "start",
  "params": {"speed_pct": 80},
  "request_id": "<new-unique-request-id>"
}
```

Use a new unique `request_id` for a new command. If a request times out, retry
the identical command with the same ID; never reuse that ID for different
command data. Check the response's `ok`, `data.status`, and
`data.effective_revision` before treating a command as applied.

Guardian or Operator can quarantine a device with
`POST /api/v1/isolate` and body `{"device_id":"<id>","reason":"<evidence>"}`.
Restore it with `POST /api/v1/restore` and body
`{"device_id":"<id>"}`. Brain commands to quarantined devices are rejected;
refresh state or readings and choose an available response.

Responses use a common envelope: `ok`, `run_id`, `data`, `error`, and
`timestamp`. On an error, use `error.code` to decide whether to correct the
request, check role authorization, or select another device. Full schemas are
available at `<base-url>/docs`.

## 5. MCP tool usage

Connect an MCP Streamable HTTP client to the MCP endpoint from the table above
and set `Authorization: Bearer <your-role-token>` as a transport header. Then
initialize the MCP session and discover tools; do not send the token as a tool
argument. The available tool descriptions include each tool's arguments,
permissions, and layer guidance.

| MCP tool | Use |
| --- | --- |
| `get_service_health` | Check MCP readiness before a workflow or after a connection error |
| `get_sensor_readings` | Read public telemetry; filter by optional `zone_id`, `sensor_id`, or `sensor_type` |
| `get_city_state` | Read the current infrastructure overview and alerts |
| `get_events_since` | Poll events using the greatest `event_seq` already processed as `seq` |
| `send_actuator_command` | Brain/Operator simulated control; supply `device_id`, `command`, `params`, and unique `request_id` |
| `isolate_device` | Guardian/Operator quarantine with a device ID and optional reason |
| `restore_device` | Guardian/Operator restore a safe device to service |

MCP calls return the same permission decisions and data envelope as REST.
Tools change only the simulation; they do not operate physical equipment.

## 6. Suggested layer workflow

1. Check `/health/ready` (or call `get_service_health` over MCP).
2. Read city state and relevant sensors before making a decision.
3. Brain submits a simulated command; Guardian isolates a device only when
   security analysis supports quarantine; Evaluator records/assesses the
   observed outcome; Operator performs authorized manual actions.
4. Check the command result, then read state/events again to confirm the
   simulation outcome. Maintain the event sequence cursor between polls.

Sensor outputs are public reported measurements. Internal ground truth,
fault-model details, and private diagnostics are not part of the partner
interface; do not expect or publish them. The simulation, event buffer, and
quarantine state live in memory and reset when the service restarts or is
replaced. On Render's free plan, an idle service may spin down before the next
request; see [Cloud Deployment](CLOUD_DEPLOYMENT.md) for deployment limits.
