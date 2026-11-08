# Smart City Digital Twin Scenario Book

## Purpose and evidence boundary

This book describes the repository's six scenarios as implemented and
exercised by the integration campaigns dated 2026-11-02. It does not treat the legacy
`Expected: Brain` or `Expected: Guardian` prose in the scenario JSON files as
proof that those services ran. The campaign explicitly reports that no Brain
or Guardian partner service was running. Calls made with local role credentials
exercise the authenticated TwinFacade boundary only.

The campaign's overall result is **FAIL**: the isolated scenario runs, the
sequential run, and the controlled cross-scenario checks passed, but the
restart/idempotency check failed. Limitations and untested behavior are called
out below rather than counted as successes.

## Architecture and shared lifecycle

```text
Scenario JSON / operator launch
              |
              v
LiveCity ScenarioRunner
  - seeds declared inputs/faults
  - assigns scenario_id, event_id, correlation_id
              |
              v
Twin SensorBank -> Twin reading -> TWIN_UPDATED
              |                      |
              |                      +--> dashboard frame / event and audit sinks
              +--> GUARDIAN_PENDING / BRAIN_PENDING
                                     |
        External partner calls are not started by this repository
        Local authenticated test calls were used by the campaign
                     |                         |
                     v                         v
       Guardian-facing TwinFacade       Brain-facing TwinFacade
       isolate/restore (veto only)      actuator command request
                     \                         /
                      v                       v
                Twin permissions and safety gates
              (quarantine, scenario guard, validation,
                 idempotency, revision and CityCore checks)
                                  |
                                  v
                       CityCore simulated effect
                                  |
                                  v
                  COMMAND_EXECUTED / COMMAND_REJECTED
                    dashboard frame and audit evidence
```

For each active scenario, the `ScenarioRunner` assigns a unique correlation ID.
Lifecycle records carry that ID, a unique event ID, the scenario ID, timestamp,
event type, source, payload, and a device ID when applicable. Sensor event IDs
are linked to related command events where the device relationship is known.
The campaign checked correlation consistency and monotonic event ordering.

The common lifecycle observed in the implementation includes `RECEIVED`,
`TWIN_UPDATED`, `GUARDIAN_PENDING`, `BRAIN_PENDING`, `BRAIN_DECIDED`,
`COMMAND_PENDING`, `COMMAND_EXECUTED`, `COMMAND_REJECTED`, `COMPLETED`, and
`SCENARIO_WINDOW_COMPLETE`. `GUARDIAN_REJECTED` records an isolation action.
`FAILED` can be emitted for scenario setup failure. These are not all emitted
for every run. In particular, the system does not currently have a
request-bound positive Guardian approval state or a real outbound partner
client.

### Twin, Guardian, and Brain

- **Twin** owns the simulation runtime, registered sensors, lifecycle events,
  safety checks, actuator command path, and CityCore effects.
- **Guardian** has an existing authenticated isolation/restoration interface.
  The current scenario integration is **veto-only**: an isolated device is
  blocked, and pending safety checks fail closed. There is no request-bound
  positive-approval contract in this integration.
- **Brain** can use the existing authenticated Twin actuator-command
  interface. A command received by that interface is subject to Twin
  permission, scenario safety, quarantine, idempotency, and state validation.
- This repository does not launch a Brain or Guardian service, nor does the
  scenario runner call either service to obtain a decision. Campaign role
  calls are **controlled local test-client calls**, not real partner-service
  responses and not scenario adapters purporting to be those services.

### Why orchestration exists

The shared `LiveCity` scenario runner makes the five definitions use the same
sensor sampling, correlation, safety, actuator, dashboard, and observability
paths where the scenario supports them. It makes state transitions and
unavailable partner interactions visible instead of fabricating decisions.
It is a coordination and evidence mechanism; it is not a Brain/Guardian
replacement, durable queue, or recovery service.

### Actuator authorization and state effects

The TwinFacade checks caller permissions before command execution. The
scenario guard rejects commands while Guardian isolation is pending in SCN-04,
and rejects commands linked to untrusted sensor data unless the supplied
registered backup reading is eligible and valid. The normal Twin command path
also checks quarantine, command parameters, request idempotency, expected
revision, and CityCore acceptance.

`COMMAND_EXECUTED` is emitted only after the CityCore effect is committed; it
includes the resulting revision/effect receipt. A log record alone is not
counted as an actuator effect. The campaign directly compared before/after
CityCore state:

| Campaign run | CityCore result |
|---|---|
| SCN-01 | Pump unchanged; revision **70 → 70**. Unsafe command rejected. |
| SCN-02 | `PRT-PMP-01` speed **0.0 → 0.3**; revision **8 → 9**. |
| SCN-03 | `IND-VLV-01` opening **0.3333 → 0.25**; revision **10 → 11**. |
| SCN-04 | Pump unchanged; revision **7 → 7**. Primary and same-target alias were rejected. |
| SCN-05 | `DTN-TLT-01` signal **normal → priority**; revision **12 → 13**. |

These are simulated CityCore changes, not physical city-device operations.
The successful commands were sent through controlled local Brain-role test
calls; no actual Brain service generated them.

### Dashboard and audit

The campaign verified that a private operator dashboard frame included the
current scenario and readings for each individual scenario. This validates
frame capture, not browser rendering or visual behavior. Browser rendering was
**NOT TESTED**.

The campaign wrote sanitized JSONL audit logs, Twin event histories, EventStore
exports, and dashboard frames. It checked event ordering, correlation, and the
absence of private actual/truth fields in audit output. The scenario input
definitions may include seeded or fault ground truth, but those private values
are not necessarily present in the public lifecycle reading.

## Scenario sequence and results

The campaign's sequential part ran **SCN-01 → SCN-02 → SCN-03 → SCN-04 →
SCN-05** in one owner, using the supported `LiveCity.reset` between runs.
It verified that each run had its own correlation ID, audit/EventStore history
continued to grow, no scheduled actions remained pending, and stale prior-run
commands were rejected. The sequential result was **PASS**. This reset test
does not establish durable process-restart recovery.

### SCN-01 / T5 — Fake flood at the port

- **Objective:** Exercise a false high port-water reading and ensure it does
  not authorize an unsafe pump command.
- **Initial conditions:** Scenario `T5` seeds the configured input for
  `PRT-WLV-01` at 40 cm. The first Twin sample reported 40 cm. The scenario
  definition declares a false report of 180 cm beginning at tick 5.
- **Trigger:** `python scenarios/run.py --scenario t5`.
- **Expected behavior:** The Twin recognizes the mismatch; the unsafe flood
  response is held/rejected unless a valid configured backup reading is
  supplied. A real Guardian/Brain outcome must not be inferred from pending
  markers.
- **Actual observed behavior:** Twin samples reported 180 cm from tick 5
  onward. The campaign's local Brain-role pump command was rejected with
  `SCENARIO_GUARDIAN_PENDING`; pump state and revision did not change. The
  audit reading omits actual/truth values, so it proves the reported 180 cm
  and the rejection, not an independently observed physical 40 cm.
- **Services involved:** `ScenarioRunner`, SensorBank, TwinFacade, CityCore
  state check, dashboard frame, EventStore and audit logger. Brain and Guardian
  partner services were **BLOCKED / absent**; command exercise was a
  controlled local role call.
- **Actuator changed CityCore:** **No.**
- **Test result:** **PASS** for the campaign's safe-rejection behavior; no
  claim of a real Guardian verdict or real Brain refusal.
- **Evidence:** [Twin events](../test-evidence/scenario-campaign-20261102T154417.731910Z-d4acc976/individual/individual-scn-01-city-events.json),
  [dashboard frame](../test-evidence/scenario-campaign-20261102T154417.731910Z-d4acc976/individual/individual-scn-01-dashboard-frame.json),
  [audit log](../test-evidence/scenario-campaign-20261102T154417.731910Z-d4acc976/audit/individual-scn-01_campaign-1-8690060cfc21.jsonl).
- **Limitations:** No real external flood telemetry, Guardian service verdict,
  Brain service decision, or browser rendering was tested. The campaign did
  not expose the private actual value in its event evidence.

### SCN-02 — Harbor basin surge and emergency valve dispatch

- **Objective:** Send a high-water Twin event through the common path and
  verify that an accepted pump command changes CityCore.
- **Initial conditions:** The scenario starts from the configured 40 cm port
  level and explicitly seeds the Twin input to 110 cm at tick 1. This is a
  seeded simulation event, not live external telemetry.
- **Trigger:** `python scenarios/run.py --scenario scn-02`.
- **Expected behavior:** Twin publishes the 110 cm reading; an accepted
  command must pass through the existing TwinFacade and actually update
  CityCore.
- **Actual observed behavior:** `PRT-WLV-01` reported 110 cm with status `OK`.
  A controlled local Brain-role command set `PRT-PMP-01` speed to 0.3; its
  CityCore revision advanced **8 → 9**. Replaying the same request did not
  create a second revision/effect.
- **Services involved:** `ScenarioRunner`, SensorBank, TwinFacade, CityCore,
  dashboard frame, EventStore and audit logger. Brain and Guardian services
  were **BLOCKED / absent**; command call was a local test-client call.
- **Actuator changed CityCore:** **Yes**, simulated pump state changed.
- **Test result:** **PASS** for seeded event processing and CityCore command
  effect.
- **Evidence:** [Twin events](../test-evidence/scenario-campaign-20261102T154417.731910Z-d4acc976/individual/individual-scn-02-city-events.json),
  [dashboard frame](../test-evidence/scenario-campaign-20261102T154417.731910Z-d4acc976/individual/individual-scn-02-dashboard-frame.json),
  [audit log](../test-evidence/scenario-campaign-20261102T154417.731910Z-d4acc976/audit/individual-scn-02_campaign-2-a017fc0510c3.jsonl).
- **Limitations:** No real rain sensor or Brain-generated decision, Guardian
  service response, or browser render was tested.

### SCN-03 — Sensor death and frozen traffic telemetry

- **Objective:** Distinguish a frozen stream and a dead sensor from usable
  telemetry, and accept a backup-feed command only when the configured backup
  is valid.
- **Initial conditions:** Scenario inputs include traffic baseline 48,
  declared frozen-sensor input 55, industrial temperature input 71, and
  registered backup `IND-TMP-02` input 70.
- **Trigger:** `python scenarios/run.py --scenario scn-03`.
- **Expected behavior:** The frozen traffic report remains at its prior
  sample, the dead sensor becomes unavailable, and a command without a valid
  backup is rejected.
- **Actual observed behavior:** `DTN-TRF-01` continued reporting 48 during the
  `STUCK` fault. `IND-TMP-01` transitioned from a reported 71 to `null` with
  status `FAILED`/unavailable. The registered backup reported 70. A
  no-backup valve command was rejected; a command citing the valid backup
  changed `IND-VLV-01` opening to 0.25 (revision **10 → 11**).
- **Services involved:** `ScenarioRunner`, SensorBank, TwinFacade, CityCore,
  dashboard frame, EventStore and audit logger. Brain and Guardian services
  were **BLOCKED / absent**; the command was a controlled local role call.
- **Actuator changed CityCore:** **Yes**, on the validated-backup command.
- **Test result:** **PASS** for frozen/dead reading handling, unsafe-command
  rejection, and the tested registered-backup command.
- **Evidence:** [Twin events](../test-evidence/scenario-campaign-20261102T154417.731910Z-d4acc976/individual/individual-scn-03-city-events.json),
  [dashboard frame](../test-evidence/scenario-campaign-20261102T154417.731910Z-d4acc976/individual/individual-scn-03-dashboard-frame.json),
  [audit log](../test-evidence/scenario-campaign-20261102T154417.731910Z-d4acc976/audit/individual-scn-03_campaign-3-e1763ea7c18d.jsonl).
- **Limitations:** No real sensor hardware, Brain decision, or Guardian
  service decision was exercised. The private truth inputs are scenario
  fixtures, not independently verified field telemetry.

### SCN-04 — Guardian containment prevents unsafe Brain dispatch

- **Objective:** Show that a command is blocked while containment is pending
  and that quarantine prevents use of the isolated target.
- **Initial conditions:** The scenario defines a port pump quarantine trigger
  for `PRT-PMP-01` and lists `PRT-PMP-02` as a backup. It defines no sensor
  trigger.
- **Trigger:** `python scenarios/run.py --scenario scn-04`.
- **Expected behavior:** Twin fails closed while Guardian containment is
  pending, then rejects commands to the quarantined pump. An alternative
  actuator may be used only if it is independently eligible and authorized.
- **Actual observed behavior:** The pending command was rejected. A
  **controlled local Guardian-role call** isolated `PRT-PMP-01`; the primary
  and `PRT-PMP-02` commands were then rejected with
  `GUARDIAN_RESTRICTED`. Both pump IDs map to the same CityCore physical
  target. CityCore revision remained **7**. No alternative actuation occurred.
- **Services involved:** ScenarioRunner, TwinFacade quarantine and command
  interfaces, CityCore state check, dashboard frame, EventStore and audit
  logger. The Guardian role call was local; the actual Guardian service was
  **NOT TESTED**. Brain service was **BLOCKED / absent**.
- **Actuator changed CityCore:** **No.**
- **Test result:** **PASS** for pending/quarantine rejection. Sensor update is
  **NOT TESTED / not defined** by this scenario. Alternative actuator
  execution was not demonstrated.
- **Evidence:** [Twin events](../test-evidence/scenario-campaign-20261102T154417.731910Z-d4acc976/individual/individual-scn-04-city-events.json),
  [dashboard frame](../test-evidence/scenario-campaign-20261102T154417.731910Z-d4acc976/individual/individual-scn-04-dashboard-frame.json),
  [audit log](../test-evidence/scenario-campaign-20261102T154417.731910Z-d4acc976/audit/individual-scn-04_campaign-4-0f186f3719a1.jsonl).
- **Limitations:** No actual Guardian service was connected. The named backup
  is not an independent actuator; the catalog maps it to the same target, so
  no safe fallback can be claimed.

### SCN-05 — Concurrent real emergency and spoofing attack

- **Objective:** Keep a legitimate traffic response distinct from a
  simultaneous spoofed, scenario-local air-quality fixture.
- **Initial conditions:** Downtown traffic is scenario-seeded to 105 at tick
  5. `RES-AIR-TEST-01` is declared as a local air-quality fixture, not a
  registered Twin sensor; its fixture values are actual 18 and reported 180.
- **Trigger:** `python scenarios/run.py --scenario scn-05`.
- **Expected behavior:** The Twin processes the traffic reading and labels the
  air event as a fixture. A safe accepted signal command may affect the
  configured traffic light; the fixture must not be presented as hardware.
- **Actual observed behavior:** `DTN-TRF-01` reported 105 with status `OK`.
  The lifecycle recorded the air fixture as `scenario_local_fixture: true`.
  A controlled local Brain-role command changed `DTN-TLT-01` from `normal` to
  `priority` (revision **12 → 13**). No real Guardian response to the fixture
  was observed.
- **Services involved:** `ScenarioRunner`, SensorBank for traffic, fixture
  lifecycle emission for air, TwinFacade, CityCore, dashboard frame,
  EventStore and audit logger. Brain and Guardian services were **BLOCKED /
  absent**; the command was a local test-client call.
- **Actuator changed CityCore:** **Yes**, simulated traffic signal changed.
- **Test result:** **PASS** for the seeded traffic event, fixture labeling,
  correlation and CityCore signal effect.
- **Evidence:** [Twin events](../test-evidence/scenario-campaign-20261102T154417.731910Z-d4acc976/individual/individual-scn-05-city-events.json),
  [dashboard frame](../test-evidence/scenario-campaign-20261102T154417.731910Z-d4acc976/individual/individual-scn-05-dashboard-frame.json),
  [audit log](../test-evidence/scenario-campaign-20261102T154417.731910Z-d4acc976/audit/individual-scn-05_campaign-5-809600b3e5cc.jsonl).
- **Limitations:** Neither event is live external telemetry. The air feed is
  explicitly a fixture, and no real Brain or Guardian service was involved.

### SCN-06 — Inter-Layer Communication Failure and Recovery

- **Objective:** Verify fail-closed behavior when the Brain- and
  Guardian-facing Twin interfaces are made unavailable, and revalidate
  requests after the controlled recovery sequence.
- **Initial conditions:** A new `LiveCity` owner starts with pump state at its
  baseline and no scenario-local channel faults. The scenario emits
  `PARTNER_CHANNEL_AVAILABLE` for Brain and Guardian as a **controlled local
  baseline marker**; the repository does not probe an external connection.
- **Trigger:** `python scenarios/run.py --scenario scn-06`.
- **Expected behavior:** At ticks 1–4 the runner injects Brain unavailable,
  Guardian unavailable, Brain recovered, then Guardian recovered. Requests
  against unavailable channels fail closed; no partner decision is
  fabricated. The runtime has no pending-command queue, so rejected outage
  requests are not saved for automatic replay. A delayed request after recovery
  must pass ordinary authorization, quarantine, and expected-revision checks.
- **Actual observed behavior:** The campaign logged both baseline statuses,
  both outages, and both recoveries under one correlation ID. A Brain-role
  actuator request during the Brain outage produced no `BRAIN_DECIDED` event
  and no CityCore effect. Guardian isolate/restore calls and an operator
  restore attempt during its outage returned `INVALID_STATE`; an existing
  quarantine remained in place. After
  Brain recovered but while Guardian remained unavailable, both Brain and
  operator actuator calls were blocked. After both channels recovered, a
  delayed request with the old revision returned `STATE_CONFLICT`; a command
  to a quarantined pump returned `GUARDIAN_RESTRICTED`. Following authorized
  restoration, one fresh command changed pump speed to **0.3**; an exact
  duplicate replayed the same receipt without another effect, and reuse of
  that request ID with changed parameters returned `IDEMPOTENCY_CONFLICT`.
- **Services involved:** `ScenarioRunner`, `LiveCity`, `TwinFacade`, CityCore,
  EventStore, ScenarioLogger, and dashboard-frame capture. Both partner
  services were **absent**. Failure and recovery were **controlled scenario
  injections**, not network outages; calls used local test role credentials,
  not partner mocks or actual partner-service responses.
- **Actuator changed CityCore:** **Yes**, one simulated pump effect after
  recovery and revalidation. No effect was applied during either outage.
- **Test result:** **PASS** for the deterministic local scenario campaign and
  the targeted automated checks. This is not a real-service recovery result.
- **Evidence:** [SCN-06 campaign report](../test-evidence/scn06-20261102T162055.402283Z-2fe9bc5c/campaign.json),
  [Twin events](../test-evidence/scn06-20261102T162055.402283Z-2fe9bc5c/twin-events.json),
  [dashboard frame](../test-evidence/scn06-20261102T162055.402283Z-2fe9bc5c/dashboard-frame.json),
  [audit log](../test-evidence/scn06-20261102T162055.402283Z-2fe9bc5c/audit/scn-06_scn06-27ad756a9ed0.jsonl).
- **Limitations:** No live Brain/Guardian clients, connection probes, transport
  failure, distributed queue, or automatic pending-operation reconciliation
  exists. In-memory request idempotency is not durable across process
  recreation; the earlier integration campaign's owner-recreation probe
  **failed**. SCN-06 does not resolve that defect.

## Cross-scenario synchronization

The campaign ran controlled calls against an isolated `LiveCity` owner; the
facade serializes city operations. It did not assume concurrent simulation
physics. All five cross-scenario cases passed:

| Check | Result and scope |
|---|---|
| Duplicate trigger delivery | **PASS**; replaying the same scenario tick did not add duplicate lifecycle events. |
| Concurrent duplicate command during active spoof | **PASS**; eight calls returned the same rejection and CityCore did not change. |
| Competing commands at one expected revision | **PASS**; one revision-checked operation won and the competing operation conflicted. |
| Sensor failure during emergency | **PASS** in the campaign's controlled case. |
| Preferred actuator quarantined during emergency | **PASS** for safe rejection; no unauthorized fallback effect. |

These results demonstrate controlled in-process ordering and deduplication,
not multi-process concurrency or a distributed event broker. The repository
has no Kafka component in this integration.

## Failure handling and recovery

- A rejected, pending, quarantined, invalid, or revision-conflicting command
  does not produce a successful CityCore effect receipt.
- Twin state, the request ledger, and scenario coordinator are in memory.
  The campaign recreated the owner and retried a previously used request ID.
  The request was accepted again and produced an effect: **FAIL**. Durable
  idempotency, snapshot restoration, and crash recovery are not implemented.
- Brain service unavailable: **BLOCKED**; no Brain service/client was present
  to fail or recover.
- Guardian service unavailable: **BLOCKED**; no Guardian service/client was
  present to fail or recover.
- Kafka interruption: **NOT TESTED**; Kafka is not integrated.
- Real process crash/restart: **NOT TESTED**. Owner recreation is only an
  in-process recovery probe and exposed the idempotency defect.
- Failed actuator validation: **PASS** in the controlled campaign case; the
  action was rejected rather than recorded as executed.

## Campaign results and reproduction

| Campaign part | Result |
|---|---|
| Five SCN-01–SCN-05 individual scenario runs | **PASS**; SCN-04 sensor-update check is **NOT TESTED** because it defines no sensor event. |
| Sequential SCN-01 → SCN-05 with reset | **PASS**; history retained, stale commands rejected, no pending scheduled actions. |
| Controlled cross-scenario synchronization | **PASS**; all five checks listed above passed. |
| Failure and recovery | **FAIL** overall due to duplicate execution after owner recreation; partner failures were **BLOCKED**, Kafka and real crash were **NOT TESTED**. |
| SCN-06 controlled communication campaign | **PASS**; deterministic injected availability transitions, fail-closed guards, stale revision rejection, one post-recovery effect, duplicate safety, correlation, dashboard frame and audit checks. This is not a real partner outage/recovery test. |
| Brain / Guardian services | **NOT TESTED as real services**; no partner process was present. |
| Dashboard | Private operator frame checks **PASS**; browser rendering **NOT TESTED**. |
| Overall campaign | **FAIL**; do not describe the campaign as end-to-end partner integration or restart-safe. |

Run the five-part campaign from the repository root:

```powershell
python tools/run_scenario_campaign.py
```

It creates a new timestamped evidence folder and exits nonzero when the
restart/idempotency check fails. For an individual scenario, launch the shared
runtime with one of:

```powershell
python scenarios/run.py --scenario t5
python scenarios/run.py --scenario scn-02
python scenarios/run.py --scenario scn-03
python scenarios/run.py --scenario scn-04
python scenarios/run.py --scenario scn-05
python scenarios/run.py --scenario scn-06
```

The campaign evidence used for this book is
[`campaign.json`](../test-evidence/scenario-campaign-20261102T154417.731910Z-d4acc976/campaign.json).
The directory also contains per-run Twin event histories, EventStore exports,
dashboard frames, and audit logs.

Run SCN-06's separate, deterministic communication-failure campaign with:

```powershell
python tools/run_scn06_campaign.py
```

It creates a unique `test-evidence/scn06-*` directory containing
`campaign.json`, Twin events, EventStore export, dashboard frame, and sanitized
JSONL audit. A passing result means the **controlled local injection** checks
passed; it does not mean real Brain or Guardian services were exercised.

## Capability coverage matrix

**Demonstrated** means exercised in the recorded campaign. **Partial** means
the Twin-side path exists, but its production integration or behavior was not
validated. **Not demonstrated** means no successful evidence exists. Local
role calls and scenario fixtures are identified explicitly and do not count
as real partner-service responses.

| Capability | Status | Evidence / qualification |
|---|---|---|
| Normal event processing | **Demonstrated** | Seeded Twin SensorBank events observed in SCN-02 and SCN-05; sensor fault paths in SCN-03. |
| Twin-to-Brain communication | **Partial** | Pending lifecycle markers and the inbound Brain command boundary exist. No outbound Brain client or real Brain service response was tested. |
| Twin-to-Guardian communication | **Partial** | Authenticated Guardian isolation boundary and pending markers exist. No actual Guardian service was connected; positive request-bound approval is unsupported. |
| Brain and Guardian coordination | **Partial / controlled local calls** | The Twin veto/quarantine path was exercised with separate local role calls; no real service-to-service coordination or approval handshake was validated. |
| Authorized actuator execution | **Demonstrated in controlled Twin path** | SCN-02, SCN-03 and SCN-05 changed CityCore after Twin checks. The command callers were local test clients, not the Brain service; no positive Guardian approval was tested. |
| Rejected actuator command | **Demonstrated** | SCN-01 untrusted/pending safety rejection; SCN-03 invalid backup rejection; SCN-04 pending and quarantined rejection. |
| Alternative actuator execution | **Not demonstrated** | SCN-04's listed `PRT-PMP-02` shares the same CityCore target and was rejected. SCN-03 validated an alternative sensor feed, not an alternative actuator. |
| Sensor failure handling | **Demonstrated** | SCN-03 frozen traffic report, dead sensor `FAILED`/unavailable, and valid registered backup reading. |
| Multiple-event synchronization | **Demonstrated in controlled in-process tests** | Five cross-scenario checks passed under serialized facade access; no distributed broker or parallel physics claim. |
| Service failure and recovery | **Partial: controlled scenario injection demonstrated; real services not tested** | SCN-06 fail-closed and recovery checks passed locally. Partner services were absent; Kafka and real process restart remain **NOT TESTED**. Owner recreation still permits a duplicate request ID. |
| Dashboard state updates | **Frame demonstrated; UI partial** | Private operator frames included current scenario/readings. Browser rendering was **NOT TESTED**. |
| Audit traceability | **Demonstrated** | Correlation and ordering checks passed; sanitized JSONL audit, Twin events and EventStore exports were saved. |

## Implementation status at a glance

- **Implemented and validated:** shared scenario event orchestration; Twin
  seeded sensor event and fault paths; correlation and event ordering;
  TwinFacade authorization/safety gates; CityCore effect receipts for tested
  commands; same-owner duplicate and reset/stale-context checks; dashboard
  frame capture; sanitized audit evidence.
- **Implemented but not validated against production services:** authenticated
  Brain command and Guardian isolation interfaces. The campaign used local
  role calls only.
- **Partially implemented:** Guardian veto-only integration; cross-scenario
  integration beyond a single in-process serialized owner; dashboard behavior
  beyond frame capture.
- **Simulated using test inputs/calls:** seeded sensor readings and declared
  faults, SCN-05's local air fixture, SCN-06's controlled channel availability,
  and campaign Brain/Guardian role calls.
- **Proposed or unavailable in the tested repository:** actual partner-service
  clients/processes, request-bound positive Guardian approval, Kafka, durable
  idempotency, persistent snapshots, durable pending-operation reconciliation,
  and crash recovery.
