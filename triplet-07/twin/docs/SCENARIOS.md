# Scenario Catalog

Scenario definitions are JSON files in [`../scenarios/`](../scenarios/). They are deterministic software fixtures; their values are not field telemetry. Expected behavior and limits below are summarized from the scenario definitions.

| ID | Demonstration | What the Twin can show | Important boundary |
|---|---|---|---|
| `t5` / SCN-01 | Fake flood reading from `PRT-WLV-02`, linked to `PRT-PMP-02` | The reported value disagrees with the simulated input while `PRT-WLV-01` remains the backup. Guardian may isolate the faulty sensor; Twin rejects a command to the linked pump while the evidence is untrusted. This does not quarantine the pump itself. | Guardian and Brain lifecycle entries in the campaign are controlled local calls, not partner services. |
| `scn-02` / SCN-02 | Seeded harbour-level fixture | Two seeded level readings and a pump-command check over a review window. | The fixture is not authentic field telemetry; check the actual campaign outcome before claiming a command was applied. |
| `scn-03` / SCN-03 | Failed and frozen sensors | Failed sensor output, a frozen traffic reading, backup reading, and fail-closed command behavior. | No autonomous Brain or Guardian decision is run. The unrelated backup does not clear the traffic command gate. |
| `scn-04` / SCN-04 | Guardian-owned actuator isolation boundary | Twin emits a pending marker and blocks a command while isolation is unresolved. | Twin does not call Guardian; the scenario has no sensor trigger; the two pump IDs share one physical target. It does not prove autonomous isolation or independent backup. |
| `scn-05` / SCN-05 | Seeded traffic and local air fixture | Scenario-local sensor values and fixture labeling. | It does not create a CityCore road obstruction or a registered air-quality sensor; no external partner is invoked. |
| `scn-06` / SCN-06 | Controlled interface availability loss/recovery | Twin role boundaries reject requests during injected unavailability and accept eligible requests after recovery. | This is local state injection, not an actual network outage test. There is no durable pending-command queue. |

## Running a scenario

From the `triplet-07/twin` directory:

```powershell
python scenarios/run.py --scenario scn-03
```

The scenario runner delegates to `tools/run_twin.py`. Use `--help` with the runner for supported options. To review campaign-level outcomes, see the dated campaign JSON linked from [Verification](VERIFICATION.md).
