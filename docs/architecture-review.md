# Architecture Efficiency Review — PROPOSED (2026-09-29)

Scope: checks `docs/architecture.md` (ADR-002…006, SPEC.md §5–6) and `docs/ml-architecture.md` against Requirements Rev 2 and the 8-hour build window. Proposed changes are **ADR-007 (ML pipeline)** and **ADR-008 (infrastructure trim)**; nothing below is accepted until the team agrees. Where accepted, update `architecture.md` and `decisions.md` together.

## 1. Verdict

The two-service split, Python/FastAPI, React, Postgres, Prometheus/Grafana and Compose are **sound and should stay**. The design is **not the most efficient** in five places. Fixing them removes one container, two heavy dependencies from MVP, and closes six requirement gaps.

| # | Finding | Evidence | Change | Effect |
|---|---|---|---|---|
| E1 | **Redis adds nothing at one Core instance** | Latest state fits in Core memory; FastAPI serves WebSocket directly; Postgres holds history | Drop Redis; in-process snapshot + asyncio broadcast; last-known-good snapshot also written to Postgres | −1 container, −1 failure mode, −1 health row to explain (RISK-011) |
| E2 | **XGBoost is poor value for MVP** | Slot-average profile already scores 5.2% MAPE on held-out days vs ~5.7% simulator noise | Forecast = profile × live `demand_multiplier` × short-term correction (ADR-007). XGBoost/LightGBM only as a stretch residual model | Hours saved; same or better accuracy; crisis-adaptive |
| E3 | **Detection only on demand z-score** | 5 of 6 event types never change demand | State-change detector over the snapshot Core already has (REQ-018) | Cheap code, covers every crisis in the brief |
| E4 | **Stockout via ML is the wrong tool** | Inventory flow is deterministic; `served` = 0 once empty | Deterministic projection engine; reused for "expected impact" | One function serves REQ-007b and REQ-008 impact |
| E5 | **Default sim speed defeats human review** | 8 ticks/s = 2 sim-hours per real second; route transit = 0.25 s | Demo profile (`SIMULATION_SPEED=1` or paused + step) + auto-mode for low-risk actions (REQ-019, REQ-042) | Demo actually usable |
| E6 | **Fallback location open** | ADR-003 open item | `shared/allocator` Python package installed into both images; Core imports it | REQ-009a works with Intelligence container stopped |
| E7 | **Unbounded per-tick calls** | 9 GETs + 1 assess per tick at 8 ticks/s | One `refresh_snapshot()` per tick (SSE-triggered, 2 s poll fallback, single-flight); demand-history fetched incrementally (`limit = 12 × ticks_since_last + margin`); Intelligence called once per new tick, latest-wins | Predictable load; load test measures our code, not simulator churn |
| E8 | **OR-Tools/PuLP as a separate v2 solver** | SciPy's built-in HiGHS solves the 24 h network LP in 0.077 s (`dataset/benchmarks/lp_prototype.py`) | LP via `scipy.optimize.linprog` is the **primary** policy (ML architecture v3); no OR-Tools/PuLP; heuristic = fallback | No new dependency; one model replaces several hand-written rules |

Kept deliberately (and why):

- **Two services, not one.** Costs one HTTP hop but gives a real, demonstrable "ML model unavailable" failure (`docker stop intelligence`) and parallel work. Efficient for the judging criteria.
- **Postgres, not SQLite.** One extra container, but gives the brief's "Database Healthy" row, safe concurrent writes from async code, and a DB-down failure to demonstrate.
- **Prometheus + Grafana.** Required deliverable 8 asks for dashboards and alerts. Build the in-app status page first (REQ-014); Grafana is provisioned-as-code polish (REQ-026). Cut Grafana before any product feature if time is short.
- **SSE + polling.** SSE gives tick-aligned refresh; polling is the resilience path. Same function behind both, so no duplicate logic.

## 2. Revised architecture

```
                           ┌───────────────────────── docker compose (7 containers) ──────────────────────────┐
 BUP Simulator (given)     │                                                                                     │
  /v1/*  /admin/*  SSE     │   ┌──────────────── Core Service (FastAPI) ────────────────┐                         │
        ▲   │              │   │ SimClient: httpx + tenacity + circuit breaker           │                         │
        │   │  tick hint   │   │   refresh_snapshot() single-flight ◄─ SSE tick / 2s poll │                         │
        │   └──────────────┼──►│   pydantic validation, stale header, error envelopes    │                         │
        │                  │   │ Snapshot (in memory) + last-known-good ─► Postgres       │                         │
        │   POST alloc     │   │ DemandHistory writer (incremental) ─────► Postgres       │                         │
        └──────────────────┼───│ Executor: deterministic keys, 409 mapping, confirm GET   │                         │
                           │   │ Policy gate: auto-mode / HUMAN_REVIEW / approvals (RBAC) │                         │
                           │   │ Fallback: import shared.allocator  (no network)         │                         │
                           │   │ REST + WebSocket for UI;  /health  /metrics              │                         │
                           │   └───────┬───────────────────────────────▲──────────────────┘                         │
                           │           │ POST /intel/assess (2s timeout, circuit breaker)                            │
                           │   ┌───────▼──────── Intelligence Service (FastAPI, stateless) ──────────────────┐       │
                           │   │ guard → forecast(profile×multiplier×EWMA, q10/50/90) → detect(multi-signal) │       │
                           │   │ → project(stockout, depot+station) → decide(shared.allocator) → impact      │       │
                           │   │ → confidence gate → explain(template; LLM optional, timeout)  /metrics      │       │
                           │   │ artifacts: profile table + versioned metrics.json baked into image          │       │
                           │   └─────────────────────────────────────────────────────────────────────────────┘       │
                           │   Postgres (history, audit, predictions, lkg snapshot)                                  │
                           │   Frontend (React build served by nginx) ◄── WS/REST from Core only                     │
                           │   Prometheus ─► Grafana (provisioned dashboards + alert rules)                          │
                           └─────────────────────────────────────────────────────────────────────────────────────┘
```

Containers: simulator, core, intelligence, frontend, postgres, prometheus, grafana (**7**, was 8).

## 3. Requirement coverage (Rev 2)

| Req | Where it is met |
|---|---|
| 001, 027 | Frontend panels fed by Core WS |
| 002, 003, 004, 010 | Core SimClient + snapshot + history writer |
| 005, 006 | Core Executor |
| 007, 008, 018, 029 | Intelligence pipeline (`docs/ml-architecture.md`) |
| 009a | Core imports `shared.allocator` when Intelligence times out/circuit open |
| 009b | Pydantic guard in Core and Intelligence; reject + alert metric + log |
| 009c, 019 | Confidence gate (Intelligence) + policy gate/approvals (Core) |
| 009d, 025 | Circuit breaker, last-known-good snapshot with age, degraded banner |
| 011, 024, 028 | Scenario scripts (reset + events + step) reused for demo and benchmark |
| 012, 020, 023 | Compose + healthchecks; GitHub Actions; image tags + model version |
| 013, 014, 026 | `/metrics` on both services + cAdvisor-free system metrics via `psutil` gauges; status page; Grafana |
| 015 | Locust on `POST /api/recommendations/evaluate` (Core→Intelligence) with simulator reads served from snapshot |
| 016, 040 | Env config, operator token on approve/cancel, 422 on bad input; "Simulated" banner |
| 041 | All topology read from snapshot; no IDs in code except tests |
| 042 | `.env.demo` with `SIMULATION_SPEED=1` |
| 022, 043 | Postgres `decision_audit`; `dataset/ml/README.md` |
| 035 | Template first, LLM behind timeout |

## 4. Build order (critical path)

1. Contracts: CONTRACT-SIM (pydantic models), CONTRACT-INTEL-OUTPUT, CONTRACT-CORE-API — unblocks FE and INTEL in parallel.
2. Core snapshot + history + status page → REQ-002/014 visible early.
3. `shared.allocator` + projection engine → recommendations exist even before the Intelligence service (this *is* the fallback).
4. Intelligence forecast + detection + gate; Executor + approvals.
5. Faults/crisis scripts, metrics, Grafana, Locust, CI.
6. Stretch: LLM narration, residual model, optimizer + benchmark.

## 5. Decisions requested

1. Accept ADR-007 (ML pipeline per `docs/ml-architecture.md`).
2. Accept ADR-008: drop Redis; no OR-Tools/PuLP/XGBoost (LP via SciPy HiGHS instead); `shared.allocator` as fallback location; single-flight tick refresh; demo speed profile.
3. Accept Requirements Rev 2 (new MUSTs: REQ-018, 019, 040, 041, 042).
