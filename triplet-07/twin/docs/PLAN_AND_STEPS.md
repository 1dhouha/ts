# 🏆 Twin Layer — Master Plan & Execution Steps (TSYP14)

> **Competition:** City Brain Convergence (IEEE Tunisia, TSYP14)  
> **Layer:** Layer 1 — Digital Twin (Convergence City)  
> **Key Dates:**  
> • Development: 22 September – 14 November 2026  
> • Round 1 Triplet Matching: 31 October 2026  
> • Official Submission (Repo, Report, Pitch Deck): **7 November 2026**  
> • Live Demo & Pitch at CSTAM: **14 November 2026** (2 min pitch + 8 min live demo)  
> **Target Score:** 50/50  

---

## 1. Team Responsibilities & Ownership

| Person | Focus Area | Core Responsibilities | Current Status |
| :--- | :--- | :--- | :--- |
| **Person A** | **Ground Truth / Core** | Catalog, physical state models (power, water, traffic, air, responders), discrete clock, lifecycle, transitions, atomic effects, deterministic replay. | **~90% Complete** in `src/city_core/` (Coverage $\ge 80\%$) |
| **Person B** | **Devices & Scenarios** | Sensor sampling, 7 fault models, actuator command states, isolation flags, scenario triggers (`flood`, `power_outage`, `accident`). | Adapt existing `src/city_live/` to match A's physical anchors. |
| **Person C** | **Interface, Security & Observability** | Public Façade, FastMCP server, REST safety net, Bearer tokens, allow-list projection, R1 mocks, R2 JSONL logs, R3 live timeline, docs, CI. | **Active Execution (Phases 0–9 below).** |

---

## 2. Final Target Project Arborescence

```text
CS-TSYP-TWIN-LAYER/
├── .gitignore
├── pyproject.toml                     # Python 3.12+ config & test coverage rules (>=80%)
├── docker-compose.yml                 # One-click launch for jury / multi-PC testing
├── Makefile                           # Unified test, lint, run commands
├── README.md                          # Quickstart, architecture overview, runbook
├── Start Dashboard.cmd                # One-click Windows launcher for stage demo
│
├── docs/                              # Mandatory competition deliverables
│   ├── PLAN_AND_STEPS.md              # This execution master plan
│   ├── INTERFACE_CARD.md              # [Requis R1] 1-page guide for partner teams
│   ├── TEST_BOOK.md                   # Concrete test runs, logs & coverage proofs
│   ├── ARCHITECTURE.md                # System architecture, data flow & security
│   ├── SECURITY.md                    # Role-based access, tokens, allow-list proofs
│   ├── CORE_DESIGN.md                 # 5 physical domain equations & transition order
│   └── DEVICES.md                     # Complete sensor, actuator & fault catalog
│
├── schemas/                           # JSON Schemas for contract enforcement
│   ├── envelope.json                  # Standard response {ok, run_id, data/error, timestamp}
│   ├── reading_public.json            # Public sensor observation (no ground truth)
│   ├── reading_evaluator.json         # Full observation with ground truth (evaluator only)
│   └── actuator_command.json          # Validated actuator command schema
│
├── scenarios/                         # Jury-facing scenario definitions
│   ├── flood_harbor.json              # Coastal surge / harbor water rise
│   ├── false_water_reading.json       # Sensor drift / falsified reading
│   ├── power_grid_failure.json        # Substation feeder trip
│   └── traffic_emergency.json         # Road blockage + ambulance dispatch
│
├── mocks/                             # [Requis R1] Autonomous Partner Clients
│   ├── brain_mock/
│   │   └── mock_brain.py              # Automated MCP Brain client (reads & dispatches)
│   └── guardian_mock/
│       └── mock_guardian.py           # Automated MCP Guardian client (detects & isolates)
│
├── src/                               # Application packages
│   ├── city_core/                     # [Person A] Pure-Python ground truth simulation
│   │   ├── catalog.py, state.py, snapshots.py, clock.py, lifecycle.py, transitions.py
│   │   ├── power.py, water.py, traffic.py, air.py, responders.py, effects.py, replay.py
│   │   └── data/ (catalog.json, initial_config.json)
│   ├── twin_devices/                  # [Person B] Sensors, 7 faults, actuators, isolation
│   │   ├── registry.py, isolation.py, sensors/, faults/, actuators/
│   ├── twin_scenarios/                # [Person B] Scenario triggers & timeline drivers
│   ├── twin_interface/                # [Person C] Public boundary
│   │   ├── facade.py                  # Central gateway with thread-safe locking
│   │   ├── auth.py                    # Bearer token validation (constant-time)
│   │   ├── permissions.py             # Tool × Role access matrix
│   │   ├── projections.py             # Allow-list filter (strips actual_value, delta, fault)
│   │   ├── envelope.py                # Uniform JSON response wrapper
│   │   ├── mcp_server/                # FastMCP server on HTTP/SSE
│   │   └── rest/                      # Emergency REST fallback API
│   ├── twin_observability/            # [Person C] Monitoring & proof
│   │   ├── seq.py                     # Monotonic global sequence counter
│   │   ├── event_store.py             # In-memory ring buffer (get_events_since)
│   │   ├── scenario_log.py            # [Requis R2] Per-scenario append-only JSONL
│   │   └── timeline.py                # [Requis R3] Live timeline stream
│   ├── twin_dashboard/                # SCADA web interface (SVG map, telemetry, controls)
│   ├── runner.py                      # Simulation loop driver
│   └── twin_app.py                    # Application assembler (`python -m twin_app`)
│
├── tests/                             # Comprehensive test suites (>=80% coverage)
│   ├── core/                          # Physics & transition unit tests (A)
│   ├── devices/                       # Sensor & fault unit tests (B)
│   ├── interface/                     # Auth, permissions & facade tests (C)
│   ├── security/                      # Anti-leak test (asserts 0 ground truth leaks)
│   ├── contract/                      # JSON schema validation tests
│   └── e2e/                           # Full-cycle integration tests
│
├── tools/                             # Automation scripts
│   ├── run_checks.py                  # Test & trace-coverage runner
│   ├── run_standalone.py              # Boots Twin + Mocks in 1 command [R1 proof]
│   └── export_manifest.py             # Integrity checker for submission
│
└── logs/                              # Generated per-scenario JSONL logs (.gitignore tracked)
```

---

## 3. Critical Architectural Decisions (Golden Rules)

1. **Ground Truth Stays Private:** Brain and Guardian receive `reported_value` and `status`, never `actual_value`, `delta`, or `fault`. Only the Evaluator role/dashboard sees the ground truth.
2. **One Monotonic Clock:** All physics and events are indexed by `sim_tick` (1 tick = 1000 ms). Wall-clock speed multipliers never change simulation step math.
3. **Idempotency Unification:** The Façade’s `request_id` directly maps to the Core’s `effect_id`. Duplicate network requests return identical cached outcomes without duplicate physics mutations.
4. **Quarantine Enforcement Only:** The Twin does **not** enforce "Guardian verdict before Brain command." The Twin only enforces the `GUARDIAN_RESTRICTED` flag when a device is quarantined via `isolate_device`.

---

## 4. Person C Implementation Steps (0 $\rightarrow$ Finale)

### Phase 0: Baseline & Environment Setup
- [x] **Step 0.1** — Verify Python 3.12+ (`python --version` - Python 3.11.9 detected and validated).
- [x] **Step 0.2** — Run baseline check on existing core: `python tools/run_checks.py --evidence tmp/baseline` (117/117 tests pass, 93-100% coverage).
- [x] **Step 0.3** — Install dependencies: `pip install mcp requests` (Installed & ready).
- [x] **Step 0.4** — Create folder directories (`src/twin_interface`, `src/twin_observability`, `mocks`, `schemas`, `docs`, `tests/security`, `tests/interface`, `logs`).

### Phase 1: Security, Envelopes & Anti-Leak Projections (Oct 22–23)
- [x] **Step 1.1** — Create `src/twin_interface/envelope.py`: Standard `{ok, run_id, data, error, timestamp}` (100% coverage).
- [x] **Step 1.2** — Create `src/twin_interface/auth.py`: Constant-time token checking for `brain`, `guardian`, `operator`, `evaluator` (96% coverage).
- [x] **Step 1.3** — Create `src/twin_interface/permissions.py`: Tool $\times$ Role matrix (88% coverage).
- [x] **Step 1.4** — Create `src/twin_interface/projections.py`: Allow-list filter strictly excluding `actual_value`, `delta`, `fault` (95% coverage).
- [x] **Step 1.5** — Write `tests/security/test_leakage.py`: Automated test proving zero private field leaks to Brain/Guardian (10/10 tests pass).

### Phase 2: Central Façade Gateway (Oct 24)
- [x] **Step 2.1** — Create `src/twin_interface/facade.py` connected thread-safely to `LiveCity` (82% coverage).
- [x] **Step 2.2** — Implement `get_sensor_readings`, `get_city_state`, `get_events_since` with allow-list projections.
- [x] **Step 2.3** — Implement `send_command` with `GUARDIAN_RESTRICTED` quarantine check & idempotency cache.
- [x] **Step 2.4** — Implement `isolate_device` and `restore_device` for Guardian role.
- [x] **Step 2.5** — Write `tests/interface/test_facade.py` (8/8 tests pass, 135/135 full suite pass).

### Phase 3: FastMCP Tool Server & REST Safety Net (Oct 25–27)
- [x] **Step 3.1** — Create `src/twin_interface/mcp_server/server.py` using FastMCP (97% coverage).
- [x] **Step 3.2** — Add detailed LLM docstrings to all MCP tools.
- [x] **Step 3.3** — Implement Bearer token extraction middleware `src/twin_interface/mcp_server/middleware.py` (100% coverage).
- [x] **Step 3.4** — Create emergency fallback REST API `src/twin_interface/rest/router.py` (98% coverage).
- [x] **Step 3.5** — Write in-memory MCP client test `tests/interface/test_mcp.py` (10/10 tests pass, 145/145 full suite pass).

### Phase 4: Observability, JSONL Logs [R2] & Live Timeline [R3] (Oct 28)
- [x] **Step 4.1** — Implement atomic sequencer `src/twin_observability/seq.py` (100% coverage).
- [x] **Step 4.2** — Implement 10,000-event ring buffer `src/twin_observability/event_store.py` (95% coverage).
- [x] **Step 4.3** — Build `src/twin_observability/scenario_log.py` flushing `logs/{scenario}_{run_id}.jsonl` per line without ground truth (98% coverage).
- [x] **Step 4.4** — Live timeline card formatter and SSE streaming `src/twin_observability/timeline.py` (100% coverage).
- [x] **Step 4.5** — Write `tests/observability/test_observability.py` with multi-threaded concurrency, buffer overflow, and anti-leak tests (8/8 tests pass, 153/153 full suite pass).

### Phase 5: Standalone Partner Mocks [R1] (Oct 29–30)
- [x] **Step 5.1** — Build `mocks/guardian_mock/mock_guardian.py` (detects sensor discrepancy $>50\text{ cm}$ $\rightarrow$ isolates).
- [x] **Step 5.2** — Build `mocks/brain_mock/mock_brain.py` (detects water level $>80\text{ cm}$ $\rightarrow$ dispatches pump).
- [x] **Step 5.3** — Create `tools/run_standalone.py`: 1-command boot running Twin + Mocks with full success output (verified: Guardian isolate + Brain pump dispatch).

### Phase 6: Interface Card [R1] & CI Automation (Oct 30–31)
- [x] **Step 6.1** — Write 1-page `docs/INTERFACE_CARD.md`.
- [x] **Step 6.2** — Write `docs/SECURITY.md`.
- [x] **Step 6.3** — Set up CI pipeline testing syntax, coverage $\ge 80\%$, zero leakage, and the standalone proof (`.github/workflows/ci.yml`).
- [ ] **Step 6.4** — **Hand over Interface Card to partner teams on October 31 (Round 1 Matching).**

### Phase 7: Multi-PC Triplet Testing via Tailscale (Nov 1–5)
- [ ] **Step 7.1** — Connect Twin, Brain, and Guardian laptops to a shared Tailscale virtual LAN.
- [ ] **Step 7.2** — Execute joint flood & sensor failure scenarios.
- [ ] **Step 7.3** — Verify JSONL scenario logs are cleanly produced.

### Phase 8: Deliverables, Documentation Pack & Submission (Nov 6–7)
- [ ] **Step 8.1** — Assemble `docs/TEST_BOOK.md` with concrete execution logs, screenshots, and coverage reports.
- [ ] **Step 8.2** — Tag release `v1.0-submission`.
- [ ] **Step 8.3** — Verify clean checkout runs without errors.
- [ ] **Step 8.4** — **Submit repository, technical report, and pitch deck on November 7.**

### Phase 9: Live Demo Rehearsal & Stage Presentation (Nov 8–14)
- [ ] **Step 9.1** — Rehearse 8-minute stage timing (2 min pitch + 6 min live incident response).
- [ ] **Step 9.2** — Prepare failover triggers (phone 4G hotspot + local mocks).
- [ ] **Step 9.3** — **Live Pitch at CSTAM on November 14.**

---

## 5. Daily Milestone & Progress Checklist

| Phase | Target Date | Core Deliverable | Requirement | Status |
| :---: | :---: | :--- | :---: | :---: |
| **0** | Oct 22 | Python 3.12 verify, baseline green, folders | Baseline | ✅ |
| **1** | Oct 23 | Envelopes, Tokens, Projections, Leak test | Security | ✅ |
| **2** | Oct 24 | Façade Gateway & Quarantine enforcement | Core API | ✅ |
| **3** | Oct 27 | FastMCP Tool Server + REST Fallback API | Transport | ✅ |
| **4** | Oct 28 | Scenario JSONL logger + Live Timeline | **R2, R3** | ✅ |
| **5** | Oct 30 | Fake Brain + Fake Guardian standalone runner | **R1** | ✅ |
| **6** | Oct 31 | 1-page Interface Card & CI quality gates | **R1, CI** | ✅ |
| **7** | Nov 5 | Tailscale Triplet Interoperability | Partner E2E | 🔲 |
| **8** | Nov 7 | Test Book, Report & Repo Submission | **SUBMISSION**| 🔲 |
| **9** | Nov 14 | 8-minute Live Pitch & Demo at CSTAM | **STAGE** | 🔲 |
