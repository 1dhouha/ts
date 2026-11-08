# Twin Layer Documentation

This folder is the jury and reviewer guide for the Twin Layer. It explains the system, shows how to run a demonstration, and links claims to code and saved evidence.

## Start here

1. [Jury brief](JURY_BRIEF.md) — purpose, contribution, demonstrated results, and limitations.
2. [Demo guide](DEMO_GUIDE.md) — how to start the local dashboard and present a scenario.
3. [Architecture](ARCHITECTURE.md) — components, data flow, and boundaries between layers.
4. [Scenario catalog](SCENARIOS.md) — what each included scenario does and what it does not prove.
5. [Verification and evidence](VERIFICATION.md) — test commands and the saved evidence inventory.
6. [Interfaces and security](INTERFACES_AND_SECURITY.md) — REST/MCP surface, roles, and safety controls.
7. [Deployment](DEPLOYMENT.md) — local prerequisites and Docker configuration.

## Scope and evidence rule

The Twin is a deterministic software simulation and integration layer. Scenario fixtures and local role calls are useful for testing Twin behavior, but they are not field telemetry or proof that external Brain or Guardian services were integrated. Each result in the jury brief is tied to a dated artifact under [`../test-evidence/`](../test-evidence/).

Before a jury presentation, rerun the checks against the final submitted revision and update the evidence date and results in [Verification](VERIFICATION.md). Do not report a prior evidence snapshot as a result for newer code.
