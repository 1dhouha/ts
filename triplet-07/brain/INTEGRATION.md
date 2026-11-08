# Brain, Twin, and Guardian integration

The integrated local stack is defined by `../docker-compose.yml`. It runs the production
Twin REST/MCP ASGI app, Guardian's Twin polling profile, and Brain in external-layer mode.
No layer source code or layer workflow is changed by this setup.

## Start the stack

From `triplet-07/`, a fresh clone starts with the jury/demo credentials built into the
loopback-only Compose configuration. The jury can start and build all three services with:

```powershell
docker compose up --build
```

Endpoints are Brain `http://localhost:8300`, Twin REST/MCP `http://localhost:8000`, and
Guardian `http://localhost:8002`. The Twin and Guardian ports are bound to loopback for local
use. The Twin dashboard passphrase for this local demo is `jury-city-dashboard-2026`.
Compose waits for Twin readiness before starting Guardian, and Guardian health before starting
Brain. These defaults are for a local jury demo only; deployments should supply private secrets
through an uncommitted `.env` file, which overrides the defaults.

## Wiring and data path

- Brain is configured with `SIMULATOR=false`, `MOCK_MODE=false`, `TWIN_TRANSPORT=rest`,
  and polls Twin at `http://twin:8000`.
- Guardian runs with `GUARDIAN_PROFILE=twin`, polls that same Twin at
  `http://twin:8000/api/v1/sensors`, and uses the Twin Guardian-role credential for polling
  and containment calls.
- Brain looks up Guardian verdicts at `http://guardian:8002` with the scoped read token.
  Its optional shared-timeline writes use the separately scoped timeline token.
- Docker service names (`twin`, `guardian`) are used inside the Compose network. Host-side
  clients use `localhost`.

## Sensitive integration boundary

The credentials connect the three services and define their caller roles. Twin credentials
must match on both Twin and the calling layer. Guardian's scoped tokens limit Brain to verdict
reads and timeline writes; the Guardian admin token is not given to Brain. Do not enable
`GUARDIAN_DEMO` or Twin's insecure development tokens in this stack.

Brain currently adds `X-CityBrain-*` HMAC headers to Twin command requests, but the present
Twin ASGI interface authorizes requests with its Bearer role tokens and does not verify those
Brain HMAC headers. The integration relies on Twin's Bearer role authorization; it must not be
described as HMAC-verified. Changing that behavior requires a separate, coordinated contract
change in Twin and Brain, outside this integration-only update.

## What this integration does not do

Compose starts the layers and their link pollers. Brain's standalone `/scenarios/run`
command still does not control the production Twin. Start a live scenario from the Twin dashboard
at `/`, which uses the same Twin runtime that REST/MCP, Brain, and Guardian use. The authenticated
`POST /api/operator/scenario` route is the scenario trigger; no Brain or Guardian code path is added.

The existing root `RUN_ALL_SCENARIOS.cmd` runs Brain's built-in simulator scenarios only. It
is not an end-to-end test for the production Twin/Guardian stack.

## Validation after startup

1. Check `http://localhost:8000/health/ready` and `http://localhost:8002/health`.
2. Check the Brain dashboard/API on port 8300 and confirm it reports live layers rather than
   simulator mode.
3. Sign in to the Twin dashboard and start T5. Inspect Twin readings, Guardian verdicts, and Brain
   decisions for the same current Twin run. T5 has been exercised end to end in an isolated Compose
   stack; repeat the check in the target deployment.
4. Verify a wrong Twin token is rejected and Brain can read verdicts but cannot use Guardian's
   admin-only actions.

The verified T5 flow covers spoofed evidence rejection and zero Brain actuator dispatch. Other
scenarios and normal trusted actuator responses still need separate end-to-end verification.
Brain also runs in memory-only audit mode unless its optional database service
is configured; that does not prevent polling or verdict checks.
