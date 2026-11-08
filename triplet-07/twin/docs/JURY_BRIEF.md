# Twin Layer — Jury Brief

## Project summary

The Twin Layer provides a simulated city environment that exposes sensor observations, city state, events, and controlled actuator operations to the wider City Brain system. It is designed to make cross-layer behavior observable and repeatable while enforcing a defined interface and safety boundary.

The repository includes a live city runtime, a browser dashboard, scenario fixtures, REST and MCP interfaces, persistence for selected city state and command receipts, and automated checks. The simulation is suitable for demonstrations and software integration testing; it is not connected to real city infrastructure.

## What the Twin contributes

- A deterministic, inspectable environment for traffic, water, power, air, and emergency-response concepts.
- Sensor simulation with seeded readings and injected faults, including frozen, failed, and misleading readings.
- Scenario-driven demonstrations with event timelines and correlation identifiers.
- A role-aware interface for reading observations and submitting validated commands.
- Guardrails for rejected or pending operations, device restrictions, and duplicate command requests.
- A dashboard for reviewing reported city state, sensors, scenarios, and command outcomes.

## Demonstrated results

The saved integration campaign at [`../test-evidence/scenario-campaign-20261108T074306.489418Z-f1e94dce/campaign.json`](../test-evidence/scenario-campaign-20261108T074306.489418Z-f1e94dce/campaign.json) records PASS for each of the six individual scenario runs, the sequential scenario campaign, and cross-scenario checks. Its failure-recovery section is **BLOCKED**, so the work does not claim that the complete recovery campaign passed.

The saved validation snapshot at [`../test-evidence/validation-20261107T175933Z/checks.json`](../test-evidence/validation-20261107T175933Z/checks.json) reports 127 tests, zero failures, zero errors, zero skips, and `passed: true` on Python 3.12.16. This snapshot is dated November 7, 2026; rerun checks on the final submitted revision before presenting it as current.

## Honest integration boundary

The campaign itself records that no Brain or Guardian service/client is present in this repository and that its role calls are controlled local `TwinFacade` test calls. It also records that Kafka is not configured. Therefore:

- The evidence demonstrates Twin behavior and its local role boundary; it does not demonstrate live Brain or Guardian service integration.
- Scenario fixtures are simulated data, not authentic field measurements.
- SCN-04 does not demonstrate an autonomous Guardian decision or a safe independent backup pump; both configured pump IDs map to the same physical target.
- The recovery campaign remains blocked, and runtime scenario/sensor state and event-store history are not all durable across process restart.

These limits are documented so a reviewer can distinguish implemented behavior from planned integration work.

## Suggested jury walkthrough

1. Open the dashboard using the steps in [Demo Guide](DEMO_GUIDE.md).
2. Run SCN-03 to show frozen and failed sensor behavior and a command rejected while the Guardian check is pending.
3. Show the event timeline and trace the scenario using its correlation ID.
4. Open the saved campaign JSON and point out the checks that passed and the recovery section marked BLOCKED.
5. Explain which components are local simulation and which external partner services are not connected.

## Technical references

- [Architecture](ARCHITECTURE.md)
- [Scenario catalog](SCENARIOS.md)
- [Verification and evidence](VERIFICATION.md)
- [Interfaces and security](INTERFACES_AND_SECURITY.md)
- [Deployment](DEPLOYMENT.md)
