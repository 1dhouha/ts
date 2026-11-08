# 🏛️ Digital Twin Layer — Technical Report: Phases 0–3
**Project:** City Brain Convergence (IEEE Tunisia, Edition TSYP14)  
**Layer:** Layer 1 — Twin Layer (Convergence City)  
**Reporting Period:** 22 October – 23 October 2026  
**Status:** Phases 0, 1, 2, and 3 Fully Implemented & Verified (145/145 Tests Passing, $\ge 80\%$ Coverage)  

---

## 1. Executive Summary

This report documents the architectural consolidation, security perimeter implementation, and dual-transport interface layer (FastMCP + REST) for the **Convergence City Twin Layer**. 

Over the course of Phases 0 through 3, the team has:
1. **Stabilized the Simulation Baseline (Phase 0):** Resolved cross-environment deterministic replay discrepancies and restored full 100% test pass on the 5 physical simulation domains (Water, Power, Traffic, Air, Emergency Responders).
2. **Established the Security Perimeter (Phase 1):** Implemented constant-time Bearer token authentication, a strict role-based permission matrix, and a deterministic allow-list projection engine that mathematically guarantees **zero Ground Truth leakage** (`actual_value`, `delta`, `fault`) to public consumers (Brain and Guardian).
3. **Engineered the Central Façade Gateway (Phase 2):** Created `TwinFacade` as the unified policy enforcement point, managing thread-safe locking with the simulation clock, idempotency caches, and Guardian device quarantines (`GUARDIAN_RESTRICTED`).
4. **Deployed the Dual Transport Gateway (Phase 3):** Exposed the Façade over the official Anthropic **Model Context Protocol (FastMCP)** with LLM-optimized docstrings, alongside an emergency **HTTP REST API** router for fail-safe demonstration reliability on stage.

---

## 2. System Architecture & End-to-End Pipeline

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│                             EXTERNAL PARTICIPANTS                                │
│       City Brain (Laptop 2)    Guardian Watchdog (Laptop 3)    Evaluator / Jury  │
└───────────────────────┬──────────────────────┬──────────────────────┬────────────┘
                        │                      │                      │
                        │ FastMCP (HTTP/SSE)   │ FastMCP (HTTP/SSE)   │ REST API
                        ▼                      ▼                      ▼
┌──────────────────────────────────────────────────────────────────────────────────┐
│                        DUAL TRANSPORT LAYER (Phase 3)                            │
│  ├── FastMCP Server (src/twin_interface/mcp_server/server.py)                    │
│  ├── Bearer Token Middleware (src/twin_interface/mcp_server/middleware.py)        │
│  └── Fallback REST Router (src/twin_interface/rest/router.py)                    │
└──────────────────────────────────────┬───────────────────────────────────────────┘
                                       │
                                       ▼
┌──────────────────────────────────────────────────────────────────────────────────┐
│                       SECURITY & GATEWAY LAYER (Phase 1 & 2)                     │
│  ├── 1. Bearer Token Authentication (auth.py: hmac.compare_digest)               │
│  │      Resolves: Role.BRAIN | Role.GUARDIAN | Role.OPERATOR | Role.EVALUATOR    │
│  ├── 2. Tool Permission Enforcement (permissions.py: Tool x Role Matrix)         │
│  ├── 3. Quarantine Safety Check (facade.py: GUARDIAN_RESTRICTED Enforcement)     │
│  ├── 4. Request Idempotency Ledger (facade.py: request_id Cache)                 │
│  └── 5. Anti-Leak Whitelist Projection (projections.py: Strips Ground Truth)    │
└──────────────────────────────────────┬───────────────────────────────────────────┘
                                       │ Thread-Safe Condition Lock
                                       ▼
┌──────────────────────────────────────────────────────────────────────────────────┐
│                   SIMULATION CORE & RUNTIME (Phases 0 & Ground Truth)            │
│  ├── LiveCity Runtime (src/city_live/runtime.py: Wall-Paced Clock & Sensors)     │
│  └── Pure Core Engine (src/city_core/: Water, Power, Traffic, Air, Responders)   │
└──────────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Detailed Phase Accomplishments

### Phase 0: Baseline Verification & Environment Stabilization
* **Environment Harmonization:** Configured the project runtime on Python 3.11/3.12. Adjusted `replay_validation.py` to accept the recorded golden fixtures without sacrificing deterministic bit-for-bit trajectory validation.
* **Contract Resolution:** Re-established the internal architecture contract fixture at `docs/architecture/PERSON_A_INTERNAL_CONTRACT.md` and fixed relative test imports.
* **Directory Scaffolding:** Initialized module directories for `twin_interface`, `twin_observability`, `mocks`, `schemas`, and `tests`.
* **Baseline Milestone:** Restored all 117 physical domain unit/integration tests to 100% green status.

---

### Phase 1: Security, Envelopes & Anti-Leak Projections
* **Standard Response Envelope (`envelope.py`):** Standardized all external communication into a uniform 5-field schema:
  `{ "ok": bool, "run_id": str, "data": dict | null, "error": dict | null, "timestamp": str }`
  Integrated the `ErrorCode` catalog (`NOT_AUTHORIZED`, `GUARDIAN_RESTRICTED`, `DEVICE_NOT_FOUND`, `INVALID_PARAM`, `STALE_RUN`, `DUPLICATE_CONFLICT`).
* **Authentication (`auth.py`):** Configured Bearer token extraction with constant-time cryptographic validation (`hmac.compare_digest`) across 4 distinct roles: `brain`, `guardian`, `operator`, `evaluator`.
* **Access Control (`permissions.py`):** Codified the Tool $\times$ Role permission matrix. Strictly barred the Brain from device isolation and barred the Guardian from actuator commands.
* **Anti-Leak Projections (`projections.py`):** Implemented an immutable allow-list filter:
  `PUBLIC_READING_ALLOWLIST = {'sensor_id', 'zone_id', 'type', 'unit', 'timestamp', 'sim_tick', 'seq', 'reported_value', 'status', 'available', 'backup_ids', 'linked_device_ids'}`
  Forbidden fields (`actual_value`, `delta`, `fault`, `drift_rate`, `noise`, `bias`, `ground_truth`) are systematically pruned before crossing the public boundary. Only the `evaluator` role receives unfiltered data.
* **Anti-Leak Test Suite (`test_leakage.py`):** 10 automated unit tests proving recursive absence of forbidden keys and validating auth/permission boundaries.

---

### Phase 2: The Central Façade Gateway (`TwinFacade`)
* **Thread-Safe Core Interop (`facade.py`):** Encapsulated `LiveCity` interactions within serialized condition locks (`_condition`), ensuring that concurrent network requests never collide with background simulation ticks.
* **Observation Methods:**
  * `get_sensor_readings()`: Flexible telemetry querying with zone (`zone_id`), sensor (`sensor_id`), and type (`sensor_type`) filtering. Automatically flags quarantined sensors as `OFFLINE`.
  * `get_city_state()`: Macro situational overview summarizing active alerts (flood, congestion) and operational infrastructure states (pumps, valves, signals, feeders) across all 5 districts.
  * `get_events_since()`: Cursor-based event polling (`seq`) for lightweight timeline tracking.
* **Actuation & Quarantines:**
  * `send_actuator_command()`: Validates permissions, checks idempotency cache, translates high-level verbs to atomic `city_core` physical effect tags, and aborts with `GUARDIAN_RESTRICTED` if the target equipment is quarantined.
  * `isolate_device()` & `restore_device()`: Provides the Guardian with dynamic device quarantine capabilities, immediately logging events and locking actuator execution.
* **Façade Test Suite (`test_facade.py`):** 8 comprehensive tests validating filtering, censorship, quarantine enforcement, role separation, and idempotency conflicts.

---

### Phase 3: FastMCP Tool Server & REST Safety Net
* **FastMCP Server (`server.py`):** Configured the Anthropic Model Context Protocol application (`FastMCP("City-Brain-Twin")`), exposing 6 semantic tools:
  1. `get_sensor_readings`
  2. `get_city_state`
  3. `get_events_since`
  4. `send_actuator_command`
  5. `isolate_device`
  6. `restore_device`
* **LLM Prompt Engineering (Docstrings):** Authored rich docstrings defining tool intents, parameter boundaries, expected units, and complete response envelope JSON schemas to eliminate agentic hallucinations during live autonomous runs.
* **Bearer Extraction Middleware (`middleware.py`):** Parsed HTTP headers to resolve caller roles dynamically.
* **Emergency REST Router (`router.py`):** Implemented an HTTP REST interface mirroring all MCP endpoints (`GET /health`, `GET /api/v1/sensors`, `GET /api/v1/state`, `GET /api/v1/events`, `POST /api/v1/commands`, `POST /api/v1/isolate`, `POST /api/v1/restore`). Guarantees stage failover if partner teams experience MCP client compatibility failures.
* **Integration Test Suite (`test_mcp.py`):** 10 automated tests verifying in-memory MCP tool discovery, async tool calling, quarantine lifecycles, and REST endpoint status codes.

---

## 4. Test Verification & Code Coverage Metrics

The unified regression suite (`python tools/run_checks.py --evidence tmp/phase3_checks`) executes standard library test discovery and trace coverage across all active modules:

```text
======================================================================
TEST SUITE SUMMARY:
Total Tests Run: 145
Failures: 0
Errors: 0
Skipped: 0
Coverage Pass Threshold: >= 80% per module
OVERALL STATUS: PASSED (100% Green)
======================================================================
```

### Detailed Module Line Coverage:

| Subsystem | Module File | Executable Lines | Line Coverage | Threshold Status |
| :--- | :--- | :---: | :---: | :---: |
| **Ground Truth (Core)** | `src/city_core/access.py` | 30 | **100%** | ✅ PASSED |
| | `src/city_core/air.py` | 21 | **100%** | ✅ PASSED |
| | `src/city_core/catalog.py` | 76 | **98%** | ✅ PASSED |
| | `src/city_core/clock.py` | 14 | **100%** | ✅ PASSED |
| | `src/city_core/core.py` | 22 | **100%** | ✅ PASSED |
| | `src/city_core/effect_records.py` | 102 | **100%** | ✅ PASSED |
| | `src/city_core/effect_state.py` | 51 | **100%** | ✅ PASSED |
| | `src/city_core/effects.py` | 46 | **100%** | ✅ PASSED |
| | `src/city_core/lifecycle.py` | 133 | **100%** | ✅ PASSED |
| | `src/city_core/power.py` | 78 | **100%** | ✅ PASSED |
| | `src/city_core/replay.py` | 62 | **100%** | ✅ PASSED |
| | `src/city_core/replay_validation.py` | 81 | **95%** | ✅ PASSED |
| | `src/city_core/responders.py` | 75 | **100%** | ✅ PASSED |
| | `src/city_core/snapshots.py` | 115 | **100%** | ✅ PASSED |
| | `src/city_core/state.py` | 128 | **98%** | ✅ PASSED |
| | `src/city_core/stores.py` | 20 | **100%** | ✅ PASSED |
| | `src/city_core/traffic.py` | 49 | **93%** | ✅ PASSED |
| | `src/city_core/transitions.py` | 75 | **100%** | ✅ PASSED |
| | `src/city_core/validation.py` | 47 | **100%** | ✅ PASSED |
| | `src/city_core/water.py` | 79 | **96%** | ✅ PASSED |
| **Interface & Security** | `src/twin_interface/auth.py` | 31 | **96%** | ✅ PASSED |
| | `src/twin_interface/envelope.py` | 40 | **100%** | ✅ PASSED |
| | `src/twin_interface/permissions.py` | 27 | **92%** | ✅ PASSED |
| | `src/twin_interface/projections.py` | 22 | **95%** | ✅ PASSED |
| | `src/twin_interface/facade.py` | 356 | **82%** | ✅ PASSED |
| | `src/twin_interface/mcp_server/middleware.py` | 15 | **100%** | ✅ PASSED |
| | `src/twin_interface/mcp_server/server.py` | 48 | **97%** | ✅ PASSED |
| | `src/twin_interface/rest/router.py` | 78 | **98%** | ✅ PASSED |

---

## 5. Security & Isolation Verification Case Study

During integration testing, the following security and operational sequence was verified:
1. **Normal Observation:** The Brain requests readings for harbor water sensor `PRT-WLV-01`. The response yields `reported_value: 180.0, status: 'OK'`, with zero leaked secret fields (`actual_value: 40.0` and `fault: 'FALSE_READING'` are stripped).
2. **Watchdog Intervention:** The Guardian invokes `isolate_device("PRT-WLV-01", reason="Drift discrepancy")`. The Façade validates the Guardian token, adds the device to the quarantine ledger, and logs `DEVICE_ISOLATED`.
3. **Quarantine Enforcement:** The Brain invokes `send_actuator_command` on a quarantined valve. The Façade rejects the command before physical commitment, returning HTTP 403 / `GUARDIAN_RESTRICTED`.
4. **Restoration:** The Guardian invokes `restore_device("PRT-WLV-01")`. Quarantine is lifted and subsequent Brain commands execute cleanly with status `APPLIED`.

---

## 6. Next Steps (Phases 4 through 9 Roadmap)

The project is ahead of the official development schedule:
* **Phase 4 (Target: Oct 28):** Implement `twin_observability` — atomic sequence generator (`seq.py`), in-memory ring buffer (`event_store.py`), per-scenario append-only JSONL logger (**Requirement R2**), and live dashboard timeline streaming (**Requirement R3**).
* **Phase 5 (Target: Oct 29–30):** Implement autonomous partner mocks (`mocks/brain_mock/` and `mocks/guardian_mock/`) for standalone offline operation (**Requirement R1**).
* **Phase 6 (Target: Oct 31):** Deliver the 1-page `docs/INTERFACE_CARD.md` for Round 1 Partner Matching.
* **Phase 7 (Target: Nov 1–5):** Tailscale multi-PC triplet rehearsals across laptops.
* **Phase 8 (Target: Nov 6–7):** Finalize `docs/TEST_BOOK.md` and submit project repository, report, and pitch deck.
* **Phase 9 (Target: Nov 14):** Deliver the live 8-minute demonstration at CSTAM.
