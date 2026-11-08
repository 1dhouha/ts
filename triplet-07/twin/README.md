# Sensor fix — 29 October 2026

The live simulation now uses only the sixteen sensors from the supplied sensor
specification. All sixteen have physical sources; legacy S-* sensors are disabled.
The dashboard, fault controls, replacement history and demonstrations use this
inventory. Restart an already running dashboard after updating.

To run Dashboard and MCP against the same LiveCity sensor state, start the
combined owner from this repository folder (Python 3.12):

```powershell
python -X utf8 tools/run_twin.py --dev
```

This starts the Dashboard and MCP server with one LiveCity owner. Choose a
launch passphrase, keep the console open, and connect the Dashboard in your
browser. Type `quit` to stop both interfaces and the owner. `--dev` enables
local development tokens only; omit it when using configured production tokens.
The dashboard-only `tools/run_dashboard.py` and MCP-only `tools/serve_mcp.py`
launchers each create their own owner and do not share runtime state.

Health probes are service-local: Dashboard and HTTP MCP expose
`/health/live` and `/health/ready`; the REST router exposes `/health/live`,
`/health/ready`, and legacy `/health` (an alias for readiness). Liveness means
the process answered. Readiness reports LiveCity/city_core and the service's
own required bridge; HTTP 503 means a required component is unavailable.
`DEGRADED` is still HTTP 200 when the simulation is usable but optional audit
sinks are not configured. If a configured sink fails, the facade is not ready.
An unavailable independently hosted REST process does not make Dashboard or
MCP unready.

For the network-facing Person C ASGI application, see
[Person C unified ASGI application](docs/PERSON_C_ASGI.md). It serves REST and
MCP Streamable HTTP on one port and one shared simulation owner; unlike the
local demo launchers, it requires explicit production role tokens and allowed
host configuration.

For Docker and remote partner deployment, see the
[Docker guide](docs/DOCKER.md),
[cloud deployment guide](docs/CLOUD_DEPLOYMENT.md), and
[partner connection guide](docs/PARTNER_TESTING.md).
For the role-by-role REST and MCP handoff with localhost and cloud endpoints,
see the [Twin Layer connection guide](docs/LAYER_HANDOFF.md).

[Sensor model and migration](docs/SENSORS.md) ·
[Verification and remaining limits](docs/SENSOR_VERIFICATION.md)

The deployed ASGI app serves the dashboard at `/` alongside REST and MCP, all
against one city owner. Configure `TWIN_DASHBOARD_SECRET` as described in
[Person C unified ASGI application](docs/PERSON_C_ASGI.md). The earlier notes
below preserve the original planning-workspace handover; their links outside
this repository refer to that historical workspace.

---

**Local dashboard:** double-click [Start Dashboard.cmd](Start%20Dashboard.cmd),
choose a hidden launch passphrase and connect operator in the opened browser.
[Controls, recovery and limitations](../docs/architecture/dashboard/README.md) ·
[Final verification/handover](../docs/architecture/dashboard/d07/D07_REPORT.md).
No installation required; keep the console open and type `quit` to stop the owner.
Earlier phase notes below are historical.

# Person A local core — Phase11 handover

**Live extension, authorized 18 October 2026:** use the new
[live city runbook](../docs/integration/LIVE_CITY_RUNBOOK.md) for the interactive
Python city, traffic, incidents and local sensor adapters. The original core
below remains unchanged; the live extension's [compatibility status](../docs/integration/SENSOR_INTEGRATION_STATUS.md)
records unsupported physics and unverified B/C contracts. The earlier `ready/`
package is a preserved snapshot, not the live extension distribution.

[APPROVED TEAM DESIGN] Phases01–10 ACCEPTED (10: A preparation only). Phase11
authorized18 October2026 by “done w 10 go to 11”; bounded handover without B/C.
Core/API/physical rules and tests unchanged. Actual B/C compatibility/agreements
and whole-Twin readiness UNVERIFIED. One combined independent reviewer, two rounds.
See [the portable handover guide](../docs/architecture/PERSON_A_HANDOVER.md) for current
run commands, versions, recovery and report inputs. Older stage statements below
are historical; no submission/publication/cloud/Git/installation is authorized.

Python3.12+. From the workspace root (bundled runtime; no installation):

```powershell
& 'C:/Users/anasb/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe' -X utf8 person_a/tools/run_checks.py --evidence tmp/phase09/local_checks
```

The runner executes unittest and stdlib trace, requiring >=80% executable-line
coverage per behavior module. Import/export-only __init__.py is trace-excluded and
AST-guarded; tests/tools are outside coverage. No branch/full-triplet E2E claim.

Repeatable demonstration (set PYTHONPATH=person_a/src):

```python
from city_core import create_core
core = create_core()
assert core.lifecycle.initialize("r-example").ok
request = {
    "contract_version": "pa-internal-0.1-draft", "run_id": "r-example",
    "effect_id": "eff-17-1", "expected_revision": 0, "source": "B_ACTUATOR",
    "correlation_id": "cmd-17",
    "effect": {"tag": "valve_position", "target_id": "V-12", "opening_fraction": 0.0},
}
first = core.actuators.apply_effect(request)
assert first.ok and first.value.outcome == "APPLIED" and not first.value.duplicate
assert core.lifecycle.advance_one("r-example", 1).ok
retry = core.actuators.apply_effect(request)
assert retry.ok and retry.value.duplicate and retry.value.context.revision == 1
assert core.metadata.read_metadata().value.context.revision == 2
assert core.observations.read_private("r-example").value.physical_state.valve.effective_opening_fraction == 0.0
assert core.lifecycle.reset("r-example", 2, "r-next").ok
assert core.actuators.apply_effect(request).error.code == "OLD_RUN"
assert core.metadata.read_metadata().value.context.revision == 0
```

The composition owner keeps lifecycle; B gets observations and actual-movement
actuators; private B scenario coordinator gets scenarios; C public handlers get
metadata only. C's holder has context/topology/token count, no private state/history
or writer pointer. Trusted Python capabilities prevent accidental access, not
hostile reflection; C must implement public authentication/verdict enforcement.

create_core() is uninitialized; build_city(run_id, *, initial_config=..., catalog=...)
is the compatible initialized wrapper. Initialize validates complete fixed config
and catalog before publishing; second initialization rejects. Before initialization
reads/writers return NOT_READY. Missing packaged data returns a safe error.

Both writer handles expose apply_effect(request:dict). See exact envelope/ten tag
schemas in the [internal contract](../docs/architecture/PERSON_A_INTERNAL_CONTRACT.md)
and internal fixtures/effect_contract.json. Actuators support effective valve/pump/
feeder/signal changes and responder reserve/release. Scenarios additionally support
water/source/road/air drivers. Caller source cannot upgrade a handle. No generic
setter, command intent/report, timestamp, unit conversion, quarantine or trust tag.

APPLIED means one immediate primary-state commit: revision+1, effect sequence+1
(first sequence0), private EFFECT trace, unchanged tick/time. Same-value settings
also commit once. This is distinct from command acknowledgement or mission success.
No ACCEPTED/SCHEDULED result or A queue: B owns movement delays/failures/schedules.

Validate syntax/version/handle source -> current run -> successful ID lookup ->
full normalized content equality -> original duplicate receipt or IDENTITY_CONFLICT
-> new expected_revision/budgets/semantic preconditions -> atomic commit. Duplicate
returns original context/sequence even after later effects/steps. Rejection changes
no state/time/ledger/trace. Inputs detach/freeze; key order ignored, route order kept;
finite int/float normalize, bool/NaN/infinity reject, -0=0. After uncertain outcome,
retry the entire original unchanged request. Only explicit STATE_CONFLICT permits
reconciliation/revision refresh for that uncommitted ID. C cmd-17 -> B movement
ordinal1 -> A eff-17-1 is provisional; fresh retry IDs defeat duplicate protection.

Responder reservation requires a free unit and connected usable positive-capacity
route to its declared junction. Current flood/obstruction/signal/power restrictions
gate reservation and active-edge movement; first timer=length/speed+queue/capacity.
No route-selection policy. Empty route at current location reserves as arrived;
matching arrived release frees it there. Phase08 steps evolve water→queues→travel→air
atomically; power/capacity/access facts derive immediately.
Travel/queue details and the private hospital access view are documented below.

advance_one(run_id, expected_revision, *, duration_ms=1000) advances exactly1000ms
and one revision, retaining effect ledger/sequence. Other durations/types reject.
One lock serializes effects/steps/reset/reads. Competing same-revision requests
produce one commit and conflicts; ingress order must be recorded by the coordinator.
Pause means no call; caller pacing never changes simulation dt.

reset(old_run_id, expected_revision, new_run_id, *, initial_config=...) requires a
never-consumed run ID and valid replacement config. Omitted config restores baseline.
Failed reset consumes nothing; successful reset clears reservations, ledger/sequence,
trace, tokens and clock/revision. Held immutable values stay safe, old callbacks
reject OLD_RUN. B cancels device/scenario/fault/observation delays; C reconciles retry/
verdict/delivery/mock caches and preserves logs. Resume after common acknowledgement.
This is A reset, not whole-Twin atomic reset. No quarantine engine under Q-17:
Guardian decides restrictions, C/B authenticate/enforce/cancel delayed operations.
Required future B/C checks include verdict bypass and revoke-versus-due-movement races.

Private pages contain exact initial config/versions/digests and successful STEP/
EFFECT inputs. Index=revision-1, cursor=-1 starts, limit1..1000, end pages pollable.
Export privileged evaluation evidence before reset if needed. Never project private
snapshots/receipts/digests/hidden labels into C public responses/logs/UI/ADT. Tokens
expose context, caller correlation and opaque run-scoped sequence only; no physical
revision change or proof of effect/trust. Caller correlations must not carry secrets.

Per-run limits:100000effects,100000ticks,200000records,100000tokens;10000consumed
run IDs per authority. MODEL_LIMIT rejects before crossing, without silent eviction.
Successful IDs retained for the whole run; duplicates work at capacity. No durable
restart recovery/global run registry; B/C coordinate fresh namespaces/reconciliation.

See [design](../docs/architecture/PERSON_A_CORE_DESIGN.md) and
[Phase09 report](../docs/architecture/phase-records/PHASE_09_REPORT.md). Local real-core
consumers prove only A boundaries, not actual B/C or Brain/Guardian interoperability.

## Phase05 private power checkpoint

The privileged snapshot now has a derived .power property. No new read capability,
public payload, setter, stored authority or timestamp. Each access computes an
immutable PowerState from that snapshot's frozen primary inputs and fixed model
profile, so a held snapshot's facts remain unchanged after later effects/reset.
PrivateSnapshot dataclass fields remain unchanged. Phase06 adds runtime accounting
inside physical_state.water, as described below; private physical digests include it. The supported city_core.transitions.plain() helper walks dataclass
fields, handles immutable mapping proxies and omits computed properties. Standard
dataclasses.asdict() raises TypeError on these private records because it cannot
copy mapping proxies. plain(snapshot.power) explicitly serializes feeder/asset records and pump_running;
convenience properties/totals require explicit privileged access as below.

| Internal fact | Meaning / unit |
| --- | --- |
| power.feeders[id].energized | Available source AND effective physical switch closed |
| requested_demand_kw / delivered_load_kw / unmet_demand_kw | Connected requested kW / actual delivered kW / requested minus delivered |
| rated_capacity_kw / overload | Synthetic feeder rating; overload=requested>rating while energized, no automatic trip |
| power.assets[id].feeder_id / powered | Approved branch and supply availability, independent of mechanical enable |
| power.assets[id].requested_demand_kw / delivered_load_kw | Asset's request/delivery, counted exactly once in its feeder |
| power.pump_running / pump_delivered_demand_kw | Effective enable AND supply; motor delivery10kW when running, otherwise0 |
| power.hospital_powered / plant_active | Only ESS / IND branch availability respectively |
| total_requested_demand_kw / total_delivered_load_kw / total_unmet_demand_kw | Sum across feeders, instantaneous kW, never kWh |

Fixed bindings: GRID-01 feeds F-ESS→HOSP-01/P-03, F-URBAN→LOAD-URBAN/J-D1/J-D2,
F-IND→PLANT-01. Base demands50,80,100kW plus two0.1kW signals and pump10kW
when enabled. Baseline total230.2kW; enabled pump total240.2kW. Absent supply
preserves requested demand and exposes unmet demand; it does not open the switch.
Industrial isolation preserves ESS only because this topology has separate branches.
Blocked outfall/river do not disable the motor or remove its demand; delivered water
flow now follows the Phase06 water/outfall and available-storage rule. Hospital supply is
not proof of clinical safety; plant active is not an air-concentration simulation.

Pure helper account_feeder tests zero demand and exact/excess rating. Current fixed
ratings100/120/150kW prevent overload in reachable live configurations; parameter
overrides remain rejected. No solver, energy accumulation, brownout, automatic trip,
load shedding, backup generator, B meter readings/faults or C public projection.
Signal reservation capacity now consumes the same asset_powered helper. Unknown
or non-connected asset targets reject UNKNOWN_TARGET rather than assuming supply.

This example is privileged local evidence; do not print its facts into public logs:

```python
from city_core import build_city
from city_core.transitions import plain
import json
core = build_city("r-power").value
pump = {"contract_version": "pa-internal-0.1-draft", "run_id": "r-power",
        "effect_id": "pump-on", "expected_revision": 0, "source": "B_ACTUATOR",
        "correlation_id": None, "effect": {"tag": "pump_enable", "target_id": "P-03", "enabled": True}}
assert core.actuators.apply_effect(pump).ok
held = core.observations.read_private("r-power").value
assert held.power.pump_running and held.power.feeders["F-ESS"].delivered_load_kw == 60.0
industrial = {**pump, "effect_id": "industrial-off", "expected_revision": 1,
              "effect": {"tag": "feeder_position", "target_id": "F-IND", "closed": False}}
assert core.actuators.apply_effect(industrial).ok
facts = core.observations.read_private("r-power").value.power
assert not facts.plant_active and facts.hospital_powered and facts.pump_running
essential = {**pump, "effect_id": "essential-off", "expected_revision": 2,
             "effect": {"tag": "feeder_position", "target_id": "F-ESS", "closed": False}}
assert core.actuators.apply_effect(essential).ok
snapshot = core.observations.read_private("r-power").value
assert snapshot.physical_state.pump.effective_enabled and not snapshot.power.pump_running
assert snapshot.power.feeders["F-ESS"].unmet_demand_kw == 60.0
private_payload = plain(snapshot.power)
assert private_payload["feeders"]["F-ESS"]["unmet_demand_kw"] == 60.0
json.dumps(private_payload, allow_nan=False)  # Privileged serialization; do not print publicly.
assert held.power.pump_running  # Old immutable snapshot remains coherent.
assert core.lifecycle.reset("r-power", 3, "r-power-next").ok
assert not core.observations.read_private("r-power-next").value.power.pump_running
```

fixtures/power_contract.json contains four private B-like real-core checkpoints.
These support future meter/drainage integration; actual B/C implementation and
public contracts remain unverified. Whole-layer R1/R2/R3 and trust enforcement are
not proved by this A slice. See the Phase05 report for actual tests/reviews/identity.

## Phase06 water balance and flood checkpoint

advance_one now evolves water exactly once per accepted1s step after evaluating
current power. Effects still change only their permitted settings/drivers, without
elapsed time or retroactive discharge. Pause/read/duplicate/reject do not evolve water.

Rain input=rain_mm_h/1000/3600*20000m²; controlled input=0.6*effective valve fraction.
Outfall usable only if not blocked and river<2m (city-water datum). Gravity requests
0.2m³/s, pump requests1.2m³/s when powered+enabled; both require usable outfall.
Actual discharge is limited by all available water, gravity allocated first. Next
combined storage fills the6000m³ basin and retains finite excess in surface storage.
Overflow into surface storage is internal routing, not deleted water or external loss.

Total next basin+surface=previous basin+surface+rain+inlet−gravity−pump. All values
are binary64. Checks compare using abs(a-b)<=1e-9+1e-9*max(abs(a),abs(b)); this is
numerical tolerance, not a physical safety margin. Large finite surface storage is
not artificially capped; nonfinite arithmetic returns MODEL_LIMIT atomically.
Computation sums relative to the crest to preserve adjacent flood-threshold values.
These are synthetic equations/parameters, not calibrated hydraulic predictions.

| Private fact | Meaning |
| --- | --- |
| physical_state.water.cumulative_in_m3 / cumulative_out_m3 | A-generated run totals; reset to0; never initial/effect setters |
| physical_state.water.last_step | None before firststep; frozen actual rain_in_m3/inlet_in_m3/gravity_out_m3/pump_out_m3 for most recent accepted1s step |
| last_step.inflow_m3 / outflow_m3 | Sum of actual input/discharge volumes; actual rate equals volume/1s for this fixed step |
| snapshot.water.rain_inflow_m3_s / inlet_flow_m3_s | Current rain and effective-valve inputs, immediately reflecting accepted effects |
| snapshot.water.outfall_available / gravity_capacity_m3_s / pump_capacity_m3_s | Current possible drain capacities; actual discharge additionally requires water |
| snapshot.flood[road_id].depth_m / flooded | Immutable mapped exposure; threshold reached at LOW200m³ or HIGH1000m³; other roads have0depth/false |

Current capacity is distinct from last-step actual discharge. A running motor can
have0actual discharge because of a dry store or blocked outfall; blocked outfall
may still draw10kW. Closing the inlet leaves direct rain active. A reported/requested
closure cannot change truth; only a validated actual effect changes valve position.
No B fault/spoofing/reading implementation or trust judgment is introduced.

The exact five initial water fields remain required. Runtime counters/last_step are
forbidden in initial config/reset/effects; reset restores selected initial volumes,
zero totals and None last_step. Private physical serialization/digests now include
runtime accounting; old pre-evolution digests cannot be compared across profiles.
Within the current model, header+ordered STEP/EFFECT replay remains deterministic.
Computed water/flood properties are omitted by plain(snapshot); explicit privileged
plain(snapshot.water)/plain(snapshot.flood) is supported, like the power view.
Never forward these facts/digests/labels to ordinary C responses/logs/UI/ADT.

LOW and HIGH alone are exposed to FLOOD-01. Depths use deck0/0.8m; closure is exact
at0.2m without broad tolerance. The same calculation supports existing reservation
preconditions. Full traffic capacity/queues/hospital reachability/travel are Phase07;
this phase does not advance those domains or select a route.

Privileged executable example (no private stdout):

```python
from city_core import baseline_config, build_city
config = baseline_config()
config["initial_physical"]["water"].update(basin_volume_m3=6000.0, surface_volume_m3=199.4, rain_mm_h=36.0)
config["initial_physical"]["valve"]["effective_opening_fraction"] = 1.0
core = build_city("r-water", initial_config=config).value
flooded = core.lifecycle.advance_one("r-water", 0).value
assert flooded.flood["R-LOW"].flooded and not flooded.flood["R-HIGH"].flooded
pump = {"contract_version": "pa-internal-0.1-draft", "run_id": "r-water", "effect_id": "pump-on",
        "expected_revision": 1, "source": "B_ACTUATOR", "correlation_id": None,
        "effect": {"tag": "pump_enable", "target_id": "P-03", "enabled": True}}
assert core.actuators.apply_effect(pump).ok
pumped = core.lifecycle.advance_one("r-water", 2).value
assert not pumped.flood["R-LOW"].flooded
assert pumped.physical_state.water.last_step.pump_out_m3 == 1.2
blocked = {**pump, "effect_id": "outfall-blocked", "expected_revision": 3, "source": "B_SCENARIO",
           "effect": {"tag": "water_driver", "rain_mm_h": 36.0, "river_level_m": 1.0, "outfall_blocked": True}}
assert core.scenarios.apply_effect(blocked).ok
stalled = core.lifecycle.advance_one("r-water", 4).value
assert stalled.power.pump_running and stalled.power.pump_delivered_demand_kw == 10.0
assert stalled.physical_state.water.last_step.pump_out_m3 == 0.0
assert stalled.flood["R-LOW"].flooded
assert core.lifecycle.reset("r-water", 5, "r-water-next").ok
assert core.observations.read_private("r-water-next").value.physical_state.water.last_step is None
```

fixtures/water_contract.json gives hand-checkable F01–F05 local checkpoints.
Actual B/C scenario/observation/public integration remains UNVERIFIED. No whole-Twin
R1/R2/R3/trust success or full-layer competition score is claimed.

## Phase07 private traffic, connectivity and responder checkpoint

Current private `snapshot.traffic` maps each road to frozen `RoadConditions`:
`depth_m`, `usable`, `capacity_veh_s`. Flood equality closes LOW at surface200m³,
HIGH at1000m³; obstruction1 also closes. A stop/unpowered signal can leave `usable`
true while capacity is0. LOW/IND use J-D1; DOWN/LINK use J-D2; HIGH is unsignalized.
Normal multiplier1, priority1.5, stop/unpowered0; base capacity0.5veh/s times
1−obstruction. Current capacities are throughput limits, never actual departures.

Every1s step uses post-water conditions: available=previous queue+arrival rate,
departed=min(available,capacity), next queue=available−departed. Queues use
fractional vehicle equivalents. Each road has external arrival/exit boundaries;
vehicles are not routed between these queues. The accepted model imposes no
finite storage cap, so saturation refers to throughput. All values must remain
finite; nonfinite arithmetic returns MODEL_LIMIT before any commit. Closure
retains queued vehicles; reopening removes at most the modeled capacity.

Private `snapshot.access` is frozen `HospitalAccess(facility_id="HOSP-01",
destination_node_id="J-H1", reachable_by_origin=<immutable junction→bool map>)`.
It computes connectivity through positive-capacity roads in the accepted graph;
no path choice or estimated response policy. From J-D1 the alternate requires
DOWN+LINK+HIGH all traversable. Both approaches closed means unreachable;
zero-edge access at J-H1 remains true. Congestion changes travel delay, not
connectivity. Hospital supply is separately `snapshot.power.hospital_powered`.
These derived views are private P and omitted by plain(snapshot); serialize
plain(snapshot.traffic) or plain(snapshot.access) only in privileged evaluation.
C metadata/token/topology behavior stays the same; never export hidden truth.

A alone advances the three finite physical units AMB-01/AMB-02/POL-01. B supplies
unit/path/destination and owns command delays/faults/reporting; Brain chooses
operational responses. Each elapsed tick after queues: check current edge capacity;
if closed, block and discard the tick budget, preserving the started timer.
Reopening resumes without resampling that timer. Complete an edge by advancing
node/index; sample the next edge once from this tick's post-traffic queue/capacity.
A closed next edge stores remaining0, then samples when it opens. Carry unused
seconds within the same tick, with at most5edges; never bank blocked time.
Full route is retained, index counts completed roads, node is the last reached
junction, and its suffix terminates at destination. Arrival keeps the unit
reserved until matching release; no return teleport, reroute, incident resolution
or road clearing. An effect applies no travel time. A later step can fail at a
next-edge duration overflow; water/traffic/travel/time/history all roll back.

Repeatable local core example (same PYTHONPATH setting as above):

```python
from city_core import baseline_config, build_city
config = baseline_config()
config["initial_physical"]["water"].update(basin_volume_m3=6000, surface_volume_m3=200)
core = build_city("r-access", initial_config=config).value
held = core.observations.read_private("r-access").value
assert held.traffic["R-LOW"].capacity_veh_s == 0
assert held.access.reachable_by_origin["J-D1"]  # DOWN+LINK+HIGH
after = core.lifecycle.advance_one("r-access", 0).value
assert abs(after.physical_state.roads["R-LOW"].queue_vehicles - 10.2) < 1e-9
assert held.physical_state.roads["R-LOW"].queue_vehicles == 10
```

fixtures/traffic_contract.json is private checkpoint data, not a public protocol
or B scenario trigger. Unit/boundary integration tests include a local critical
rain→flood→blocked travel→pump/power recovery flow, fractional arrival tick91,
duplicate dispatch, source outage, reset and mixed trace replay. Full Twin R1/R2/R3,
camera/command/fault models, partner trust and real B/C integration remain unverified.

## Phase08 PM2.5 and emergency-support checkpoint

`physical_state.air[zone]` is the existing frozen AirState with `pm25_ug_m3`
(PM2.5 µg/m³) and `external_source_ug_m3_s` (µg/m³/s). No AQI, medical threshold,
trust label or source type is inferred. For each1s tick:

```text
next = 10 + (previous - 10) / 1.01 + external_source + plant_contribution
```

The synthetic background is10µg/m³. Source-off relaxes toward10; zero initial
concentration rises slightly toward that background. The plant adds0.2 industrial
and0.05 residential iff actual PLANT-01 supply is available; zero in other zones.
Opening F-IND removes future plant source without resetting existing concentration
or essential supply. The external source driver is independently controlled by B's
privileged scenario capability, bounded0..100µg/m³/s; a dispatch or warning does
not change it. No plume transport, chemistry, mass balance or calibrated prediction.

Air is the last staged physical update, after water/queues/responders. Nonfinite
or negative computed results reject MODEL_LIMIT before publishing any domain,
clock, revision, trace or ledger change. Valid initial concentrations are finite
and nonnegative without an invented upper cap; reset restores baseline or a valid
initial fixture. Effects change source/switch settings immediately but consume no
elapsed time. Private replay retains the same source/effect/step sequence; older
profiles that did not evolve air are historical.

Emergency support reuses this same snapshot's actual conditions, topology,
`physical_state.responders`, `.access`, `.traffic`, `.power` and `.flood`. B owns
call events/caller reports and observations; C owns their callable public exposure.
No new call/incident/demand lifecycle or duplicate city is added. Fake/multiple
calls are reported events, not extra physical states; unsupported report payloads
cannot write the core. Device isolation/reading correction are also B/C/Guardian
responsibilities under open Q-17, never an automatic district power cut or truth
rewind. Actual feeder_position is a separate physical movement.

The current ten-family implementation/handoff matrix is in the [core design](../docs/architecture/PERSON_A_CORE_DESIGN.md).
fixtures/five_domain_contract.json is privileged A/B evaluator input, includes
all five-domain checks through the real core and an unusual valid1000µg/m³
concentration (1mg/m³ expressed in the declared unit); it carries no trust verdict.
It is not B's Test Book trigger, C's public payload or whole-layer evidence.

Private local example (PYTHONPATH=person_a/src):

```python
from city_core import build_city
core = build_city("r-air").value
held = core.observations.read_private("r-air").value
after = core.lifecycle.advance_one("r-air", 0).value
assert after.physical_state.air["Z-INDUSTRIAL"].pm25_ug_m3 == 10.2
assert after.physical_state.air["Z-RESIDENTIAL"].pm25_ug_m3 == 10.05
assert held.physical_state.air["Z-INDUSTRIAL"].pm25_ug_m3 == 10
assert after.power.hospital_powered
assert after.access.reachable_by_origin["J-D1"]
```

All ten official families retain an explicit A enabling path or B/C-only allocation.
Actual cameras/probes/meters/monitors/call feeds, actuator faults/controllers,
public authentication/Guardian verdict gates/quarantine and whole-Twin R1/R2/R3
remain B/C/triplet obligations with compatibility UNVERIFIED. Phase09 is authorized; Phase10 needs separate authorization and real B/C artifacts.

## Phase09 private capture and verified replay

Only the composition owner/evaluator uses `city_core.replay`:

- `export_replay(lifecycle, run_id) -> Result[ReplayRecording]` captures a complete
  committed prefix under A's existing writer lock. The frozen recording reuses the
  existing ReplayHeader and tuple of PrivateTransitions, final context/digest and
  small format/profile/runtime metadata. No second event store or source mutation.
- `replay_recording(payload:dict, new_run_id) -> Result[CityCore]` accepts
  `plain(recording)` or its JSON roundtrip. It validates strict shapes/versions/
  identities/order/counts, initializes an isolated real core and verifies every
  generated transition. Success returns that privileged evaluation core; failure
  returns no partially replayed core and never changes an active source run.

Recording keys: format_version, model_profile, python_version, numeric_profile,
randomness, header, records, final_context, final_digest. Current private format
pa-replay-0.1, implemented profile pa-core-09-v1, numeric profile binary64-exact-v1,
Python version exactly the capturing runtime, randomness="none". The physical
core has no random generator, so no seed/stream is required or invented; future
randomness needs a new reviewed recording profile. Physical/trace semantic changes
must update the private profile and reconcile compatibility, not reuse it silently.

The header retains exact model/catalog/contract versions, initial config and its
digest, catalog digest and initial context. Each existing record retains commit
index/context/time/revision/cause/full input/effect ID/sequence/transition ID and
before/after physical digests. Final revision must equal record count; final
tick/time/run/digest must agree with the full sequence. Limits reuse100000ticks,
100000effects and200000records. This is a complete recording at the captured cut;
later live commits do not change it. Export before reset if that evidence is needed.

Replay substitutes only run_id consistently, retaining ordered effects, their IDs,
correlations and every physical value. Every actual transition and digest must
match; same-time effects keep commit order. Existing canonical numeric equivalence
(finite int/float and object key order) is retained. Wrong runtime/profile/versions
reject UNSUPPORTED_VERSION; invalid/incomplete shapes/order reject INVALID_ARGUMENT;
wrong input run rejects OLD_RUN; accepted-budget excess rejects MODEL_LIMIT;
well-shaped but divergent header/transition/final state rejects REPLAY_MISMATCH.
Other validly shaped but illegal effects keep their existing core error codes.
All helper failures have safe_message="Replay rejected", retryable=false and
context=null, without private input or partial core in the error.

Exact digest equality is the same-runtime replay criterion, separate from the
1e-9absolute+1e-9relative tolerance for independent physical checkpoints. An exact
Python-version stamp is necessary, not a promise across operating systems/builds;
different numerical results explicitly fail comparison. No wall timestamps enter
the simulation record. B noise/scenarios/commands, C delivery and live Guardian/Brain
decisions are outside this deterministic envelope. No durable crash recovery,
live-target restore, persistence service, transport reconnect or full-triplet replay.

Recording checks establish consistency, not authentication: a coordinated rewrite
of inputs and all matching metadata can describe another valid run. No signatures
or secret evidence credentials are invented. The owner must protect private records;
C ordinary responses/logs/UI/ADT receive B-reported evidence plus allowlisted
context/correlation tokens, never this config/true state/digests/driver payload.
The metadata handle has no export/replay writer or private reference. Replay-created
cores are isolated evaluation copies, not extra persistent B/C city authorities.

Private local example (same PYTHONPATH setting):

```python
from city_core import build_city
from city_core.replay import export_replay, replay_recording
from city_core.transitions import plain
core = build_city("r-record").value
assert core.lifecycle.advance_one("r-record", 0).ok
recording = export_replay(core.lifecycle, "r-record")
assert recording.ok
result = replay_recording(plain(recording.value), "r-replay")
assert result.ok
assert result.value.observations.read_private("r-replay").value.physical_state == core.observations.read_private("r-record").value.physical_state
assert recording.value.final_context.run_id == "r-record"
```

Private fixtures/replay_normal_recording.json and replay_flood_recording.json were
captured from the accepted Phase08 core before this helper was implemented.
replay_contract.json holds independent water/power/access/queue/resource checkpoints.
Tests also change direct rain in a separately valid run: flood-chain final surface
199.4→198.2m³ and ambulance timer47→44s; unchanged old hashes reject. Reset/recapture
matches a fresh replay using existing owner reset, without adding a restore API.

One untraced local observation (Python3.12.14, Windows/AMD64,512steps/277095JSONbytes):
evolution0.213s, capture0.00023s, replay0.321s. These are measurements, not guarantees
or organizer targets; no bottleneck justified a production optimization.
Phase10 needs supervisor authorization plus real B/C artifacts/agreements for its
actual integration gate. Current replay proof is A-only and provisional.


## Phase10 private integration preflight

Run from the workspace root, with no installation or PYTHONPATH setup:

```powershell
& 'C:/Users/anasb/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe' -X utf8 person_a/tools/check_handoff.py
```

This emits **PRIVATE_A_EVALUATOR_ONLY** JSON with actual water/flood facts, A
metadata/token allowlist checks, unsupported reported-value rejection, duplicate
and conflicting effects, stale delivery after default-baseline reset, and private
same-runtime replay. It compares a delivered valve closure with **no movement
submitted**; the latter does not emulate B's stuck valve or a command receipt.
The CLI exits nonzero on a failed internal assertion; zero means A preflight only.
It always marks real B/C compatibility and whole-Twin R1/R2/R3 UNVERIFIED and the
real Phase10 gate blocked. Its output contains ground truth; keep it out of public
partner logs/UI/ADT. No Guardian verdict, C authentication or B scenario is tested.
Core reset without an explicit configuration restores the packaged baseline, not
the preflight's customized initial state. Existing handles observe the new run.

Run all existing and new tests using `person_a/tools/run_checks.py --evidence
tmp/phase10/checks` with the Python executable above. Tool coverage is measured
separately from the unchanged core package; see [Phase10 report](../docs/architecture/phase-records/PHASE_10_REPORT.md).
