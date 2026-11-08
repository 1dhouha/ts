# IEEE TSYP14 City Brain Convergence Digital Twin
## Technical Architecture, Scenario Evaluation, and Resilience Report

**Document status:** Evidence-based implementation report  
**Evidence snapshot:** 2 November 2026  
**Audience:** IEEE TSYP14 jury, smart-city and Digital Twin specialists, architects, developers  
**Primary evidence:** [Final scenario campaign](../test-evidence/scenario-campaign-20261102T170547.703254Z-de4e30cd/FINAL_VERIFICATION.md), machine-readable [campaign results](../test-evidence/scenario-campaign-20261102T170547.703254Z-de4e30cd/campaign.json), and [browser validation](../test-evidence/scenario-campaign-20261102T170547.703254Z-de4e30cd/browser-validation.json).

> **Scope and evidence note.** This report describes the implementation visible in the source and the actual evidence bundle linked above. The Brain and Guardian partner services are not present in this repository. Calls exercised under their role credentials are local test calls through the Twin interface. The inter-layer failure scenario uses deterministic local failure injection, not a network outage. “Actuator execution” in this report means an effect on simulated CityCore state; it does not mean physical equipment was contacted.

## 1. Introduction

Cities must coordinate infrastructure while handling incomplete, delayed, erroneous, and potentially manipulated observations. Flood response, traffic routing, sensor-health management, and equipment isolation all depend on timely data and safe actuation. A digital representation can support that work by combining sensor ingestion, state estimation, policy enforcement, scenario testing, and operator visibility. It does not by itself establish that partner decision services or physical control systems are connected.

The IEEE TSYP14 City Brain Convergence project implements a local Digital Twin runtime for city infrastructure and a guarded interface through which authenticated clients can observe state, request simulated actuator changes, and quarantine or restore devices. It also provides scenario fixtures, a browser dashboard, event/audit evidence, and SQLite persistence for core replay and command idempotency.

The engineering problem addressed is to make the Twin’s state and command boundary understandable and testable while preserving safety under noisy inputs, duplicate requests, stale state, and process restart. The project objectives are:

1. Model city infrastructure state and domain interactions in the `city_core` package.
2. Provide a paced runtime, sensor adapters, event/scenario handling, and operator commands through `LiveCity`.
3. Offer stable semantic MCP and REST interfaces backed by a shared `TwinFacade`.
4. Apply authentication, a role/tool allowlist, projected sensor readings, command validation, isolation, and idempotency.
5. Demonstrate six deterministic scenarios and test persistence, failure handling, dashboard display, and restart behavior.

The verified contribution is a local Twin prototype with scenario and resilience evidence. A live Brain service, a live Guardian service, production inter-layer networking, a physical actuator gateway, and measured deployment performance are outside the demonstrated scope.

## 2. Overall System Architecture

The project is organized conceptually around three collaborating layers—Twin, Brain, and Guardian—but only the Twin runtime and the partner-facing authorization boundaries are implemented in this repository. The Brain and Guardian in the diagrams below are therefore **external actors at defined interfaces**, not running local services.

### Figure 1. Three-layer architecture and actual repository boundary

```mermaid
flowchart LR
    subgraph B["Brain layer — external partner (not present in repository)"]
      BA["Decision service<br/>Not verified / not connected"]
    end
    subgraph G["Guardian layer — external partner (not present in repository)"]
      GA["Trust and security service<br/>Not verified / not connected"]
    end
    subgraph T["Digital Twin layer — implemented locally"]
      MCP["FastMCP interface"]
      REST["REST interface"]
      F["TwinFacade<br/>auth, permissions, projections, safety"]
      L["LiveCity runtime"]
      C["CityCore simulation"]
      P["SQLite WAL persistence"]
      O["Events and audit logging"]
      MCP --> F
      REST --> F
      F --> L --> C
      L --> P
      F --> O
      L --> O
    end
    D["Operator dashboard<br/>authenticated local HTTP UI"] --> L
    BA -. "inbound commands only when supplied by caller;<br/>not a service connection" .-> MCP
    GA -. "isolation / restoration calls through role boundary;<br/>no partner verdict service" .-> MCP
```

### Table 1. Component responsibility

| Component | Responsibility in the implementation | Source |
|---|---|---|
| `city_core` / CityCore | Applies validated effects and produces revisioned city state and replay transitions. Domains include traffic, water, power, air, responders, access, catalog, and clock. | [`src/city_core/`](../src/city_core), [`core.py`](../src/city_core/core.py), [`effects.py`](../src/city_core/effects.py) |
| `LiveCity` | Owns a CityCore instance, simulation thread/clock, sensor and traffic adapters, scheduled actions, scenario runner, event history, operator request processing, and optional persistence. | [`runtime.py`](../src/city_live/runtime.py), [`operator.py`](../src/city_live/operator.py) |
| `SensorBank` | Holds the local sensor registry, generated/delivered readings, fault injection, and sensor-to-device relations. | [`sensors.py`](../src/city_live/sensors.py), [`sensor_physics.py`](../src/city_live/sensor_physics.py) |
| `ScenarioRunner` | Loads scenario JSON, seeds scenario-local state, processes tick-based triggers, and emits lifecycle/correlation events. | [`b_scenarios.py`](../src/city_live/b_scenarios.py), [`scenarios/`](../scenarios) |
| `TwinFacade` | Enforces caller role permissions at the application boundary; projects observations; applies scenario and isolation gates; delegates commands; records events. | [`facade.py`](../src/twin_interface/facade.py), [`permissions.py`](../src/twin_interface/permissions.py) |
| FastMCP server | Publishes semantic MCP tools and MCP health endpoints, using the facade. | [`server.py`](../src/twin_interface/mcp_server/server.py) |
| REST router | Provides HTTP health, observations, events/timeline, commands, isolation, and restoration routes using the same facade. | [`router.py`](../src/twin_interface/rest/router.py) |
| Dashboard bridge/UI | Serves the local operator and reported views; authenticated operator routes expose a frame, scenario start, and bounded operator commands. | [`server.py`](../src/city_dashboard/server.py), [`web/`](../src/city_dashboard/web) |
| Twin persistence | Stores CityCore replay transitions, durable command receipts/operations, recovery metadata, and quarantines in SQLite. | [`store.py`](../src/twin_persistence/store.py), [`migrations.py`](../src/twin_persistence/migrations.py) |
| Observability | Provides event storage, sequence/timeline handling, and JSONL scenario audit logging. Several of these stores are process-local. | [`src/twin_observability/`](../src/twin_observability) |
| Brain partner | Expected to analyze observations and send commands through the interface; no service, client, or running response was verified. | Campaign boundary in [`campaign.json`](../test-evidence/scenario-campaign-20261102T170547.703254Z-de4e30cd/campaign.json) |
| Guardian partner | Expected to review trust/safety and call isolation/restoration; no service or partner verdict was verified. | Same campaign evidence; [`b_scenarios.py`](../src/city_live/b_scenarios.py) marks some events as awaiting real inbound calls. |

### Figure 2. Twin internal architecture

```mermaid
flowchart TB
    subgraph Interfaces["Inbound interfaces"]
      M["FastMCP"]
      R["REST"]
      H["DashboardBridge"]
    end
    subgraph Boundary["Application boundary"]
      F["TwinFacade"]
      A["Token-to-role authentication"]
      PM["Tool × role permission matrix"]
      PR["Public projection"]
      Q["Scenario, quarantine, and command guards"]
      A --> PM --> F
      F --> PR
      F --> Q
    end
    subgraph Runtime["LiveCity owner"]
      SC["ScenarioRunner"]
      SB["SensorBank / SensorPhysics"]
      OP["Operator actions and schedules"]
      EVT["Bounded private event history"]
      SC --> SB
      SC --> EVT
      OP --> C["CityCore"]
      SB --> C
    end
    subgraph Core["CityCore"]
      W["Water"]
      P["Power"]
      T["Traffic"]
      AIR["Air"]
      E["Emergency responders"]
    end
    DB["SQLite persistence<br/>opt-in for direct owner; enabled by production launchers"]
    LOG["EventStore / ScenarioLogger<br/>process-local sinks"]
    M --> F
    R --> F
    H --> Runtime
    F --> Runtime
    C --> W
    C --> P
    C --> T
    C --> AIR
    C --> E
    Runtime --> DB
    F --> LOG
```

CityCore is authoritative for modeled physical state. LiveCity coordinates time and adapters around it. The dashboard renders frames assembled from this runtime and does not call the Brain or Guardian. A dashboard’s `COMPLETED` operator command is distinct from a partner-issued Brain command.

## 3. Digital Twin Layer

### 3.1 CityCore, LiveCity, and state synchronization

CityCore applies domain effects against a revisioned state and exposes lifecycle/replay operations. The LiveCity runtime wraps it with a synchronized simulation clock and a serialized owner boundary. At each simulation tick, LiveCity advances core state, updates the modeled city and sensor state, processes scheduled actions, and lets the active scenario runner evaluate triggers. The wall-paced background loop is started by `LiveCity.start()`; `pause()`, `stop()`, `step()`, `reset()`, and `close()` manage the runtime lifecycle.

The runtime includes water, power, traffic, air-quality, and emergency-response model state. This is a deterministic/local simulation model, not a real-time digital connection to municipal equipment. SensorBank readings are generated or controlled by local adapters; scenario fixtures may seed values and faults. `reported_value` is the projected measurement supplied to ordinary clients; internal sensor records can retain actual/ground-truth and fault attributes.

### 3.2 Event and data management

Private runtime events carry sequence, run, tick, timestamp, type, and entity identifiers. Scenario lifecycle envelopes additionally carry `event_id`, `correlation_id`, `scenario_id`, optional `device_id`, source, event type, and payload. The current scenario’s lifecycle uses the correlation ID returned by `ScenarioRunner.start()`. Scenario event order is checked in the campaign; event-store and runtime histories are bounded/process-local and are reset at owner lifecycle boundaries as implemented.

The facade wraps successful and unsuccessful calls in the common envelope:

```json
{
  "ok": true,
  "run_id": "run identifier",
  "data": {},
  "error": null,
  "timestamp": "UTC timestamp"
}
```

The example shows the successful response shape; errors carry a code, message, and optional details. The envelope is implemented in [`envelope.py`](../src/twin_interface/envelope.py).

### Figure 3. Event lifecycle and caller/partner boundary

```mermaid
sequenceDiagram
    autonumber
    participant S as Scenario trigger / SensorBank
    participant L as LiveCity + ScenarioRunner
    participant F as TwinFacade
    participant G as Guardian caller
    participant B as Brain caller
    participant C as CityCore
    participant A as Event/audit sinks
    S->>L: Trigger, sample or controlled fixture
    L->>A: RECEIVED / TWIN_UPDATED / pending markers
    Note over A,G: Pending means awaiting an inbound caller; it is not a partner response.
    G-->>F: Optional authenticated isolate/restore call
    F->>F: Role and safety checks
    F->>L: Apply allowed quarantine change
    B-->>F: Optional authenticated actuator request
    F->>F: Permission, scenario, quarantine, validation, revision checks
    F->>C: Apply simulated effect when accepted
    C-->>F: Receipt and resulting revision
    F->>A: COMMAND_EXECUTED / COMPLETED or rejection
    F-->>B: Standard response envelope
```

No automatic outbound request to Brain or Guardian is demonstrated. For example, a `BRAIN_PENDING` or `GUARDIAN_PENDING` marker is emitted by the Twin and describes the local event lifecycle. It is not evidence that a remote service received or answered a request. In test campaigns, role credentials are configured locally and calls are made directly to TwinFacade.

### 3.3 Scenario lifecycle and orchestration

`ScenarioRunner` reads six JSON scenario definitions and executes their triggers against the local Twin. It produces event/correlation traceability and can inject faults, seed domain state, quarantine an actuator trigger, and—for SCN-06—toggle local partner-channel availability flags. The runner is shared; it does not instantiate independent physics engines or a second set of interfaces.

### Figure 4. Scenario execution lifecycle

```mermaid
flowchart LR
    T["Scenario start"] --> R["RECEIVED"]
    R --> U["Twin trigger / sensor update"]
    U --> GP["GUARDIAN_PENDING<br/>local pending marker"]
    GP --> BP["BRAIN_PENDING<br/>local pending marker"]
    BP --> I{"Authenticated inbound<br/>caller arrives?"}
    I -->|No| W["Continue scenario window;<br/>no partner decision invented"]
    I -->|Guardian call| GV{"Isolation / restoration<br/>allowed?"}
    GV -->|Yes| GA["GUARDIAN_REVIEW or<br/>isolation result"]
    GV -->|No| GR["GUARDIAN_REJECTED"]
    I -->|Brain command| BD["BRAIN_DECIDED marker<br/>for inbound command"]
    BD --> V{"Facade and safety<br/>validation"}
    V -->|Reject| CR["COMMAND_REJECTED"]
    V -->|Accept| CP["COMMAND_PENDING"]
    CP --> CE["COMMAND_EXECUTED<br/>CityCore revision changes"]
    CE --> C["COMPLETED"]
    W --> E["SCENARIO_WINDOW_COMPLETE"]
    GA --> E
    GR --> E
    CR --> E
    C --> E
```

Event names are not a guarantee of partner completion. Some source fields describe an inbound role call from the test harness; the evidence labels the call path as local. A scenario can complete its configured time window without a Brain decision or Guardian verdict.

## 4. Brain Layer

The intended Brain responsibility is to consume projected Twin observations and city state, reason about operational objectives, and propose infrastructure commands through the existing interface. The Twin supports a `brain` role for read tools and actuator command submission.

**Implementation status:** No Brain service, model, policy engine, network client, or live Brain decision loop was found or exercised in this repository. In scenario tests, code supplies a command via the Brain-role token to `TwinFacade`; the input is a controlled test caller, not an actual Brain service response. Lifecycle events labelled `BRAIN_DECIDED` are produced when an inbound command is handled. They must not be interpreted as proof that an external Brain computed the command.

The dashboard footer also identifies the UI as a local prototype without Brain policy decisions. This aligns with browser validation and the interface behavior in [`controls.js`](../src/city_dashboard/web/controls.js).

## 5. Guardian Layer

The intended Guardian responsibility is to assess data trust, recommend or perform device isolation, preserve security restrictions, and restore devices only after appropriate review. The Twin provides a `guardian` role for the isolation and restoration interface. It also enforces quarantines inside the Twin independent of any external Guardian runtime.

**Implemented locally:** role checks for `isolate_device` and `restore_device`, device-isolation state, reading availability overrides for isolated devices, command rejection for isolated devices, and scenario safety guards. SCN-04 and tests invoke local facade isolation calls under Guardian credentials.

**Not implemented/verified:** an external Guardian service, a remote trust verdict, request-bound positive authorization, and a live service-to-service protocol. Scenario “pending” markers explicitly state that a real Guardian call is awaited and that positive approval is unsupported. In SCN-06, a Guardian role call is rejected during the controlled outage and quarantine remains effective; this validates local fail-closed behavior, not an actual remote failure.

## 6. MCP Communication Architecture

The FastMCP server in [`src/twin_interface/mcp_server/server.py`](../src/twin_interface/mcp_server/server.py) registers semantic tools and routes them into the same TwinFacade used by REST. The tool caller’s bearer token is extracted from the MCP HTTP authorization header; the credential is not an ordinary domain argument. Local MCP transport can be started by the project launcher; this does not add a partner integration.

### Table 2. MCP tools and inputs

| MCP tool | Inputs (excluding request context) | Result | Access status |
|---|---|---|---|
| `get_service_health` | none | Liveness and readiness reports | Unauthenticated service-health call |
| `get_sensor_readings` | `zone_id?`, `sensor_id?`, `sensor_type?` | Envelope containing current/latest projected sensor readings | Brain, Guardian, Operator, Evaluator |
| `get_city_state` | `zone_id?` | Envelope containing city/zone state and active alerts | Brain, Guardian, Operator, Evaluator |
| `get_events_since` | `seq=0` | Envelope with sequence cursor and new events | Brain, Guardian, Operator, Evaluator |
| `send_actuator_command` | `device_id`, `command`, `params`, `request_id`, `context?` | Simulated effect receipt or error envelope | Brain, Operator |
| `isolate_device` | `device_id`, `reason=""` | Isolation result or error envelope | Guardian, Operator |
| `restore_device` | `device_id` | Restoration result or error envelope | Guardian, Operator |

MCP decorator discovery is the authoritative tool list. The broader permission matrix in [`permissions.py`](../src/twin_interface/permissions.py) contains names for additional scenario/fault operations that are not registered by the MCP server as tools in this report’s scope. Do not treat those matrix entries as available MCP methods.

### Table 3. REST and local dashboard interfaces

| Interface | Method and path | Function | Auth/notes |
|---|---|---|---|
| REST | `GET /health/live` | REST-process liveness | No authentication; does not probe partners |
| REST | `GET /health/ready`, `GET /health` | Local REST/facade readiness | No authentication; not whole-deployment health |
| REST | `GET /api/v1/sensors` | Projected readings, optional zone/sensor/type filters | Bearer authentication |
| REST | `GET /api/v1/state` | City state, optional zone filter | Bearer authentication |
| REST | `GET /api/v1/events?seq=N` | Cursor-based event query | Bearer authentication |
| REST | `GET /api/v1/timeline?seq=N` | Timeline view | Bearer authentication |
| REST | `POST /api/v1/commands` | Simulated actuator command | Bearer authentication; request ID required |
| REST | `POST /api/v1/isolate` | Quarantine a device | Bearer authentication; permission checked by facade |
| REST | `POST /api/v1/restore` | Restore a quarantined device | Bearer authentication; permission checked by facade |
| Dashboard | `GET /api/report/frame` | Reported/public view | No operator session; restricted projection |
| Dashboard | `POST /api/session`, `POST /api/logout` | Create/revoke local operator session | Launch passphrase; HttpOnly, SameSite=Strict cookie |
| Dashboard | `GET /api/operator/frame` | Private local operator frame | Authenticated local session |
| Dashboard | `POST /api/operator/scenario` | Start an existing scenario | Authenticated local session |
| Dashboard | `POST /api/operator/command` | Bounded local operator action | Authenticated local session |

The REST routes and schemas are defined in [`router.py`](../src/twin_interface/rest/router.py); dashboard routes are defined in [`city_dashboard/server.py`](../src/city_dashboard/server.py). Health endpoints report the answering process and required local runtime components, not external service availability.

### Figure 5. MCP communication and security boundary

```mermaid
flowchart LR
    C["MCP client"] -->|HTTP MCP request + bearer header| S["FastMCP server"]
    S -->|extract bearer token| AU["resolve role"]
    AU --> PM["Permission matrix"]
    PM -->|permitted tool| F["TwinFacade"]
    F --> PR["Projection / event selection"]
    F --> SAFE["Scenario, device, quarantine, revision, command checks"]
    SAFE --> LC["LiveCity"]
    LC --> CC["CityCore"]
    CC --> RES["Standard envelope result"]
    RES --> C
    PM -. "unauthorized" .-> ERR["NOT_AUTHORIZED"]
    SAFE -. "invalid, stale, isolated, or unsafe" .-> ERR
```

### Table 4. Error handling examples

| Error | Meaning in the interface | Evidence/source |
|---|---|---|
| `NOT_AUTHORIZED` | Caller has no valid role or the role cannot invoke the tool | [`permissions.py`](../src/twin_interface/permissions.py), facade checks |
| `GUARDIAN_RESTRICTED` | Target is isolated; command must not be executed | [`facade.py`](../src/twin_interface/facade.py), `runtime.py` |
| `INVALID_STATE` | Scenario-level fail-closed block or state precondition is not met | SCN-01 and SCN-06 evidence |
| `STATE_CONFLICT` | Expected revision is stale; caller must refresh/revalidate | SCN-06 evidence, [`operator.py`](../src/city_live/operator.py) |
| `IDEMPOTENCY_CONFLICT` | Same request ID reused with a different fingerprint | [`store.py`](../src/twin_persistence/store.py), durable tests |
| `RECOVERY_REQUIRED` | A request has an uncertain/incomplete durable execution; it is not automatically replayed | persistence recovery tests |
| `PERSISTENCE_FAILURE` | Durable write did not establish a successful command result | [`runtime.py`](../src/city_live/runtime.py), persistence tests |

### Figure 6. Brain–Guardian–Twin interaction sequence (interface contract, not live service integration)

```mermaid
sequenceDiagram
    autonumber
    participant S as Sensor / Twin
    participant G as Guardian-role caller
    participant B as Brain-role caller
    participant F as TwinFacade
    participant C as CityCore
    S->>F: Projected observation / event
    F-->>G: Observable Twin state (poll/read interface)
    Note over G: External Guardian service is not present or verified.
    G->>F: isolate_device(device_id, reason)
    F->>F: Authenticate and authorize role; isolate locally
    F-->>G: ISOLATED result or error envelope
    F-->>B: Observable Twin state (poll/read interface)
    Note over B: External Brain service is not present or verified.
    B->>F: send_actuator_command(request_id, device, command)
    F->>F: Authenticate; permissions; scenario/quarantine/validation/revision
    alt valid, available and not isolated
      F->>C: Apply simulated CityCore effect
      C-->>F: committed receipt / revision
      F-->>B: APPLIED envelope
    else rejected
      F-->>B: explicit rejection envelope
    end
```

## 7. Scenario Campaign

Scenario definitions are stored in [`scenarios/scn_*.json`](../scenarios); orchestration is implemented in [`b_scenarios.py`](../src/city_live/b_scenarios.py). The latest individual and sequential execution evidence is in the campaign bundle. Here, PASS means the stated local scenario checks passed—not that external partner services participated.

### SCN-01 — Fake flood at the port

- **Objective and motivation:** Demonstrate resistance to a suspicious flood-level reading that could trigger an unsafe pump command.
- **Initial conditions and trigger:** Scenario data seeds the Port water-level fixture; at tick 5, `PRT-WLV-01` receives a false fixed reading of 180.0. The fault is scheduled to clear after the scenario window.
- **Expected behavior:** Trace the suspicious reading, retain Guardian safety restrictions, and reject unsafe actuator dispatch.
- **Observed behavior:** A Twin update and pending Brain/Guardian markers were emitted. The controlled Brain-role command was rejected with `INVALID_STATE` while the Guardian veto check was pending. CityCore revision was 70 before and after; pump stayed disabled at 0.0.
- **Verification/result:** **PASS** for local detection/trace and fail-closed rejection. No external Brain decision or Guardian verdict was received.
- **Evidence:** [SCN-01 event trace](../test-evidence/scenario-campaign-20261102T170547.703254Z-de4e30cd/individual/individual-scn-01-city-events.json), [campaign record](../test-evidence/scenario-campaign-20261102T170547.703254Z-de4e30cd/campaign.json).
- **Limitation:** Fixture and fault are local simulation inputs; service-side trust scoring is not available.

### SCN-02 — Harbor basin surge and emergency dispatch

- **Objective and motivation:** Exercise a modeled flood surge and confirm accepted commands change Twin state.
- **Initial conditions and trigger:** Scenario fixture identifies `PRT-WLV-01`, pump `PRT-PMP-01`, and a seeded rise from 40 to 110 cm at tick 1.
- **Expected behavior:** Emit a Twin observation, apply safety checks, then mutate simulated water/pump state for an allowed command.
- **Observed behavior:** Revision moved 8→9 and pump changed from disabled/0.0 to enabled/0.30. The same request was repeated and returned the same outcome without a second revision.
- **Verification/result:** **PASS** for seeded-event handling, local interface execution, state mutation, and duplicate behavior.
- **Evidence:** [SCN-02 event trace](../test-evidence/scenario-campaign-20261102T170547.703254Z-de4e30cd/individual/individual-scn-02-city-events.json), campaign record.
- **Limitation:** The scenario title’s “real” describes the scenario narrative; the campaign used explicitly seeded Twin data and a local facade caller. Neither Brain/Guardian services nor physical infrastructure were involved.

### SCN-03 — Sensor death and frozen telemetry

- **Objective and motivation:** Distinguish stale/frozen or unavailable data from a genuine emergency and use a backup only if a validated relationship exists.
- **Initial conditions and trigger:** At tick 2, the traffic sensor `DTN-TRF-01` receives a `STUCK` fault and industrial temperature sensor `IND-TMP-01` receives `DEAD`. `IND-TMP-02` is identified as a backup fixture.
- **Expected behavior:** Reject unsupported/invalid evidence and accept a linked backup reading only when it is present and validated.
- **Observed behavior:** One request using invalid/no backup was rejected with `INVALID_STATE`; the request tied to `IND-TMP-02` was accepted. Valve opening changed from about 0.333 to 0.25, revision 10→11.
- **Verification/result:** **PASS** for fault representation, rejection, validated local backup behavior, and CityCore effect.
- **Evidence:** [SCN-03 event trace](../test-evidence/scenario-campaign-20261102T170547.703254Z-de4e30cd/individual/individual-scn-03-city-events.json), campaign record.
- **Limitation:** Sensor-network transport loss is not tested. No external Brain or Guardian participated.

### SCN-04 — Guardian containment and quarantined actuator

- **Objective and motivation:** Ensure isolation prevents actuator commands and identify whether a safe alternate pump is genuinely available.
- **Initial conditions and trigger:** Scenario fixture selects primary `PRT-PMP-01`, alternate ID `PRT-PMP-02`; tick 1 emits a quarantine request for the primary.
- **Expected behavior:** Quarantined-device commands must be rejected; an alternative can only be used if it is distinct, eligible, and authorized.
- **Observed behavior:** The primary command was rejected pending the Guardian veto check. A local Guardian-role isolation call succeeded. Subsequent requests to both IDs returned `GUARDIAN_RESTRICTED`; revision stayed 7 and no pump effect occurred.
- **Verification/result:** **PASS** for safe rejection and quarantine enforcement; **NOT SUPPORTED** for fallback actuation.
- **Evidence:** [SCN-04 event trace](../test-evidence/scenario-campaign-20261102T170547.703254Z-de4e30cd/individual/individual-scn-04-city-events.json), campaign record.
- **Limitation:** `PRT-PMP-01` and `PRT-PMP-02` map to the same CityCore physical target. SCN-04 defines no sensor trigger. Isolation is a test caller action, not an external Guardian verdict.

### SCN-05 — Concurrent emergency and spoofing attack

- **Objective and motivation:** Represent competing traffic demand and suspicious air-quality reporting in a stress scenario.
- **Initial conditions and trigger:** The scenario seeds traffic sensor `DTN-TRF-01`; the air-quality fixture `RES-AIR-TEST-01` has a scenario-local false reading (18 actual, 180 reported) at tick 5. The configured actuator is `DTN-TLT-01`.
- **Expected behavior:** Preserve event/correlation traceability, reject untrusted actions where required, and apply an accepted traffic response in CityCore.
- **Observed behavior:** The local traffic response changed `J-D1` from normal to priority, revision 12→13. Cross-scenario tests also cover competing expected-revision commands and duplicate trigger delivery.
- **Verification/result:** **PASS** for the campaign’s local lifecycle and simulated state mutation.
- **Evidence:** [SCN-05 event trace](../test-evidence/scenario-campaign-20261102T170547.703254Z-de4e30cd/individual/individual-scn-05-city-events.json), campaign record.
- **Limitation:** Air fixture is scenario-local, not a production sensor. This result does not establish a real emergency partner workflow.

### SCN-06 — Inter-layer communication failure and recovery

- **Objective and motivation:** Verify fail-closed behavior when communication is marked unavailable and ensure stale commands are not applied after recovery.
- **Initial conditions and trigger:** All channels begin locally marked available. Brain is marked unavailable at tick 1; Guardian at tick 2; Brain and Guardian are marked recovered at ticks 3 and 4.
- **Expected behavior:** Reject commands during unavailability, preserve quarantine restrictions, avoid fabricating partner responses, and revalidate a fresh post-recovery command.
- **Observed behavior:** Brain command during its outage and Guardian operations during its outage returned explicit `INVALID_STATE` errors. Quarantine remained active. A delayed request with a stale revision returned `STATE_CONFLICT`. After restoration and a fresh revision check, one simulated command changed pump state, and a duplicate returned its stored result without another mutation.
- **Verification/result:** **PASS** for deterministic local failure injection, fail-closed behavior, recovery, stale-command rejection, correlation continuity, and idempotency.
- **Evidence:** [SCN-06 test campaign](../test-evidence/scn06-20261102T165453.592708Z-dde078db/campaign.json), [campaign event trace](../test-evidence/scenario-campaign-20261102T170547.703254Z-de4e30cd/individual/individual-scn-06-city-events.json).
- **Limitation:** Channel availability is an injected ScenarioRunner flag; no network connection was dropped or probed, no external service was restarted, and no pending command queue exists.

### Table 5. Scenario coverage matrix

| Capability | SCN-01 | SCN-02 | SCN-03 | SCN-04 | SCN-05 | SCN-06 |
|---|---|---|---|---|---|---|
| Individual execution | PASS | PASS | PASS | PASS | PASS | PASS |
| Twin event/state update | PASS | PASS | PASS | N/A (no sensor trigger) | PASS | N/A (communication triggers) |
| Real partner Brain response | Not verified | Not verified | Not verified | Not verified | Not verified | Not verified |
| Real partner Guardian verdict | Not verified | Not verified | Not verified | Not verified | Not verified | Not verified |
| Unsafe command rejected | PASS | Safety gate present; local action accepted | PASS for unvalidated feed | PASS | Scenario/cross-case checks | PASS during outage/stale state |
| Simulated CityCore mutation | No | PASS, pump | PASS, valve | No; correctly rejected | PASS, signal | PASS, pump after recovery |
| Physical actuator effect | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested |
| Cross-scenario / sequential evidence | Included | Included | Included | Included | Included | Included |

The independent campaign reports all six individual **local scenario checks** as PASS. The campaign’s total outcome is nevertheless `BLOCKED` for full integration because external partner service claims cannot be tested here.

## 8. Security and Safety

### Table 6. Security mechanisms and status

| Mechanism | Implementation | Evidence/status |
|---|---|---|
| Token authentication and role resolution | `resolve_token()` compares configured tokens in constant time; development tokens require explicit opt-in. | Implemented; [`auth.py`](../src/twin_interface/auth.py), regression tests |
| Tool permissions | Tool-to-role allowlist separates actuator commands from isolation/restoration. | Implemented; [`permissions.py`](../src/twin_interface/permissions.py) |
| Credential transport | MCP bearer is read from request context; REST expects Authorization bearer and rejects credential values in query/body; dashboard uses a launch passphrase/session cookie. | Implemented; MCP server, [`router.py`](../src/twin_interface/rest/router.py), dashboard tests |
| Sensor information projection | Ordinary sensor reads expose selected public fields; private evaluator-oriented ground truth is separately controlled. | Implemented; [`projections.py`](../src/twin_interface/projections.py), leakage tests |
| Command schema and semantic validation | Command/device pairing and parameters are built/validated before an effect is applied. | Implemented; [`commands.py`](../src/city_live/commands.py) and CityCore |
| Quarantine | Isolated devices are marked/rejected at the Twin boundary; quarantine rows can be persisted when SQLite is enabled. | Implemented and tested; SCN-04 and durable tests |
| Revision checks | Optional `expected_revision` prevents applying a stale decision. | Implemented and SCN-06 tested |
| Idempotency | Durable request ID uniqueness and payload fingerprint; same fingerprint returns prior result; a changed payload conflicts. | Implemented with SQLite; durable tests and restart evidence |
| Event/audit traceability | Correlation IDs and lifecycle events; JSONL scenario logger sanitization checks. | Demonstrated in local scenario evidence |
| Physical safety interlock | External device interlock or controller authorization | Not implemented / not verified; simulation-only effects |

The command-validation sequence is important: facade permission resolution occurs before command dispatch; scenario and partner-channel guards run before CityCore command handling; command semantics, expected revision, isolation, availability, and persistence are checked by the runtime. An accepted command changes CityCore and obtains a resulting revision/effect receipt.

### Figure 7. Actuator command validation and execution

```mermaid
flowchart TD
    R["Receive request_id, device, command, params, context"] --> AUTH["Resolve caller role"]
    AUTH -->|Invalid / not permitted| DENY["NOT_AUTHORIZED"]
    AUTH -->|Permitted| SC{"Scenario context and channel guards"}
    SC -->|Unavailable / stale / unsafe| REJECT["Explicit rejection"]
    SC -->|Allowed| DUP{"Durable request ID exists?"}
    DUP -->|Same fingerprint + committed| ORIGINAL["Return stored result"]
    DUP -->|Different fingerprint| CONFLICT["IDEMPOTENCY_CONFLICT"]
    DUP -->|Incomplete receipt| REC["RECOVERY_REQUIRED"]
    DUP -->|New request| VAL["Validate device, command, params,<br/>revision, isolation, availability"]
    VAL -->|Invalid| REJECT
    VAL -->|Valid| CLAIM["Persist EXECUTING request + DOING operation"]
    CLAIM --> EFFECT["Apply simulated CityCore effect"]
    EFFECT --> ATOMIC["Persist transitions + receipt + DONE operation<br/>in SQLite transaction"]
    ATOMIC --> RESULT["Return APPLIED result / revision"]
```

**Implementation detail and caution:** The durable receipt is authoritative; the facade maintains a process-local response-projection cache. Authentication/tool permission checks happen before this cache is used. A historical receipt is not a new physical action. However, source inspection shows the durable duplicate lookup occurs before the runtime’s ordinary `is_device_isolated()` check. The scenario-specific guardian guard executes before that lookup in an active scenario; outside such a scenario, returning a prior receipt for a now-isolated device is possible. No physical effect is replayed, but the strict “recheck current quarantine before returning any duplicate result” behavior is **not verified** and merits a dedicated follow-up fix/test before treating duplicate replay as a fresh authorization decision.

## 9. Persistence and Resilience

### 9.1 Persistence architecture

Persistence is implemented in [`src/twin_persistence/`](../src/twin_persistence). The SQLite connection is configured for WAL mode and durable synchronous behavior by the connection manager. The migration defines:

- `core_runs` and `core_transitions` for replayable CityCore history.
- `processed_requests` with a unique request ID, command hash/payload, status, result, resulting revision, and timestamps.
- `operations` with a unique request relationship and operation status/timestamps.
- `recovery_metadata` for active-run information.
- `quarantined_devices` for durable quarantine state.

The migration does **not** create `twin_snapshots`; CityCore state is recovered by validated replay, not by an implemented snapshot manager. SQLite is enabled by production launchers through `TWIN_STATE_DB_PATH`; direct `LiveCity` instances may omit persistence. EventStore, ScenarioLogger output, sensor adapters, scenario runner status, and scheduled local runtime state are not all part of the durable replay.

### Figure 8. Durable command and restart recovery

```mermaid
sequenceDiagram
    autonumber
    participant F as TwinFacade
    participant L as LiveCity
    participant DB as SQLite WAL
    participant C as CityCore
    F->>L: Authenticated, validated command
    L->>DB: Claim unique request; status EXECUTING; operation DOING
    L->>C: Apply simulated effect
    C-->>L: Receipt and transition(s)
    L->>DB: Atomic transaction: transitions + final context + COMMITTED + DONE
    L-->>F: Original result
    Note over DB: A crash before final transaction leaves intent incomplete.
    Note over L,DB: Startup marks EXECUTING / PENDING / DOING as RECOVERY_REQUIRED;<br/>uncertain commands are not blindly replayed.
    L->>DB: Load active replay and quarantine state
    DB-->>L: Validated durable replay
    F->>L: Same request ID and payload
    L->>DB: Lookup stored receipt
    DB-->>L: Return committed result; no second CityCore mutation
```

### 9.2 Idempotency and recovery semantics

The evidence demonstrates:

1. Same request ID and same payload returns the prior committed result.
2. Reuse of that request ID with a changed payload is rejected with `IDEMPOTENCY_CONFLICT`.
3. A SQLite unique constraint prevents concurrent request claims from both becoming new requests.
4. CityCore transition rows and the final command receipt are committed in the same SQLite transaction after the simulated effect is applied in memory.
5. On restart, the durable replay reconstructs the committed CityCore state. Incomplete request rows are changed to `RECOVERY_REQUIRED`; they are not automatically replayed.
6. If persistence fails after the in-memory mutation, LiveCity restores the durable CityCore replay and returns a persistence error rather than reporting success.

The process-restart evidence runs two separate Python child processes. Process one advances revision 0→1. Process two loads revision 1, repeats the same request, returns the original result, and remains at revision 1. Both children exit successfully. This validates the `LiveCity` runtime against the SQLite file; it is not evidence of restarting an external MCP host or restarting the dashboard executable itself.

**Demonstrated recovery:** CityCore state/receipt survival, quarantine restoration, owner recreation, fail-closed uncertain requests, and idempotency after a process restart.

**Not demonstrated:** Restoring active ScenarioRunner timing/correlation, sensor adapters and their fault state, event-store history, dashboard browser session state, a durable pending-command queue, or automatic revalidation/replay of queued actions. The design intentionally does not blindly replay uncertain operations.

### Figure 9. SCN-06 communication failure/recovery

```mermaid
stateDiagram-v2
    [*] --> ChannelsAvailable: Scenario starts; local flags initialized
    ChannelsAvailable --> BrainUnavailable: tick 1 controlled injection
    BrainUnavailable --> BothUnavailable: tick 2 Guardian controlled injection
    BothUnavailable --> GuardianUnavailable: tick 3 Brain flag restored
    GuardianUnavailable --> ChannelsAvailable: tick 4 Guardian flag restored
    BrainUnavailable --> Rejected: Brain command fails closed
    BothUnavailable --> Rejected: authorization/isolation calls fail closed
    GuardianUnavailable --> Rejected: Guardian-dependent command fails closed
    ChannelsAvailable --> Revalidate: Fresh request after recovery
    Revalidate --> Rejected: stale revision / quarantine / unsafe
    Revalidate --> SimulatedCommit: fresh valid command only
    SimulatedCommit --> [*]
```

These are in-process flags controlled by scenario triggers. Their event payload explicitly says `external_connection_probed: false`. SCN-06 does not emulate transport retries, packet delivery, service discovery, or an external partner restart.

## 10. Dashboard and Operator Interface

The dashboard is a local HTTP bridge serving a browser UI and controlled APIs. `DashboardBridge` binds to loopback, uses bounded request handling, and validates origin/host/fetch-site properties. Operator access is granted through the launch passphrase and an HttpOnly, SameSite=Strict session cookie. The report view is separate from the private operator frame.

The UI includes an illustrative city map and asset selector, layer toggles, sensor readings/history, a private sensor/fault workspace, operator lifecycle controls, scenario selection, local command controls, event timeline, and JSON frame diagnostics. The map includes illustrative geometry; scenario values and physical overlays come from simulation frames, not measured geography.

### Figure 10. Dashboard data path

```mermaid
flowchart LR
    U["Browser UI<br/>map, sensors, timeline, controls"] -->|same-origin HTTP| D["DashboardBridge<br/>loopback server"]
    D -->|reported frame| PUB["PublicReader / projected data"]
    D -->|session-gated private frame / commands| LC["LiveCity"]
    LC --> S["SensorBank + ScenarioRunner"]
    LC --> C["CityCore"]
    LC --> DB["SQLite state / command store"]
    LC --> EV["Process-local event history"]
    D --> H["Local health/readiness"]
    H -. "does not probe Brain/Guardian" .-> N["Partner service status: unavailable / not connected"]
```

### Dashboard validation

The latest browser campaign opened the actual local dashboard, authenticated operator access, started SCN-06 from the UI, observed its scenario status and correlation ID, and inspected the event timeline. The eight events were ordered from `RECEIVED`, two `PARTNER_CHANNEL_AVAILABLE`, two `PARTNER_CHANNEL_UNAVAILABLE`, two `PARTNER_CHANNEL_RECOVERED`, to `SCENARIO_WINDOW_COMPLETE`.

The operator then changed simulated `A-PUMP-01` from disabled to enabled. Revision changed 314→315, and the dashboard recorded `ACTION_REQUESTED` / `ACTION_COMPLETED` with effect ID `live-0000000106`. After stopping the dashboard process, a fresh LiveCity owner read the same database and recovered the run ID, revision, and pump state. This was an operator action in the simulation, not a Brain/Guardian action or a physical actuator test.

Structured result: [browser-validation.json](../test-evidence/scenario-campaign-20261102T170547.703254Z-de4e30cd/browser-validation.json). A screenshot was captured by the browser tool, but no screenshot file was saved to the repository; image evidence is therefore not a downloadable report asset.

## 11. Testing and Experimental Evaluation

### 11.1 Test methodology

Verification combines unit/contract suites, scenario campaigns, direct resilience probes, dashboard HTTP tests, and an actual browser session. Scenario tests use isolated LiveCity owners; cross-scenario concurrency is controlled and serialized at the Twin boundary. The campaign saves JSON results, event traces, dashboard frames, and audit logs in a unique evidence directory.

The core test runner uses Python’s standard-library `trace` to measure executable-line coverage with a configured 80% minimum. This is not branch coverage. Test counts from the three runners overlap; they must not be summed.

### Table 7. Test results

| Test campaign | Result | Evidence |
|---|---|---|
| Core, interface, and durable checks | 246 tests; 0 failures, 0 errors, 0 skipped; coverage gate PASS | [checks.json](../test-evidence/scenario-campaign-20261102T165554.879075Z-6043b627/regression-core/checks.json), [tests.txt](../test-evidence/scenario-campaign-20261102T165554.879075Z-6043b627/regression-core/tests.txt) |
| Full LiveCity suite | 117 tests; 0 failures, 0 errors, 0 skipped; measured behavior modules at/above 80% | [checks.json](../test-evidence/scenario-campaign-20261102T165554.879075Z-6043b627/regression-live/checks.json), [tests.txt](../test-evidence/scenario-campaign-20261102T165554.879075Z-6043b627/regression-live/tests.txt) |
| Dashboard/API suite | 40 tests; 0 failures, 0 errors, 0 skipped; coverage gate PASS | [checks.json](../test-evidence/scenario-campaign-20261102T165554.879075Z-6043b627/regression-dashboard/checks.json), [tests.txt](../test-evidence/scenario-campaign-20261102T165554.879075Z-6043b627/regression-dashboard/tests.txt) |
| Six individual scenarios | SCN-01…SCN-06 local checks PASS | [campaign.json](../test-evidence/scenario-campaign-20261102T170547.703254Z-de4e30cd/campaign.json) and each linked `individual/*-city-events.json` |
| Six-scenario sequential run | PASS; history retained; no scheduled pending actions | Same campaign, `B_sequential` |
| Cross-scenario synchronization | 5/5 PASS | Same campaign, `C_cross_scenario` |
| SCN-06 targeted communication resilience | All listed checks PASS with controlled local channel injection | [SCN-06 campaign](../test-evidence/scn06-20261102T165453.592708Z-dde078db/campaign.json) |
| Abrupt process restart idempotency | PASS; same result returned; revision remained 1 after restart | [summary.json](../test-evidence/scenario-campaign-20261102T170547.703254Z-de4e30cd/process-restart/summary.json), process logs and database JSON beside it |
| Browser dashboard | PASS for actual page, scenario frame, event history, simulated state change, and persistence reload | [browser-validation.json](../test-evidence/scenario-campaign-20261102T170547.703254Z-de4e30cd/browser-validation.json) |

### 11.2 What the campaign does not establish

The tests do not establish live Brain/Guardian integration, Kafka behavior, real sensor transport, a physical control path, branch coverage, throughput/latency, or multi-host concurrency. SCN-02 with a real partner outage, SCN-03 network disconnection, and alternative actuator execution were not demonstrated. Partner availability in health reports is not probed.

### 11.3 Reproducible commands

Run from the repository root in PowerShell, using new directories for test runner output:

```powershell
python tools\run_scenario_campaign.py --evidence-root test-evidence
python tools\run_scn06_campaign.py
python tools\run_durable_idempotency_evidence.py
python tools\run_checks.py --evidence test-evidence\<new-core-run>
python tools\run_live_checks.py --evidence test-evidence\<new-live-run>
python tools\run_dashboard_checks.py --evidence test-evidence\<new-dashboard-run>
```

The scenario campaign exits with a non-zero status when the aggregate is `BLOCKED`, which is expected while real partner services are absent. Do not convert that status into a full integration PASS.

For browser validation, set `TWIN_STATE_DB_PATH` to an isolated database, start `python tools\run_dashboard.py --port 18765`, enter a test-only passphrase at its prompt, open `http://127.0.0.1:18765/`, connect operator access, run SCN-06, pause, and submit `A-PUMP-01=true`. The captured procedure and state are in [browser-validation.json](../test-evidence/scenario-campaign-20261102T170547.703254Z-de4e30cd/browser-validation.json).

## 12. Results and Contributions

### Table 8. Implementation status

| Capability | Status | Evidence statement |
|---|---|---|
| City domain simulation and revisioned effects | **Implemented and tested** | Core suites and actual scenario CityCore revisions |
| LiveCity lifecycle, sensors, scheduled actions | **Implemented and tested locally** | LiveCity suite, scenario campaign |
| TwinFacade, MCP, REST contracts | **Implemented and tested locally** | Tool/route source and test suites |
| Authentication, role permissions, observation projection | **Implemented and tested locally** | Auth/permissions/projection source and regression tests |
| Scenario orchestration SCN-01…SCN-06 | **Implemented and demonstrated locally** | JSON definitions and campaign evidence |
| Guardian isolation enforcement | **Implemented locally; partner verdict not verified** | SCN-04, quarantine persistence tests |
| Brain decision service | **Not implemented / not verified** | Local caller supplies commands; no service/client found |
| Guardian decision service | **Not implemented / not verified** | Local caller invokes isolation; no service/client found |
| SQLite durable idempotency and CityCore replay | **Implemented and restart-tested** | Process restart evidence and durable test suite |
| Snapshot manager / `twin_snapshots` table | **Not implemented in current migration** | Schema includes replay tables; no snapshot table |
| Pending-command durable queue | **Not implemented** | Outage requests rejected; uncertain execution marked recovery-required |
| Kafka integration | **Not implemented / not tested** | No Kafka dependency/component in runtime |
| Dashboard | **Implemented and browser-tested locally** | Browser validation; local operator control is not a partner command |
| Physical actuator gateway | **Not implemented / not verified** | MCP docs and runtime specify simulated effects |
| Partner service integration | **Proposed / future integration** | Not verified with real Brain/Guardian services |

The technical contributions are an auditable simulation and interface boundary, shared deterministic scenario lifecycle, safety gates and quarantine state, durable request tracking tied to CityCore replay, fail-closed restart handling, and reproducible evidence. These contributions do not constitute a complete autonomous three-layer deployment.

## 13. Limitations and Future Work

### Table 9. Limitations, evidence, and next work

| Limitation | Current evidence/status | Recommended work |
|---|---|---|
| Brain/Guardian partner applications absent | No client/service or response in repository; role calls are local | Agree partner API/auth contracts; implement separately deployable clients; record actual partner request/response evidence |
| No Guardian request-bound positive approval | Pending markers explicitly do not represent approval | Define a versioned approval/verdict contract and fail-closed authorization flow |
| SCN-06 is not a network-failure test | Local deterministic flags; `external_connection_probed=false` | Add controlled transport fault tests once a real client/transport is integrated |
| No persistent pending-command queue | Requests during outage reject; no queued work is replayed | If queuing becomes a requirement, design durable state transitions and authorization/revision revalidation before implementation |
| Runtime adapters and scenario state not durable | CityCore replay persists; sensors/scenario/event history do not | Define which runtime state is required after restart; add versioned migrations and restore tests |
| No `twin_snapshots` storage in current schema | Persistence is replay-based | Add snapshots only if replay size or recovery requirements justify them, with integrity validation |
| SCN-04 fallback actuator aliases primary target | No distinct backup physical target; fallback rejected | Model a physically distinct target and verify its safety/permission mapping before claiming fallback |
| Duplicate receipt versus current quarantine check ordering | Durable replay lookup precedes ordinary isolation test in command execution | Revalidate current isolation and command authorization before returning a stored result; add regression tests |
| No Kafka or other event broker | None configured | Add only if required by agreed deployment; specify ordering, delivery, replay, and security first |
| No physical actuation or HIL evidence | All mutations are CityCore simulation effects | Integrate a controlled hardware gateway and safety interlocks; run a separate hardware-in-loop campaign |
| No quantitative performance results | No latency/throughput measurements in evidence | Define test environment and acceptance measures before collecting results |
| Browser screenshot not stored in evidence directory | Browser screenshot was returned in session only | Persist a sanitized screenshot alongside structured browser evidence in future campaigns |
| Coverage is line execution only | `trace` tool; branch coverage not collected | Add branch/condition coverage if required by the evaluation plan |

## 14. Conclusion

The repository contains a functioning local Digital Twin implementation: CityCore models revisioned city state; LiveCity coordinates the simulation, sensor adapters, scenarios, and operator lifecycle; TwinFacade centralizes the public interaction and safety boundary; MCP and REST expose semantic interfaces; and a browser dashboard presents local state and events. Six deterministic scenario definitions and an integration campaign demonstrate local state changes, unsafe-command rejection, quarantine behavior, event correlation, controlled communication-failure behavior, durable idempotency, and process-restart recovery.

The strongest verified resilience result is that a committed request survives abrupt process restart: the original result is returned and no additional CityCore revision is created. The strongest safety result is fail-closed rejection for suspicious/unvalidated scenarios, quarantined devices, stale revisions, and controlled partner-channel unavailability.

The implementation must still be represented accurately: Brain and Guardian are interface roles, not verified partner services; SCN-06 is local injection, not network recovery; and CityCore actions are simulated, not physical actuation. This report therefore evaluates the Twin as an evidence-backed local prototype and identifies the additional interfaces and integrations needed for a complete three-layer city deployment.

## Appendix A. Source and evidence index

### Core and interfaces

- [CityCore package](../src/city_core)
- [LiveCity runtime](../src/city_live/runtime.py)
- [ScenarioRunner](../src/city_live/b_scenarios.py)
- [SensorBank](../src/city_live/sensors.py)
- [TwinFacade](../src/twin_interface/facade.py)
- [Authentication](../src/twin_interface/auth.py)
- [Permissions](../src/twin_interface/permissions.py)
- [Projections](../src/twin_interface/projections.py)
- [FastMCP server](../src/twin_interface/mcp_server/server.py)
- [REST router](../src/twin_interface/rest/router.py)
- [Dashboard server](../src/city_dashboard/server.py)
- [Dashboard UI](../src/city_dashboard/web/index.html)
- [SQLite persistence package](../src/twin_persistence)

### Scenario, test, and evidence

- [Scenario definitions](../scenarios)
- [Scenario integration campaign runner](../tools/run_scenario_campaign.py)
- [SCN-06 campaign runner](../tools/run_scn06_campaign.py)
- [Durable idempotency evidence runner](../tools/run_durable_idempotency_evidence.py)
- [Scenario orchestration tests](../live_tests/test_scenario_orchestration.py)
- [Durable idempotency tests](../live_tests/test_durable_idempotency.py)
- [Persistence unit tests](../tests/unit/test_twin_persistence.py)
- [Final campaign report and evidence](../test-evidence/scenario-campaign-20261102T170547.703254Z-de4e30cd)
- [Regression evidence bundle](../test-evidence/scenario-campaign-20261102T165554.879075Z-6043b627)

### Documentation context

This report is the current evidence-based summary. Earlier documents such as [`JURY_TECHNICAL_REPORT.md`](./JURY_TECHNICAL_REPORT.md), [`INTERFACE_CARD.md`](./INTERFACE_CARD.md), and [`TECHNICAL_REPORT_PHASES_0_3.md`](./TECHNICAL_REPORT_PHASES_0_3.md) describe earlier project stages and contain statements that should not override the source and current campaign evidence. In particular, narrative descriptions of standalone partner mocks or partner responses are not proof of actual Brain/Guardian services.

## Appendix B. Reproduction quick reference

| Goal | Command |
|---|---|
| Six scenario, sequential, cross-scenario, restart campaign | `python tools\run_scenario_campaign.py --evidence-root test-evidence` |
| SCN-06 controlled communication recovery | `python tools\run_scn06_campaign.py` |
| Abrupt process restart receipt proof | `python tools\run_durable_idempotency_evidence.py` |
| Core/interface/durable regression | `python tools\run_checks.py --evidence test-evidence\<new-core-run>` |
| LiveCity regression | `python tools\run_live_checks.py --evidence test-evidence\<new-live-run>` |
| Dashboard HTTP/API regression | `python tools\run_dashboard_checks.py --evidence test-evidence\<new-dashboard-run>` |
| Local browser dashboard | `python tools\run_dashboard.py --port 18765` (campaign-specific `TWIN_STATE_DB_PATH`; passphrase entered interactively) |
