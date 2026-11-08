# Phase 1 Test Book

## Shared scenario pipeline

All six existing JSON scenarios use `LiveCity`'s `ScenarioRunner`. The runner
injects only explicitly seeded sensor inputs and declared sensor faults, then
observes the normal Twin sampling path. It does not fabricate Guardian verdicts
or Brain decisions. A Brain response is recorded only when an authenticated
Brain caller uses the existing Twin actuator interface; an isolation or restore
is recorded only when an authenticated Guardian caller uses the existing
Guardian interface.

Guardian integration is currently veto-only: there is no request-bound positive
approval API. `GUARDIAN_PENDING` means that no real verdict has arrived;
`GUARDIAN_REJECTED` records a real inbound isolation action. Twin permissions,
device quarantine, command validation, idempotency, and CityCore checks still
apply before any effect. Successful actuator receipts identify the committed
CityCore effect. Partner services are not launched by this repository, so a
scenario can remain pending until the Brain or Guardian team calls the existing
interfaces. The local scenario selector is available after authenticating the
operator dashboard.

The Residential `RES-AIR-TEST-01` observation in SCN-05 is a scenario-local
fixture, not a sensor in the Twin registry. It appears as a clearly labeled
scenario event and is never registered or sampled as hardware. SCN-04's listed
backup pump `PRT-PMP-02` shares a CityCore physical target with `PRT-PMP-01`;
quarantining the primary therefore also makes this alias ineligible as a
fallback. The runner does not bypass that quarantine or invent another device.

| ID | Existing scenario data | Shared Twin behavior | Partner behavior and command outcome |
|---|---|---|---|
| T5 / SCN-01 | Port flood spoof: PRT-WLV-01 actual input 40 cm, reported value 180 cm from the declared false-reading fault. | Samples through SensorBank; exposes the actual/reported mismatch in private Twin diagnostics and lifecycle events. Unsafe Brain commands linked to the untrusted sensor are rejected unless a validated registered backup reading is supplied. | Guardian is pending until it actually isolates the sensor. Brain is pending until an actual command or response arrives. No automatic flood action is fabricated. |
| SCN-02 | Port level seeded to 110 cm from the existing scenario definition. | Forces a real Twin sensor sample of the seeded value and publishes it through the ordinary reading path. | The dashboard and MCP/REST consumers can observe it. A Brain command is applied only after existing permission, idempotency, quarantine, and CityCore validation. |
| SCN-03 | Existing traffic and temperature sensor faults, plus seeded IND-TMP-02 backup input. | Samples the traffic counter before and during the STUCK fault, and records the dead sensor's failed reading. Backup commands require an existing configured backup whose latest reading is valid, matches actual input, and is linked to the target device. | Guardian and Brain outcomes remain pending until real inbound calls occur. The runner neither invents backup readings nor automatically issues a command. |
| SCN-04 | Guardian containment of PRT-PMP-01; existing scenario data lists PRT-PMP-02 as backup. | Waits for real Guardian isolation before treating the target as quarantined. Twin rejects a Brain request while isolation is pending and rejects it through the normal quarantine gate after isolation. | PRT-PMP-02 is not an eligible fallback in the current device catalog because it maps to the same physical pump target as PRT-PMP-01. |
| SCN-05 | Seeded Downtown traffic value and scenario-local Residential air fixture at the same tick. | Publishes the traffic event through SensorBank. Emits the air fixture as a labeled fixture event only; it is not inserted into the hardware registry. | Only an actual Brain command to an existing eligible signal can change CityCore. No Guardian or Brain response is synthesized for the air fixture. |
| SCN-06 | Controlled, deterministic availability transitions for Brain-facing and Guardian-facing inbound Twin interfaces. | Emits explicit baseline, unavailable, and recovered lifecycle records. Injected failures block Brain commands and Guardian isolate/restore calls; an unavailable Guardian also blocks Brain and operator actuator commands. Quarantine is preserved. | This is local `ScenarioRunner` failure injection, not a network outage or service probe. No Brain decision or Guardian verdict is fabricated. Outage requests are rejected, not queued; delayed requests are evaluated again against current revision and safety gates. |

## Launch and inspect

Start one shared Twin process with an existing scenario. This starts the
dashboard and FastMCP server against the same `LiveCity`; scenario events are
written to the normal observability log:

```text
python scenarios/run.py --scenario t5
python scenarios/run.py --scenario scn-02
python scenarios/run.py --scenario scn-03
python scenarios/run.py --scenario scn-04
python scenarios/run.py --scenario scn-05
python scenarios/run.py --scenario scn-06
```

The command prompts for a one-launch operator passphrase. The dashboard and MCP
are loopback-only by default; the dashboard defaults to `http://127.0.0.1:8765`
and MCP to port 8000. Optional `--dashboard-port`, `--mcp-port`,
`--mcp-transport`, `--run-id`, and `--dev` arguments are forwarded to
`tools/run_twin.py`. Do not enable `--dev` outside local development.

Alternatively, start the shared runtime using `python tools/run_twin.py` and
launch the scenario from the authenticated dashboard's **Existing scenario**
selector. Brain and Guardian integrations must use their existing authenticated
MCP/REST contracts; scenario launch alone does not claim their participation.

Verify:

1. Authenticate to the private operator dashboard and start the chosen
   scenario, or start it with the command above.
2. Observe the scenario correlation ID and lifecycle timeline. Sensor events
   appear in the normal readings and dashboard frame; SCN-05's fixture remains
   explicitly labeled.
3. Use the existing Brain/Guardian credentials and interfaces to issue real
   inbound commands or isolation actions. A pending status is not a service
   response.
4. Confirm a successful actuator result has `city_core_updated: true` and a
   committed effect/revision. A rejected or pending command is not execution.
5. Replay a successful request ID to verify the existing idempotent receipt is
   returned without a second physical effect.

Targeted orchestration tests:

```text
python -m unittest live_tests.test_b_scenario_adapter live_tests.test_scenario_orchestration
python -m unittest dashboard_tests.test_bridge
```

Run the full integration campaign and create a unique evidence directory (the
campaign will not overwrite an existing run):

```text
python tools/run_scenario_campaign.py
```

The campaign's overall result may be `FAIL` or `BLOCKED` when it detects
current durability defects or requires unavailable Brain/Guardian services.
Read `campaign.json` for per-check `PASS`, `FAIL`, `BLOCKED`, `SKIPPED`, and
`NOT TESTED` results and the exact evidence paths.

Run the dedicated communication failure/recovery campaign:

```text
python tools/run_scn06_campaign.py
```

The campaign creates a unique `test-evidence/scn06-*` directory with a JSON
report, Twin events, EventStore export, dashboard frame, and sanitized audit
log. A passing run validates controlled local failure injection only; no
external Brain or Guardian service is involved. There is no durable pending
command queue or process-restart recovery in this architecture.
