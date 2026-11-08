# Interfaces and Security

## Integration surfaces

The ASGI application in `src/twin_app.py` provides REST endpoints and mounts an MCP transport. The REST routes include:

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/sensors` | Read projected sensor readings |
| GET | `/api/v1/state` | Read projected city state |
| GET | `/api/v1/events` | Read events from a cursor |
| GET | `/api/v1/timeline` | Read the operator timeline |
| POST | `/api/v1/commands` | Submit an actuator command |
| POST | `/api/v1/isolate` | Request device isolation |
| POST | `/api/v1/restore` | Request device restoration |
| GET | `/health/live` | Liveness check |
| GET | `/health/ready` | Readiness check |

The MCP HTTP transport is mounted at `/mcp/`. Request schemas, error envelopes, tool names, and authorization rules are defined in `src/twin_interface/` and `src/twin_app.py`; consult those implementations for the exact current contract.

The fixture [`../fixtures/five_domain_contract.json`](../fixtures/five_domain_contract.json) is explicitly marked provisional and says cross-layer compatibility is unverified. Do not treat it as a finalized partner agreement.

## Security controls present in code

- The ASGI runtime requires separate, unique role tokens for Brain, Guardian, Operator, and Evaluator. Tokens must be configured outside source control; the example file contains placeholders that intentionally fail production checks.
- Credentials are accepted through Bearer authorization, not query parameters.
- Host and optional origin allowlists are required/configurable in ASGI settings.
- The facade applies role permissions, input validation, device checks, and command idempotency/safety checks.
- Dashboard operator actions use a separate local passphrase session; the public reported view is read-only.
- The dashboard HTTP bridge bounds concurrent connections and avoids logging private request payloads.
- Public projections are designed to omit private sensor ground truth and internal diagnostic fields.

These statements describe implementation intent and code paths; they are not a security certification or a substitute for deployment-specific review.

## Deployment cautions

Use long, unique role tokens and exact allowed hostnames. Keep `.env` private. Do not expose the local dashboard or API directly to an untrusted network without a reviewed deployment boundary, TLS termination, and appropriate network controls. See [Deployment](DEPLOYMENT.md) for the repository's Compose settings.
