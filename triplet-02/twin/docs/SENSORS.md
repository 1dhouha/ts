# PDF-only live sensor inventory — 29 October 2026

[APPROVED TEAM DESIGN] Supervisor request: make the sensors in the previously
supplied file the only working sensors, replacing the others. This authorizes the
bounded local model/registry/dashboard change and supersedes the earlier prohibition
on the missing sensor physics for this purpose. It does not approve new partner
interfaces, cloud, installation, Git operations or teammate production changes.

Source: all ten pages of [Twin-Layer-Sensor-Specification.pdf](sources/Twin-Layer-Sensor-Specification.pdf), SHA-256
`4f8b9e2b4d1bc4f03248073ca383811372185c0d7a78d5750bcba0ef8963576d`.
Initial review: actual independent agent `/root/sensor_inventory_review` inspected
source/model/UI and identified legacy auto-registration and absent physical sources.

## Scope and acceptance

- Exactly the sixteen PDF sensors are active on a fresh runtime. Legacy S-* IDs
  produce no readings and reject operations. Preserve the historical core catalogue,
  original tests, ready package, geometry and Phase 0 artifacts.
- Preserve the PDF units, ranges, intervals, links and backup pairs. Every healthy
  sensor produces numeric truth/report at its first due tick (5, 10 or 60).
- Supply deterministic, bounded harbour, cooling, drainage, ambient and residential
  state in the existing LiveCity owner, stepping once after each core commit.
  Port is a separate storage, never a River alias. Existing traffic sources remain.
- Rain/power conditions have causal effects; faults change reporting only. Backups
  share physical truth and retain independent faults. No automatic failover policy.
- Reset clears physical extension/fault/history state. Model version is explicit;
  original core replay excludes this new extension, seeded live repeat covers it.
- PDF pp.2/10 replacement semantics remain: sixteen active positions, same-family
  replacements, retired history, no ID reuse. No legacy/unrelated new families.
- Update live demo, operator sensor controls, map indicators and current guidance.
  Privileged model/truth stays absent from ordinary reported output.
- Test-first important behavior; core/live/dashboard/real-browser checks and >=80%
  executable-line coverage. One independent combined reviewer, at least two rounds,
  frozen SHA-256 content identities, findings/fixes/retest in engineering review log.

## Model decisions

[RECOMMENDATION] Small lumped models use one-second water volume balances and a
first-order thermal balance, with explicit parameters, independent storages and
power dependencies. These are illustrative simulation parameters, not calibrated
hydraulics or an organizer-mandated model. Existing rain and feeder conditions drive
them; no independent random measurement generation. New eight-actuator command
semantics remain a separate task; linked IDs are preserved metadata.

[OFFICIAL REQUIREMENT] The retained original brief p.1 lists traffic cameras,
power-load, air-quality and emergency-call feeds in addition to water. This
supervisor-selected local inventory does not cover those original families.
Phase 1 p.1 still describes these city domains. Follow the requested inventory and
record this coverage gap; do not claim complete Twin/triplet compliance. Actual B/C
agreement and external interoperability remain unverified.

## Implemented model and migration

Runtime owns immutable `SensorPhysics` (`pdf-city-physics-1`) beside its unchanged
core state. It reads committed rain and feeder availability and advances exactly
once per tick before sampling. Measurement faults never feed into this state.
Sensor values are rounded to two decimals, with reported values clamped to PDF
hard limits; privileged truth/diagnostics remain unclamped for saturation evidence.

| Quantity | Local physical rule / declared parameters |
| --- | --- |
| Harbour level, primary outlet flow, header pressure | Independent 1000 m² storage, initial 500 m³ (50 cm). Inflow 0.06 m³/s plus rain over 10000 m². Automatic primary discharge 0.06 + 0.002×(volume−500) m³/s, bounded 0–0.25 and available water, disabled without F-URBAN power. Header pressure 2 + 4×flow/0.25 bar while pumping; zero stopped. Backup level samples the same storage independently. No second-pump capacity or automatic failover is invented. |
| Downtown storm drain | Independent 100 m² storage, initial 20 m³ (20 cm); rainfall over 3000 m² enters it; gravity discharge 0.04×sqrt(volume/20) m³/s, bounded by available water. This separate drain catchment does not subtract/duplicate transfers from the core flood storage. |
| Downtown ambient | 20 + 5×sin(2π×UTC simulated time of day/86400) °C; uses full epoch hour/minute/second and ticks, no wall clock or random values. |
| Industrial process temperature, coolant flow/pressure | Initial process 65 °C; F-IND-powered coolant is 60 L/s at 4 bar, otherwise zero. Temperature adds 0.03 °C/s powered process heat (0.002 residual when off), subtracting (0.0005×flow/60 + 0.00005)×(process−ambient) °C/s. Backup temperature samples this same process. |
| Residential reservoir, network flow/pressure | Independent 100 m² reservoir, initial 200 m³ (200 cm). Demand 0.025 + 0.005×sin(day phase) m³/s. F-URBAN powers make-up supply 0.025 + 0.001×(200−volume), bounded 0–0.06 m³/s. Powered delivery is demand bounded by available water; unpowered delivery is zero. Pressure is 4×sqrt(volume/200) bar while delivering, zero otherwise. |
| Three traffic counters | Unchanged approved R-LOW/R-DOWN/R-LINK completed-departure 10-second average ×60, veh/min. |

Water volumes use next = previous + inflow − outflow for each one-second step.
Power inputs are availability dependencies, not additional loads on the frozen core
power budget. The extension is a local model, not a new physical topology/transport
contract for B/C. Old river pump/valve commands still affect their original assets;
new linked actuator command APIs remain outside this sensor-only change.

Fresh active inventory is exactly the PDF's sixteen IDs. Compatible replacements
follow PDF pp.2/10: new ID in the same zone/type, sixteen active slots, immutable
retired history and redirected active backup references; no reuse. Reset preserves
registration history but restores physical/fault/buffer state. Process restart starts
a fresh registry; durable cross-process registration remains unimplemented.

Reading schema advances to `twin-reading-0.2-local`; registry version is
`sensor-inventory-2026-10-29-v2`. Migrate legacy S-* selections/examples to PDF IDs.
The original core and ready package remain unchanged historical artifacts. Existing
D01–D07 checks prove their original scope; this new revision needs the new evidence.

## Verification record

Original workspace evidence was kept in `tmp/pdf_inventory/`. See [verification](SENSOR_VERIFICATION.md) for results and portable commands.
Initial four new integration tests actually ran RED: extra 13 legacy sensors,
unbound/null Port sources, and 29 rather than 16 active slots. After implementation,
all four passed. Additional replacement regression ran RED on a stale backup link;
active backup references were redirected and the regression was rerun.

Final checks, independent reviews and frozen identities are recorded in the rolling
[verification record](SENSOR_VERIFICATION.md); completion is
engineering evidence and does not constitute supervisor acceptance or B/C sign-off.


Operator map markers/asset sensor entries follow the active registry. The ordinary
reported view preserves historical observations and draws no sensor markers,
because retained readings cannot identify which devices have been retired.
