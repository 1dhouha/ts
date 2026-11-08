# Portable documented effect example

Exact section 6 excerpt from the original Person A internal contract.
Source document SHA-256: `d1a6ae99fb37b985622813601fc819f2822a1e4190b53a31f719e387b8e4fd6d`.
This fixture makes the existing receipt-conformance test independent of the
parent planning workspace. Request, expected receipt and test assertions are
unchanged. This is an internal provisional contract, not teammate sign-off.

## 6. Example exchanges (documentation only)

JSON is an illustrative serialization of these semantic records, not MCP/HTTP messages. Initial context fixture run `r-a1`, catalog/model versions above, tick0/time0/revision0. Private water fixture uses design initial volumes; all examples are independent unless explicitly sequenced. Every apply request uses exactly the same field set.

Normal physical valve closure after B finishes actual motion:

```json
{"contract_version":"pa-internal-0.2-draft","run_id":"r-a1","effect_id":"eff-17-1","expected_revision":0,"source":"B_ACTUATOR","correlation_id":"flow-17","effect":{"tag":"valve_position","target_id":"V-12","opening_fraction":0.0}}
```

Success value (inside Result success tag):

```json
{"context":{"run_id":"r-a1","tick":0,"sim_time_ms":0,"revision":1,"model_version":"pa-model-0.2-draft","catalog_version":"pa-catalog-0.1-draft","contract_version":"pa-internal-0.2-draft"},"effect_id":"eff-17-1","sequence":0,"outcome":"APPLIED","duplicate":false,"correlation_id":"flow-17"}
```

Retry identical request after receipt loss: same receipt context/revision1/sequence0, duplicate=true, no second effect even if current physical revision later advanced. Same effect_id with opening_fraction1 produces IDENTITY_CONFLICT. New effect_id with expected_revision0 after the successful closure produces STATE_CONFLICT. Negative opening or unknown target returns INVALID_ARGUMENT or UNKNOWN_TARGET without changing state. Source B_SCENARIO on an actuator-only handle returns FORBIDDEN_CAPABILITY even for an otherwise duplicate request.

Stuck valve: B command `cmd-17` can have requested_setting0 and reported_setting0 while actual opening remains1. B sends no successful closure effect if it did not move. Optional actual unchanged-position feedback uses a distinct deliberate effect only if B's agreement needs it; a physical no-op receipt still proves no objective. A continues inlet0.6. C receives B report and independent B flow/level feedback, never this privileged state. Guardian evaluates evidence; A does not generate a “stuck” label.

False reading: B reads read_private("r-a1"), generates an observation with its own event ID, and may report a different value under its fault model. It never calls an effect with `sensor_value` or `true_level`: those tags/fields are invalid. read_metadata returns Context only. This makes the mock/real substitution privacy assertion concrete.

Private reservation example, at independently initialized revision0, home J-H1 to J-D1:

```json
{"contract_version":"pa-internal-0.2-draft","run_id":"r-a1","effect_id":"eff-18-1","expected_revision":0,"source":"B_ACTUATOR","correlation_id":"flow-18","effect":{"tag":"reserve_responder","target_id":"AMB-01","mission_ref":"mission-18","destination_node_id":"J-D1","route_road_ids":["R-LOW"]}}
```

If LOW is flooded the request is ROUTE_UNAVAILABLE, no allocation. A connected alternate is R-HIGH → R-LINK → R-DOWN if all usable. A second different effect for occupied AMB-01 returns RESOURCE_UNAVAILABLE. On arrival, release requires mission-18 and status arrived; no automatic hospital-home teleport. One dispatcher does not authorize unlimited resources.

A complete tagged failure example for an otherwise well-formed effect request with an unsupported contract revision:

```json
{"ok":false,"error":{"code":"UNSUPPORTED_VERSION","safe_message":"Unsupported contract revision","retryable":false,"context":{"run_id":"r-a1","tick":0,"sim_time_ms":0,"revision":0,"model_version":"pa-model-0.2-draft","catalog_version":"pa-catalog-0.1-draft","contract_version":"pa-internal-0.2-draft"}}}
```

No world/revision/sequence/run-ID change occurs. Malformed numeric version fields instead return INVALID_ARGUMENT. For pre-initialization errors context is null; no raw configuration/driver values appear in either failure.

Private pagination example: after initialization, read_private_transitions(r-a1,-1,2) returns the exact ReplayHeader, empty records, next_after_index=-1, at_end=true. After an effect (revision1) and step (revision2), limit1 from -1 returns record_index0/EFFECT including the complete request, next_after_index0 and at_end=false; the next page from0 returns record_index1/STEP with StepInput, next_after_index1 and at_end=true. Reading again from1 returns an empty page/header and unchanged cursor1. Cursor2 is CURSOR_INVALID; the same cursor/run after reset is OLD_RUN. Each page is privileged and cannot be serialized into C ordinary logs.

One step: `advance_one("r-a1", expected_revision=0)` on untouched fixture returns snapshot tick1/time1000/revision1. A subsequent old-revision step is STATE_CONFLICT. Reset: `reset("r-a1", expected_revision=1, new_run_id="r-a2", initial_config=<same valid fixture>)` returns READY context tick0/time0/revision0 with new run, baseline physical state, empty A ledger/records; a delayed eff-17-1 still addressing r-a1 is OLD_RUN. No API syntax in this example is an executed result.

Permitted C metadata/example token:

```json
{"context":{"run_id":"r-a1","tick":1,"sim_time_ms":1000,"revision":1,"model_version":"pa-model-0.2-draft","catalog_version":"pa-catalog-0.1-draft","contract_version":"pa-internal-0.2-draft"},"correlation_id":"flow-17","token_id":"ev-001"}
```

The token says when C bound a report to the core context, not that the reading is true, that a command executed, or that Guardian approved it. C owns observation/verdict/decision/command ID binding; string copies cannot establish authority.


## 7. End of excerpt
