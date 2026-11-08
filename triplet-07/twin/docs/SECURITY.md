# Twin Layer Security Model

> **Scope:** public boundary of Layer 1 (Digital Twin). The simulation core is
> private; Brain and Guardian are untrusted external clients.

## 1. Assets and trust boundaries

| Asset | Sensitivity | Who may see it |
| :--- | :--- | :--- |
| Physical ground truth (`actual_value`, `delta`, `fault`, drivers) | Private | Evaluator only |
| Public telemetry (`reported_value`, `status`, `unit`) | Public to authenticated roles | All four roles |
| Actuator effect execution | Privileged | Brain, Operator |
| Quarantine registry | Privileged | Guardian, Operator |
| Audit JSONL log | Public-safe by construction | Everyone (no secrets) |
| Bearer tokens | Secret | The owning role only |

Trust boundary: **FastMCP and REST are both untrusted transport adapters**. All
authorization, projection and quarantine logic lives in `TwinFacade`, so a new
transport cannot bypass the rules.

## 2. Authentication

* Tokens are read from `TWIN_TOKEN_BRAIN`, `TWIN_TOKEN_GUARDIAN`,
  `TWIN_TOKEN_OPERATOR`, `TWIN_TOKEN_EVALUATOR`.
* Comparison uses `hmac.compare_digest` and checks every role, avoiding early
  exit timing leaks (`src/twin_interface/auth.py`).
* Both raw tokens and `Authorization: Bearer <token>` forms are accepted.
* Development defaults exist only when `TWIN_ALLOW_INSECURE_DEV_TOKENS=1`.
  **Never set this flag in production.**
* `missing_token_roles()` is available as a startup health check; a deployment
  with missing role tokens should refuse to start or log a critical warning.

## 3. Authorization matrix

Defined once in `src/twin_interface/permissions.py` and enforced by every Façade
entry point. Unknown tools are denied by default.

| Tool | Brain | Guardian | Operator | Evaluator |
| :--- | :---: | :---: | :---: | :---: |
| Observation tools | ✅ | ✅ | ✅ | ✅ |
| `send_actuator_command` | ✅ | — | ✅ | — |
| `isolate_device` / `restore_device` | — | ✅ | ✅ | — |

## 4. Ground-truth non-leakage

`src/twin_interface/projections.py` implements an **allow-list**, not a
deny-list, for public roles:

* Allowed reading keys: `sensor_id`, `zone_id`, `type`, `unit`, `timestamp`,
  `sim_tick`, `seq`, `reported_value`, `status`, `available`, `backup_ids`,
  `linked_device_ids`.
* Forbidden keys are also stripped recursively from events and nested payloads:
  `actual_value`, `true_value`, `true_level`, `sensor_value`, `delta`, `fault`,
  `noise`, `drift_rate`, `lag_ticks`, `bias`, `ground_truth`.
* The JSONL logger applies the same projection before writing
  (`src/twin_observability/scenario_log.py`).
* `tests/security/test_leakage.py` recursively scans responses and logs and
  asserts zero forbidden keys for Brain/Guardian.

## 5. Quarantine enforcement

`isolate_device` writes to a Façade-owned registry. Every subsequent
`send_actuator_command` checks it first and returns `GUARDIAN_RESTRICTED` with
the isolation reason. Quarantined sensors are reported as `OFFLINE` with
`available=false`. Only Guardian/Operator may restore a device.

The Twin does **not** enforce "Guardian verdict before Brain command"; it only
enforces the quarantine flag. This keeps the contract simple and auditable.

## 6. Idempotency and replay safety

`request_id` is the idempotency key. A duplicate request returns the cached
outcome and does not mutate physics again. Reusing a key with different
parameters is rejected with `DUPLICATE_CONFLICT`. This protects the stage demo
from network retries and accidental double dispatch.

## 7. Audit and monitoring

* Every applied command, quarantine and restore is appended to the in-memory
  ring buffer and to `logs/{scenario}_{run_id}.jsonl`.
* Writes are flushed and `fsync`-ed per line for crash resilience [R2].
* Sequence numbers are monotonic and thread-safe (`src/twin_observability/seq.py`).
* The timeline formatter only consumes sanitized events [R3].

## 8. CI security gates

`.github/workflows/ci.yml` runs on every push/PR:

1. `python -m compileall src mocks tools` — syntax gate.
2. `python tools/run_checks.py --evidence tmp/ci` — full unittest suite,
   zero skips, city-core coverage ≥ 80%, and the leakage suite.
3. `python tools/run_standalone.py --run-id ci-standalone` — R1 proof that the
   Twin + mocks work end to end.

## 9. Known limitations

* In-memory quarantine and idempotency caches are per-process; a restart clears
  them. Persistent multi-process coordination is out of scope for the competition
  build.
* Tokens are static per role; rotation requires a restart.
* The local dashboard is a development tool, not a hardened internet-facing
  service. Bind it to localhost for the stage demo.
