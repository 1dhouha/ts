# Guardian: threat model and security measures (paste-ready for report Part 3)

Principle: **Guardian must never be the weak point.** It fails closed (unknown or unreachable means UNTRUSTED), explains every
verdict with a reason code, and never punishes a device because of a message that anybody could have forged.

| # | Attack | What an attacker gains | Guardian's defence | Reason code / response | Proof |
|---|---|---|---|---|---|
| 1 | Spoofed constant value (fake flood) | City Brain acts on a false level | Frozen-value check | `FROZEN_VALUE`, quarantine at +6 s | T2 |
| 2 | Spoofed value with noise | Same, evades the frozen check | Cross-sensor check against trusted peers (median) | `CROSS_SENSOR_CONTRADICTION`, +8 s | T2b, T5, T9 |
| 3 | Impossible or jumping values | Crash or confuse downstream logic | Range and rate-of-change checks | `OUT_OF_RANGE`, `IMPLAUSIBLE_RATE` | tests |
| 4 | Replay of old messages | Re-inject stale readings | Timestamp and sequence ordering; trust untouched | `REPLAY_OR_OUT_OF_ORDER` | T6, T9 |
| 5 | **Forged future timestamp** | Make every later real reading look like a replay (denial of service on one sensor) | Refuse timestamps more than `max_future_skew_s` ahead; ordering state untouched | `FUTURE_TIMESTAMP` | T12, `test_security.py` |
| 6 | **Message flood** | Saturate Guardian, hide an attack in noise | Per-device rate limit; refused messages do not change trust (no framing) | `MESSAGE_FLOOD`, `flood_detected` event | T10 |
| 7 | **Fake-device injection (Sybil)** | Outvote the real sensors in the peer check so they look like liars | Device registry (`strict` = only the Twin's known ids), device and per-zone caps, new devices cannot vote until they have history | `UNKNOWN_DEVICE` | T11 |
| 8 | Missing, wrong or guessed token | Read verdicts, write fake timeline events, feed data | Bearer token, constant-time compare, **refuses to start without a token**; same token on the timeline | `401 UNAUTHORIZED` | `test_security.py` |
| 9 | Malformed, hostile or oversized payload | Crash, memory exhaustion, log or URL injection | Strict id pattern, body limit (413), safe XML parser (`defusedxml`: no entities or DTD), unusable input is UNTRUSTED, never guessed | `MALFORMED_PAYLOAD`, `413` | adapter and security tests |
| 10 | Memory exhaustion by many ids, verdicts or failed containment calls | Crash Guardian | Caps on devices, per-zone devices, stored verdicts and the retry queue | `UNKNOWN_DEVICE`, `containment_queue_full` | `test_security.py` |
| 11 | Twin down during an attack | Attack proceeds uncontained | Verdict stays UNTRUSTED / QUARANTINE for City Brain; failed calls queued and retried | `containment_call ok=false` | containment tests |
| 12 | Guardian down | City Brain trusts unchecked data | Fail-safe: no verdict in time means UNTRUSTED (agreed deadline 300 ms plus one retry) | Brain `HOLD` | T8 |
| 13 | Sensor outage mistaken for attack | Needless quarantine | Silence is `STALE` (monitor), trust untouched | `device_stale` | T4 |
| 14 | Container compromise | Pivot to the host | Non-root user, read-only filesystem, all capabilities dropped, no-new-privileges, separate containment token | n/a | `Dockerfile`, `docker-compose.yml` |
| 15 | **Compromised sensor in a redundant pair** (the Twin's T5) | Brain acts on a fake 180 cm | Backup-pair check: culprit is the sensor that moved away from its own clean history; a healthy backup that agrees makes a fast change count as real (no false alarm on a real flash flood) | `BACKUP_DISAGREEMENT` | T13, T14, `test_twin_connector.py` |
| 16 | Acting on a reading that was just flagged | Brain opens a valve on a bad reading before the score drops | A reading that fails any check is UNTRUSTED itself, immediately | any reason code | all scenarios |
| 17 | **Hostile or broken Twin responses** | Crash, injection, memory use | Token only in the header, response size cap, strict envelope check, id pattern, strict device registry (their 16 sensors), `poll_error` events, Guardian stays up when the Twin is down | `poll_error`, `/health` | `test_twin_connector.py` |
| 18 | Twin restart replays old sequence numbers | Every reading looks like a replay | New `run_id` resets ordering state | `run_reset` | `test_twin_connector.py` |
| 19 | Sensor reports FAILED | Stream unusable | Quarantined, Brain uses the backup; an offline or isolated sensor is not judged | `SENSOR_FAILED`, `device_unavailable` | `test_twin_connector.py` |
| 20 | **Slow drift** (tiny steps that each pass the rate check) | Pull a value away from the truth without ever jumping | Smoothed gap to the peer median or backup; raised after N readings in a row above a tolerance; a real surge that moves every sensor is not drift | `SLOW_DRIFT` | `test_hardening_v04.py` |
| 21 | Restart wipes quarantine | A quarantined sensor looks innocent after a restart | Trust state saved atomically (owner-only file) and restored at start; a hostile or corrupt state file is skipped | `state_unreadable` | `test_hardening_v04.py` |
| 22 | One stolen shared token | Full access for everyone | Narrow tokens: Brain reads verdicts only, Twin sends readings only, admin does all; weak or duplicate tokens refuse to start; revoke = remove the variable | `401` | `test_hardening_v04.py` |

## Verified against the Twin's real code
Guardian (profile `twin`) was run against the Twin team's real `twin_app.py` (16 sensors over REST) together with the City Brain: 440+ verdicts, 0 UNTRUSTED on a calm city. The first run found a real problem: the Twin holds calm values at exactly the same number for minutes, so the plain `FROZEN_VALUE` rule quarantined healthy port sensors and the Twin isolated them. Fix: in the twin profile (`frozen_requires_motion: true`) a constant reading is frozen only when its peers or backup moved during the same window (test `test_a_noise_free_calm_city_never_triggers_frozen_value`).

## Known limitations (state them in the report; they are part of the required "Known limitations" heading)
- **No TLS** in Guardian itself. Use a TLS reverse proxy on a shared network.
- **Registry `open` mode** (the default, so partners can connect before their device list is known) only has caps and probation.
  A patient attacker with enough fake ids still wins in open mode (the test `test_without_the_registry_fake_devices_discredit_the_real_sensors`
  shows it). **Set `registry.mode: strict` and fill `allowed_ids` as soon as the Twin's device list is known.**
  The per-zone cap only helps when it is set close to the real zone size.
- **Both sensors of a pair compromised and agreeing**, or a pair that is ambiguous from the very first reading (no clean history), cannot be resolved: both are distrusted (fail-safe for the Brain).
- **No physical cross-check yet** (water level against pump flow or pressure); single sensors only get range, rate and frozen checks.
- **Slow drift** is now detected when a reference exists (peer median or backup). A drift smaller than the drift tolerance, or on a single sensor with no reference, is still not detected.
- **Frozen value on a sensor with no peer and no backup** (for example RES-WLV-01) is not judged in the twin profile: a noise-free simulator and a stuck sensor look the same. Range, rate and status checks still apply.
- **Zones with fewer than 3 healthy peers** get no cross-sensor check (range, rate and frozen checks still apply).
- **A majority of compromised sensors in one zone** defeats the peer check.
- **Actuator failure** detection is not built (sensors only).
- **Trust state** is saved to `logs/guardian-state.json` every 5 s and restored at start; ordering state is not restored (a fresh stream must be accepted). A crash can lose up to 5 s of state.
- A flood also refuses up to one limiter window (1 s) of normal readings from that device.
- Per-partner tokens exist (admin, Brain read-only, Twin ingest-only) but they are static environment variables: revoking one needs a restart; no per-user identity.
