# Test Book · Triplet 02

Every scenario has one trigger command, and each run writes `logs/<id>-<UTC time>.log` (R2). Each log holds the timestamped timeline (R3), the decisions, the offline vault state and a PASS/FAIL line for every expected output below.

**Status:** the City Brain scenarios are listed first and run against the Brain's built-in simulators (R1). The Guardian scenarios (G1 to G13) are at the end of this file and run from the `guardian/` folder. The three layers are all in this repo, and each one still runs alone by default.

| ID | Name | Last result |
|---|---|---|
| CALM-01 | Calm day: the Brain must not overreact | PASS (`logs/`) |
| T5 | Fake flood at the port | PASS (`logs/`) |
| SCN-02 | Real flash flood: start the linked pump | PASS (`logs/`) |
| SCN-03 | Dead and frozen sensors: use the backup | PASS (`logs/`) |
| SCN-04 | Quarantined pump: use the backup pump | PASS (`logs/`) |
| SCN-05 | Real accident and spoofed air alarm | PASS (`logs/`) |
| SCN-06 | Guardian outage: hold, never fabricate | PASS (`logs/`) |
| AIR-01 | Industrial smog: air model curtails the plant | PASS (`logs/`) |
| AIR-02 | Downtown haze: advisory only | PASS (`logs/`) |
| NET-01 | Link lost after deciding: offline vault | PASS (`logs/`) |
| SEC-01 | Command shield: tampering, replay, bad commands | PASS (`logs/`) |
| INP-01 | Malformed partner input | PASS (`logs/`) |
| EMG-01 | Emergency call: route responders | PASS (`logs/`) |

Run everything with `python scenarios/run.py --all` (on Windows, `RUN_ALL_SCENARIOS.cmd`).

## CALM-01 · Calm day: the Brain must not overreact

| Field | Value |
|---|---|
| ID | CALM-01 |
| Name | Calm day: the Brain must not overreact |
| Layers involved | Twin, Guardian, Brain |
| Description | Normal city for 60 ticks: water, traffic, temperature, flow, pressure and PM2.5 all within limits. |
| Trigger | `python scenarios/run.py --scenario calm-01` |
| Expected: Twin | 16 sensors and 5 air monitors report values inside their normal_limits. |
| Expected: Guardian | Every reading TRUSTED. |
| Expected: Brain | No incident, no actuator command. |
| How to verify | Log: no `decision` or `action` events; checks PASS. |

## T5 · Fake flood at the port

| Field | Value |
|---|---|
| ID | T5 |
| Name | Fake flood at the port |
| Layers involved | Twin, Guardian, Brain |
| Description | PRT-WLV-01 jumps from 50 cm to 180 cm while its redundant partner PRT-WLV-02 stays normal (forged reading). |
| Trigger | `python scenarios/run.py --scenario t5` |
| Expected: Twin | PRT-WLV-01 reports 180 cm; PRT-WLV-02 stays around 50 cm. |
| Expected: Guardian | UNTRUSTED (BACKUP_DISAGREEMENT, trust score falls to 0), then PRT-WLV-01 quarantined. |
| Expected: Brain | Flood response refused (DISCARDED), no pump or valve command; switches to backup PRT-WLV-02. |
| How to verify | Timeline chain READING → VERDICT UNTRUSTED → DECISION refused, no ACTION; `/city` shows the sensor red. |

## SCN-02 · Real flash flood: start the linked pump

| Field | Value |
|---|---|
| ID | SCN-02 |
| Name | Real flash flood: start the linked pump |
| Layers involved | Twin, Guardian, Brain |
| Description | Water rises on both port sensors (PRT-WLV-01/02) above 80 cm, then drains. |
| Trigger | `python scenarios/run.py --scenario scn-02` |
| Expected: Twin | Both sensors over 80 cm; PRT-PMP-01 running after the command; water back to normal. |
| Expected: Guardian | TRUSTED (pair agrees). |
| Expected: Brain | START PRT-PMP-01 once (no double response), STOP it when the water is normal again. |
| How to verify | Log: ACTION `PRT-PMP-01 start APPLIED` then `stop APPLIED`; pump blinks green on `/city`. |

## SCN-03 · Dead and frozen sensors: use the backup

| Field | Value |
|---|---|
| ID | SCN-03 |
| Name | Dead and frozen sensors: use the backup |
| Layers involved | Twin, Guardian, Brain |
| Description | IND-TMP-01 dies (null value) while the industrial temperature rises; DTN-TRF-01 freezes on one value. |
| Trigger | `python scenarios/run.py --scenario scn-03` |
| Expected: Twin | IND-TMP-01 FAILED, IND-TMP-02 reports the overheat, DTN-TRF-01 repeats one value. |
| Expected: Guardian | IND-TMP-01 and DTN-TRF-01 UNTRUSTED/quarantined; IND-TMP-02 TRUSTED. |
| Expected: Brain | Ignore both bad streams, use IND-TMP-02, open coolant valve IND-VLV-01. |
| How to verify | Log: BACKUP_USED for IND-TMP-01, ACTION `IND-VLV-01 set_opening APPLIED`. |

## SCN-04 · Quarantined pump: use the backup pump

| Field | Value |
|---|---|
| ID | SCN-04 |
| Name | Quarantined pump: use the backup pump |
| Layers involved | Twin, Guardian, Brain |
| Description | Real flood while pump PRT-PMP-01 is quarantined by the Guardian. |
| Trigger | `python scenarios/run.py --scenario scn-04` |
| Expected: Twin | Refuses PRT-PMP-01 with GUARDIAN_RESTRICTED; accepts PRT-PMP-02. |
| Expected: Guardian | Flood readings TRUSTED; PRT-PMP-01 device trust quarantined. |
| Expected: Brain | Record the rejection, START PRT-PMP-02; PRT-PMP-01 never started. |
| How to verify | Log: ACTION REJECTED GUARDIAN_RESTRICTED then `PRT-PMP-02 start APPLIED`. |

## SCN-05 · Real accident and spoofed air alarm

| Field | Value |
|---|---|
| ID | SCN-05 |
| Name | Real accident and spoofed air alarm |
| Layers involved | Twin, Guardian, Brain |
| Description | Traffic surge on DTN-TRF-01/02 (real accident) while a fake sensor RES-AIR-TEST-01 claims dangerous air. |
| Trigger | `python scenarios/run.py --scenario scn-05` |
| Expected: Twin | Downtown traffic over its limit; RES-AIR-TEST-01 reports an impossible air value. |
| Expected: Guardian | Traffic TRUSTED; RES-AIR-TEST-01 UNTRUSTED (OUT_OF_RANGE). |
| Expected: Brain | Priority corridor on DTN-TLT-01; spoofed alarm refused, no command for it. |
| How to verify | Log: ACTION `DTN-TLT-01 set_priority_corridor APPLIED`; DISCARDED decision for RES-AIR-TEST-01. |

## SCN-06 · Guardian outage: hold, never fabricate

| Field | Value |
|---|---|
| ID | SCN-06 |
| Name | Guardian outage: hold, never fabricate |
| Layers involved | Twin, Guardian, Brain |
| Description | Guardian stops answering for 30 s while the port water is high. |
| Trigger | `python scenarios/run.py --scenario scn-06` |
| Expected: Twin | Water at 110 cm on both sensors. |
| Expected: Guardian | No verdict during the outage, then TRUSTED. |
| Expected: Brain | HELD during the outage (no command, nothing queued); START PRT-PMP-01 once the Guardian is back. |
| How to verify | Log: decision HELD with no commands, later ACTION `PRT-PMP-01 start APPLIED`. |

## AIR-01 · Industrial smog: air model curtails the plant

| Field | Value |
|---|---|
| ID | AIR-01 |
| Name | Industrial smog: air model curtails the plant |
| Layers involved | Twin, Guardian, Brain |
| Description | A strong PM2.5 source over the industrial zone; PLANT-01 adds to it. |
| Trigger | `python scenarios/run.py --scenario air-01` |
| Expected: Twin | AIR-IND-01 rises above 35 µg/m³, then falls once F-IND is open. |
| Expected: Guardian | TRUSTED. |
| Expected: Brain | Air model chooses curtail_industry: open feeder F-IND (cuts PLANT-01), never touch hospital feeder F-ESS; close F-IND when the air is clean. |
| How to verify | Log: decision `chosen_by: model` with curtail_industry, ACTION `F-IND open APPLIED`. |

## AIR-02 · Downtown haze: advisory only

| Field | Value |
|---|---|
| ID | AIR-02 |
| Name | Downtown haze: advisory only |
| Layers involved | Twin, Guardian, Brain |
| Description | Traffic haze downtown, no plant in that zone. |
| Trigger | `python scenarios/run.py --scenario air-02` |
| Expected: Twin | AIR-DTN-01 above 35 µg/m³. |
| Expected: Guardian | TRUSTED. |
| Expected: Brain | Health advisory; no curtailment (no plant to cut). |
| How to verify | Log: decision ADVISORY for AIR-DTN-01, no F-IND command. |

## NET-01 · Link lost after deciding: offline vault

| Field | Value |
|---|---|
| ID | NET-01 |
| Name | Link lost after deciding: offline vault |
| Layers involved | Twin, Guardian, Brain |
| Description | Uplink to the Twin is cut, then a real flood happens; the link comes back. |
| Trigger | `python scenarios/run.py --scenario net-01` |
| Expected: Twin | Unreachable during the cut; applies the command once the link is back. |
| Expected: Guardian | TRUSTED. |
| Expected: Brain | Decision SAVED_OFFLINE in data/offline/pending_decisions.jsonl (fsync, hash chain, HMAC seal); re-sent with the same request_id; vault marks it SENT. |
| How to verify | Log: `vault` events SAVED then SENT; vault integrity OK; pump blinks amber then green. |

## SEC-01 · Command shield: tampering, replay, bad commands

| Field | Value |
|---|---|
| ID | SEC-01 |
| Name | Command shield: tampering, replay, bad commands |
| Layers involved | Twin, Brain |
| Description | Six attacks on the Brain → Twin channel: out-of-range parameter, unknown device, tampered body, valid send, replay, unsigned command. |
| Trigger | `python scenarios/run.py --scenario sec-01` |
| Expected: Twin | Accepts only the valid signed command; refuses BODY_TAMPERED, NONCE_REPLAYED, SIGNATURE_MISSING (401). |
| Expected: Guardian | Not involved. |
| Expected: Brain | Blocks PARAMETER_OUT_OF_RANGE and UNKNOWN_DEVICE before anything leaves. |
| How to verify | Log: all six checks PASS; `POST /api/security/selftest` returns the codes. |

## INP-01 · Malformed partner input

| Field | Value |
|---|---|
| ID | INP-01 |
| Name | Malformed partner input |
| Layers involved | Twin, Brain |
| Description | The Twin feed carries five broken readings: text value, unknown type, negative seq, bad id, bad timestamp. |
| Trigger | `python scenarios/run.py --scenario inp-01` |
| Expected: Twin | Sends the five malformed rows alongside the 16 good ones. |
| Expected: Guardian | Skips the malformed rows. |
| Expected: Brain | Refuses each with a clear code (BAD_VALUE, UNKNOWN_TYPE, BAD_SEQ, BAD_SENSOR_ID, BAD_TIMESTAMP), keeps running, no command. |
| How to verify | Log: `input_rejected` events; `/api/overview.input_rejections` counts. |

## EMG-01 · Emergency call: route responders

| Field | Value |
|---|---|
| ID | EMG-01 |
| Name | Emergency call: route responders |
| Layers involved | Twin, Brain |
| Description | Operator reports an accident in the industrial zone. |
| Trigger | `python scenarios/run.py --scenario emg-01` |
| Expected: Twin | AMB-01 and POL-01 dispatched along R-LOW → R-IND, then released. |
| Expected: Guardian | Not involved (operator-confirmed call). |
| Expected: Brain | Dispatch ambulance and police on the shortest route of the Twin road graph (≤ 5 roads); release at mission end. |
| How to verify | Log: ACTION dispatch APPLIED for AMB-01 and POL-01 with route [R-LOW, R-IND]. |


## Guardian scenarios (team Guardian, folder `guardian/`)

Each Guardian scenario has one trigger command, run from the `guardian/` folder (`python -m scenarios.run <runner id>`, or `all` for every scenario), and writes one log to `guardian/logs/<runner id>-<time>.log` (R2). Full details, including two extra scenarios (frozen power meter and future-timestamp poisoning), are in `guardian/TESTBOOK.md`. Scenarios G1 to G11 use the Guardian's mock Twin and mock City Brain; G12 and G13 replay the Twin team's own scenarios (T5 and SCN-02) through the Twin's REST contract.

| ID | Name | Last result |
|---|---|---|
| G1 | Calm day | PASS (`guardian/logs/`) |
| G2 | Fake flood, sensor frozen | PASS (`guardian/logs/`) |
| G3 | Fake flood, noisy spoof | PASS (`guardian/logs/`) |
| G4 | Camera dropout is not an attack | PASS (`guardian/logs/`) |
| G5 | Fire, compromised air-quality monitor | PASS (`guardian/logs/`) |
| G6 | Replayed readings | PASS (`guardian/logs/`) |
| G7 | Brain wants a quarantined device | PASS (`guardian/logs/`) |
| G8 | Guardian unreachable | PASS (`guardian/logs/`) |
| G9 | Real incident and attack together | PASS (`guardian/logs/`) |
| G10 | Message flood | PASS (`guardian/logs/`) |
| G11 | Fake-device injection | PASS (`guardian/logs/`) |
| G12 | Fake flood at the port (Twin T5) | PASS (`guardian/logs/`) |
| G13 | Real flash flood (Twin SCN-02) | PASS (`guardian/logs/`) |

## G1 · Calm day

| Field | Value |
|---|---|
| ID | G1 |
| Name | Calm day |
| Layers involved | Twin, Guardian |
| Description | Five water sensors around 40 cm, no incident. |
| Trigger | `python -m scenarios.run t1` (from `guardian/`) |
| Expected: Twin | Streams normal readings. |
| Expected: Guardian | 300 readings, all TRUSTED, no containment. |
| Expected: Brain | Does not react. |
| How to verify | No UNTRUSTED and no containment_call in the timeline; guardian/logs/t1-*.log |

## G2 · Fake flood, sensor frozen

| Field | Value |
|---|---|
| ID | G2 |
| Name | Fake flood, sensor frozen |
| Layers involved | Twin, Guardian |
| Description | A real flood lifts 4 sensors past 180 cm; WL-03 is stuck at 30 cm. |
| Trigger | `python -m scenarios.run t2` (from `guardian/`) |
| Expected: Twin | Streams the real flood and the frozen WL-03. |
| Expected: Guardian | WL-03 UNTRUSTED at +6 s (FROZEN_VALUE), quarantined; the real flood sensors are never flagged. |
| Expected: Brain | (see G5, G6) |
| How to verify | device_quarantined for WL-03 in the log; 0 other UNTRUSTED |

## G3 · Fake flood, noisy spoof

| Field | Value |
|---|---|
| ID | G3 |
| Name | Fake flood, noisy spoof |
| Layers involved | Twin, Guardian |
| Description | WL-03 reports about 30 cm with noise to look alive (evades the frozen check). |
| Trigger | `python -m scenarios.run t2b` (from `guardian/`) |
| Expected: Twin | As G2 with noise. |
| Expected: Guardian | WL-03 UNTRUSTED at +8 s (CROSS_SENSOR_CONTRADICTION), quarantined once. |
| Expected: Brain | n/a |
| How to verify | Reason code in the verdict; one quarantine call |

## G4 · Camera dropout is not an attack

| Field | Value |
|---|---|
| ID | G4 |
| Name | Camera dropout is not an attack |
| Layers involved | Twin, Guardian |
| Description | CAM-01 stops answering at 20 s. |
| Trigger | `python -m scenarios.run t4` (from `guardian/`) |
| Expected: Twin | Stops sending CAM-01. |
| Expected: Guardian | CAM-01 STALE (monitor), trust unchanged, no quarantine; water sensors unaffected. |
| Expected: Brain | n/a |
| How to verify | device_stale event; /devices/CAM-01/trust shows stale true, quarantined false |

## G5 · Fire, compromised air-quality monitor

| Field | Value |
|---|---|
| ID | G5 |
| Name | Fire, compromised air-quality monitor |
| Layers involved | Twin, Guardian |
| Description | Fire lifts AQI to 260 on 3 monitors; AQ-03 keeps reporting about 40. |
| Trigger | `python -m scenarios.run t5` (from `guardian/`) |
| Expected: Twin | Streams the fire and the lying AQ-03. |
| Expected: Guardian | AQ-03 UNTRUSTED at +7 s, quarantined; other monitors never flagged. |
| Expected: Brain | n/a |
| How to verify | One quarantine_device call for AQ-03 |

## G6 · Replayed readings

| Field | Value |
|---|---|
| ID | G6 |
| Name | Replayed readings |
| Layers involved | Twin, Guardian |
| Description | An attacker re-sends 5 old WL-02 messages. |
| Trigger | `python -m scenarios.run t6` (from `guardian/`) |
| Expected: Twin | Streams normally; replays injected. |
| Expected: Guardian | 5 replays refused on arrival (REPLAY_OR_OUT_OF_ORDER); WL-02 trust stays 1.0; no containment. |
| Expected: Brain | n/a |
| How to verify | 5 UNTRUSTED replay verdicts; WL-02 trust_score 1.0 |

## G7 · Brain wants a quarantined device

| Field | Value |
|---|---|
| ID | G7 |
| Name | Brain wants a quarantined device |
| Layers involved | Twin, Guardian, City Brain |
| Description | Brain asks about WL-03 after it was quarantined. |
| Trigger | `python -m scenarios.run t7` (from `guardian/`) |
| Expected: Twin | As G3. |
| Expected: Guardian | Quarantines WL-03 and answers the trust lookup. |
| Expected: Brain | Excludes WL-03, opens the valve on two trusted sensors, rejects WL-03, accepts WL-02. |
| How to verify | brain_decision events: REJECT_DEVICE WL-03, USE_DEVICE WL-02 |

## G8 · Guardian unreachable

| Field | Value |
|---|---|
| ID | G8 |
| Name | Guardian unreachable |
| Layers involved | Twin, Guardian, City Brain |
| Description | Guardian is unreachable for the Brain from 20 s to 35 s. |
| Trigger | `python -m scenarios.run t8` (from `guardian/`) |
| Expected: Twin | As G3. |
| Expected: Guardian | Unreachable, then back. |
| Expected: Brain | No verdict counts as UNTRUSTED: HOLD, then RESUME; valve opens only after recovery (+35 s). |
| How to verify | HOLD, RESUME, OPEN_VALVE in order in the log |

## G9 · Real incident and attack together

| Field | Value |
|---|---|
| ID | G9 |
| Name | Real incident and attack together |
| Layers involved | Twin, Guardian, City Brain |
| Description | Real flood, noisy spoof on WL-03 and a replay burst on WL-02 at the same time. |
| Trigger | `python -m scenarios.run t9` (from `guardian/`) |
| Expected: Twin | Streams all three at once. |
| Expected: Guardian | WL-03 quarantined (+8 s); 5 replays refused; WL-02 trust unchanged. |
| Expected: Brain | Opens the valve at +26 s on trusted evidence; ignores replayed messages. |
| How to verify | OPEN_VALVE, one quarantine_device, replays ignored |

## G10 · Message flood

| Field | Value |
|---|---|
| ID | G10 |
| Name | Message flood |
| Layers involved | Twin, Guardian |
| Description | Someone using WL-02's id sends 150 messages in one second. |
| Trigger | `python -m scenarios.run t10` (from `guardian/`) |
| Expected: Twin | Streams normally. |
| Expected: Guardian | First flood message refused at +0.1 s (MESSAGE_FLOOD); flood_detected logged once; WL-02 trust stays 1.0, not quarantined; other sensors unaffected. |
| Expected: Brain | n/a |
| How to verify | flood_detected event; 0 containment calls |

## G11 · Fake-device injection

| Field | Value |
|---|---|
| ID | G11 |
| Name | Fake-device injection |
| Layers involved | Twin, Guardian |
| Description | During a real flood, 30 fake sensors join the zone and ramp down together to outvote the 5 real ones. |
| Trigger | `python -m scenarios.run t11` (from `guardian/`) |
| Expected: Twin | Real flood plus 30 fake devices. |
| Expected: Guardian | All 810 fake readings refused (UNKNOWN_DEVICE, strict registry); real sensors never flagged. With an open registry the same attack flags all 5 real sensors (proved in tests). |
| Expected: Brain | n/a |
| How to verify | UNKNOWN_DEVICE verdicts; 0 UNTRUSTED on WL-01..05 |

## G12 · Fake flood at the port (Twin T5)

| Field | Value |
|---|---|
| ID | G12 |
| Name | Fake flood at the port (Twin T5) |
| Layers involved | Twin, Guardian, City Brain |
| Description | From tick 5 PRT-WLV-01 reports about 180 cm while its backup PRT-WLV-02 reports 40 cm. |
| Trigger | `python -m scenarios.run t13` (from `guardian/`) |
| Expected: Twin | Streams the fake reading through its REST shape, keeps the true level. |
| Expected: Guardian | First fake reading UNTRUSTED at +0 s (BACKUP_DISAGREEMENT); PRT-WLV-01 quarantined at +5 s via POST /api/v1/isolate; backup and the other 14 sensors never flagged. |
| Expected: Brain | Does not open the valve on the fake reading, and records why. |
| How to verify | One isolate call for PRT-WLV-01; 0 other UNTRUSTED; guardian/logs/t13-*.log |

## G13 · Real flash flood (Twin SCN-02)

| Field | Value |
|---|---|
| ID | G13 |
| Name | Real flash flood (Twin SCN-02) |
| Layers involved | Twin, Guardian, City Brain |
| Description | Both redundant port sensors climb from 40 to 110 cm together. Not an attack. |
| Trigger | `python -m scenarios.run t14` (from `guardian/`) |
| Expected: Twin | Streams the real surge. |
| Expected: Guardian | All readings TRUSTED: the healthy backup confirms the surge; no containment. |
| Expected: Brain | Opens the valve on trusted evidence. |
| How to verify | 0 UNTRUSTED, 0 containment calls; guardian/logs/t14-*.log |

Times come from the Guardian's mocks and a stand-in for the Twin's REST contract, not from the real Twin. The Guardian was also run against the real Twin and Brain on a calm city (0 UNTRUSTED).
