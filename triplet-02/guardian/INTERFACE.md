# Guardian: Interface Card (v0.5, to agree with the Twin and City Brain teams)

## Receives
**Observation** (from the Twin, `POST /observations` or pulled from its tools)
```json
{"observation_id":"OBS-000228","device_id":"WL-03","sensor_type":"water_level","zone":"canal-north",
 "value":30,"unit":"cm","timestamp":"2026-10-16T10:02:00Z","seq":1841}
```
**Verdict request** (from City Brain): `GET /verdicts/{observation_id}` and `GET /devices/{device_id}/trust`

## Sends
**Trust verdict** (to City Brain)
```json
{"verdict_id":"VRD-000452","observation_id":"OBS-000228","device_id":"WL-03","verdict":"UNTRUSTED",
 "trust_score":0.21,"risk_level":"HIGH","reason_codes":["CROSS_SENSOR_CONTRADICTION"],
 "recommended_action":"QUARANTINE","issued_at":"2026-10-16T10:02:05Z"}
```
**Containment calls** (to the Twin): `POST <twin_url><path>` with JSON `{"device_id","reason"}` for `quarantine_device` and `isolate_sensor`,
`{"observation_id"}` for `rollback_reading`. Paths are in `config.yaml` (`containment.paths`); names to be confirmed with the Twin team.
Separate bearer token from the verdict API (`GUARDIAN_CONTAINMENT_TOKEN`). Failed calls are retried and logged.

## Other ways in and out
- `POST /ingest/{partner}`: send the partner's own payload (JSON, XML, or a JSON string holding XML). The format adapter maps fields
  and units using `adapters.partners.<partner>` in `config.yaml`. Unusable payloads return 422 with a clear `error.code`
  (MISSING_FIELD, INVALID_VALUE, INVALID_TIMESTAMP, UNSUPPORTED_FORMAT, XML_ENTITY_FORBIDDEN...) and an UNTRUSTED verdict (`MALFORMED_PAYLOAD`).
- `POST /trust/verdict` with `{"observation_id": "..."}`: same answer as `GET /verdicts/{observation_id}`.
- Timeline event (JSON): `{"timestamp": "...Z", "layer": "guardian", "event": "verdict", "observation_id": "...", "verdict": "UNTRUSTED"}`.

## Timeline (R3): one screen for the whole triplet
`POST /timeline/events` with JSON `{"timestamp":"...Z","layer":"twin|brain|guardian","event":"reading","observation_id":"OBS-1", ...any fields}`.
`GET /timeline/events?since=<id>` returns events after an id. The screen is `GET /timeline`. Same bearer token as the verdict API.

## Errors
`404 {"error":{"code":"OBSERVATION_UNKNOWN","message":"..."}}`, `401 UNAUTHORIZED`, `413 PAYLOAD_TOO_LARGE` (body over 64 KB),
`422` for malformed payloads, `503` when Guardian cannot answer (the caller must treat it as UNTRUSTED).

## Reason codes in a verdict (`reason_codes`)
| Code | Meaning | Trust of the claimed device |
|---|---|---|
| `OUT_OF_RANGE`, `IMPLAUSIBLE_RATE`, `FROZEN_VALUE`, `CROSS_SENSOR_CONTRADICTION` | The data itself looks false | lowered |
| `DEVICE_QUARANTINED` | The device is quarantined, its data is not used | n/a |
| `REPLAY_OR_OUT_OF_ORDER` | Old or repeated message | **unchanged** |
| `FUTURE_TIMESTAMP` | Timestamp more than 5 s ahead of the clock (it would break ordering for the real sensor) | **unchanged** |
| `MESSAGE_FLOOD` | More than 20 messages from one device in 1 s | **unchanged** |
| `BACKUP_DISAGREEMENT` | Two redundant sensors disagree and this one moved away from its own history (with two sensors there is no majority, so the culprit is the one that moved; if unclear both are distrusted) | lowered |
| `SLOW_DRIFT` | Smoothed gap to the peer median or backup stayed above the drift tolerance for several readings in a row (tiny steps that each pass the rate check) | lowered |
| `SENSOR_FAILED` | The device itself reports FAILED / no value: stream unusable, quarantined so the Brain uses its backup | quarantined |
| `UNKNOWN_DEVICE` | Device not allowed to join (registry mode, device or zone cap) | no state created |
| `MALFORMED_PAYLOAD` + detail | Could not be normalized (see 422 codes above) | unchanged |

"Unchanged" is deliberate: the sender of a replay, a flood or a forged timestamp can be anyone, so punishing the device
it claims to be would let an attacker frame a healthy sensor. Such messages still get `UNTRUSTED` and action `MONITOR`.
Device ids must match `^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$`.

## Pointing Guardian at a different partner
`config.yaml` -> `partners.twin_url`, `partners.brain_url`. Nothing is hard-coded.

## Authentication
`Authorization: Bearer <token>`; the token is read from the env var named in `config.yaml` (`GUARDIAN_TOKEN`).
**Guardian refuses to start without a token** (fail closed). `GUARDIAN_DEMO=1` is the explicit, logged opt-out for a local demo.
The same token protects `/timeline/events` and `/scenarios`, so nobody can write fake events onto the shared screen.
Containment to the Twin uses a separate token (`GUARDIAN_CONTAINMENT_TOKEN`) from anything City Brain holds.
Open the live screen with `http://<host>:8002/timeline?token=<token>`.
Not implemented: TLS. On a shared network, put a TLS reverse proxy in front.

## Guardian -> Twin team's REST contract (twin-interface-0.1)
Guardian is a client of the Twin. It uses only its `guardian` token, sent in the `Authorization` header (never in the URL).
| Guardian needs | Twin endpoint | Notes |
|---|---|---|
| Read readings | `GET /api/v1/sensors` | polled every second; new samples only (same `seq` or `sim_tick` is skipped) |
| Quarantine / isolate a sensor | `POST /api/v1/isolate` `{"device_id","reason"}` | `reason` is a reason code, 200 characters at most |
| Restore a sensor | `POST /api/v1/restore` `{"device_id"}` | after a manual restore the sensor starts on probation again |
| Rollback a reading | none | the Twin has no such tool; Guardian logs `containment_unsupported` |
Field mapping (`config.twin.yaml`): `sensor_id->device_id`, `zone_id->zone`, `type->sensor_type`, `reported_value->value`, `seq` and `sim_tick`
for ordering, `backup_ids` for the redundant-pair check. Their timestamps are not trusted: readings are stamped on arrival and ordered by `seq`.
Sensors reported `OFFLINE`/`available:false` are not judged (an outage or an isolation is not an attack). Sensors reporting `FAILED` are quarantined.
Operator endpoint on our side: `POST /devices/{id}/restore` (bearer token) restores a quarantined device here and at the Twin.
