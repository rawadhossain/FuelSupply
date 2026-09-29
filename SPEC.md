# BUP Fuel Supply Intelligence & Resilience Platform — Specification

Event: BUP CSE Fest 2026 Hackathon Finals
Document type: Self-contained team specification (problem + decisions + roadmap)
Stack decided: Python/FastAPI backend, React/TypeScript frontend, LLM narration via OpenAI API
Team: 4-5 members, specialized roles

---

## 1. Problem Statement

**Challenge:** Build and operate an intelligent decision-support platform for a simulated fuel supply network in Bangladesh. The system must help an operations team understand fuel availability, identify emerging shortages, respond to disruptions, recommend allocation decisions, and remain usable when parts of the system fail.

This is explicitly **not only a machine-learning challenge** — it spans three equally-judged dimensions:

| Pillar | Focus |
|---|---|
| Application Development | Operator experience |
| AI / Decision Intelligence | Predict, detect, optimize |
| DevOps & Reliability | Ship, observe, recover |

**Core loop the system must demonstrate:**

```
Observe → Detect → Predict → Decide → Simulate → Act → Monitor → Recover
```

**Supply chain modeled:** Import/Supply → Port/Arrival → Depot/Storage → Distribution/Transport → Fuel Stations → Customer Demand. Fuel types: Diesel, Petrol, Octane.

**Constraint:** Teams do not build their own simulator. The organizers provide the operational world (the BUP Fuel Supply Simulator); the team's job is the intelligent system operating on top of it.

### 1.1 Constraints and Guardrails

- Operate only against the simulation environment; never touch real fuel infrastructure.
- Do not execute real purchases or dispatches.
- Do not use real credentials or private operational systems.
- Distinguish simulated results from real-world conditions in all UI/copy.
- Document all assumptions.
- Preserve human review for consequential simulated decisions.

### 1.2 Success Criteria

```
Useful Application + Meaningful Intelligence + Reliable Backend
+ Deployment + Observability + Resilience + Measured Performance
= Operational AI System
```

Complexity alone does not raise the score — a working, explainable, narrower system beats an unreliable sophisticated one.

---

## 2. Required Deliverables

1. Working application — runnable end-to-end.
2. Source repository — code, setup instructions, dependencies, deployment instructions.
3. Simulator integration — interacts with the official simulator.
4. Intelligence component — at least one meaningful AI/ML/optimization/detection/decision-support capability.
5. Operator interface — usable, shows meaningful operational information.
6. Architecture diagram — simulator → data/backend → intelligence → decision → application → monitoring.
7. Deployment — reproducible method.
8. Observability evidence — logs, metrics, dashboards, alerts.
9. Resilience demonstration — evidence of handling at least one failure condition.
10. Load-test evidence — workload definition + measured results.
11. Final demo — live or judge-supervised.

**Recommended (stretch) deliverables:** CI/CD, automated tests, experiment tracking, model versioning, decision audit history, deployment versioning, simulation replay, scenario configuration, automated fallback, rollback.

---

## 3. Evaluation Criteria

| Criterion | Weight | Assessed on |
|---|---|---|
| Working Product & User Experience | 20% | Functional application, operational workflow, usability, completeness |
| Intelligence & Decision Quality | 20% | Usefulness/quality of AI/ML/optimization/detection; appropriate methodology |
| Architecture & Integration | 15% | Backend engineering, simulator integration, component design, technical coherence |
| DevOps & Engineering Quality | 15% | Deployment, automation, testing, maintainability, engineering practices |
| Resilience & Incident Response | 10% | Failure handling, crisis response, fallback behavior, recovery |
| Observability & Performance | 10% | Monitoring, metrics, logs, health visibility, load testing |
| Demo & Problem Understanding | 10% | Clear explanation, understanding of constraints, effective demonstration |

**Takeaway:** Product + Intelligence = 40% of the score. Invest there first; treat RL and advanced infra as optional polish, not requirements.

---

## 4. Simulator Reference

### 4.1 World model (fixed across all scenarios; only seed/preloaded events differ)

**Regions**

| id | name | demand_factor |
|---|---|---|
| region-dhaka | Dhaka Division | 1.00 |
| region-chattogram | Chattogram Division | 1.08 |

**Depots**

| id | region | dispatch/tick | capacity (D/P/O) | initial inventory (D/P/O) |
|---|---|---|---|---|
| depot-gazipur | region-dhaka | 12,000 | 90,000 / 70,000 / 45,000 | 60,000 / 45,000 / 26,000 |
| depot-patiya | region-chattogram | 11,000 | 85,000 / 65,000 / 40,000 | 55,000 / 42,000 / 24,000 |

**Stations**

| id | region | demand_profile | capacity (D/P/O) | initial inventory (D/P/O) |
|---|---|---|---|---|
| station-mirpur | region-dhaka | urban_high | 15,000 / 14,000 / 9,000 | 9,000 / 9,000 / 5,000 |
| station-tongi | region-dhaka | industrial | 18,000 / 9,000 / 6,000 | 11,000 / 6,000 / 3,500 |
| station-karnaphuli | region-chattogram | highway | 14,000 / 15,000 / 9,000 | 8,500 / 9,500 / 5,200 |
| station-coxsbazar | region-chattogram | regional | 12,000 / 12,000 / 7,000 | 7,500 / 7,500 / 4,200 |

**Routes**

| id | depot → station | transit_ticks | max_shipment |
|---|---|---|---|
| route-gazipur-mirpur | depot-gazipur → station-mirpur | 2 | 7,000 |
| route-gazipur-tongi | depot-gazipur → station-tongi | 2 | 6,500 |
| route-patiya-karnaphuli | depot-patiya → station-karnaphuli | 2 | 7,000 |
| route-patiya-coxsbazar | depot-patiya → station-coxsbazar | 3 | 6,000 |
| route-gazipur-karnaphuli | depot-gazipur → station-karnaphuli | 4 | 5,000 |
| route-patiya-mirpur | depot-patiya → station-mirpur | 4 | 5,000 |

**Demand profiles** (liters/sim-day, noise factor): urban_high 8500/10500/5600 (0.10), industrial 14000/4500/2200 (0.08), highway 10500/11000/6200 (0.12), regional 7200/7600/3600 (0.10) — order is Diesel/Petrol/Octane.

**Hour-of-day multipliers:** industrial 06-18h → 1.55, off-peak 0.45. highway peak (06-09,16-20) → 1.35, else 0.75. urban_high peak (07-09,16-20) → 1.45, else 0.70. regional (07-21h) → 1.25, else 0.65.

**Supply arrivals:** 4 initial-burst arrivals (ticks 12-20) + 18 recurring arrivals every 64 ticks (~16 sim-hours).

### 4.2 Fast start

```yaml
services:
  simulator-api:
    image: asifmahmoud414/bup-fuel-supply-simulator:1.0.0
    environment:
      SIMULATION_SPEED: ${SIMULATION_SPEED:-8}
      TICK_MINUTES: ${TICK_MINUTES:-15}
      SIMULATOR_START_MODE: ${SIMULATOR_START_MODE:-paused}
    ports:
      - "8000:8000"
```

Base URL: `http://localhost:8000`. Swagger: `/docs`. Dashboard: `/admin`.

### 4.3 Hard rules

- Simulator is the world, not the brain — it executes, never decides.
- REST is source of truth; SSE is a "go re-GET" hint only.
- Deterministic: same seed + same actions + same events ⇒ identical state.
- Scenario is baked into the image; cannot switch at runtime.
- One instance per participant, single-tenant, no central server.
- Never modify simulator source — judges run the published image.
- `/admin/*` bypasses all fault injection; `/v1/*` (except `/v1/health`) does not — test resilience against `/v1/*` only.
- Only `/v1/allocations` writes domain state. Everything else is read-only.

### 4.4 Read endpoints (`/v1/*`, JSON, all fault-injectable except `/v1/health`)

| Endpoint | Notes |
|---|---|
| `GET /v1/health` | Liveness probe, bypasses faults |
| `GET /v1/instance` | tick, sim_time, status, seed |
| `GET /v1/regions` | — |
| `GET /v1/depots`, `/v1/depots/{id}` | status: OPEN/CONSTRAINED |
| `GET /v1/stations`, `/v1/stations/{id}` | status: OPEN/OUTAGE; demand_multiplier mutated by events |
| `GET /v1/routes` | status: AVAILABLE/DISRUPTED |
| `GET /v1/supply-arrivals` | sorted by planned_tick; status SCHEDULED/DELAYED/ARRIVED |
| `GET /v1/events` | domain events; status SCHEDULED/ACTIVE/RESOLVED |
| `GET /v1/allocations` | your shipment ledger; status PENDING/IN_TRANSIT/ARRIVED/FAILED/CANCELLED |
| `GET /v1/demand-history?station_id=&limit=` | limit clamped [1,2000], default 200; table grows unboundedly — always pass a limit |
| `GET /v1/metrics` | service_level, allocation_liters, allocation_failures |

### 4.5 Write endpoint: `POST /v1/allocations`

**Request body**

```json
{
  "idempotency_key": "string, 1-150 chars, unique per intended shipment",
  "source_depot_id": "must exist in /v1/depots",
  "destination_station_id": "must exist in /v1/stations",
  "route_id": "must exist in /v1/routes",
  "fuel_type": "DIESEL | PETROL | OCTANE",
  "quantity": "float > 0, <= route.max_shipment"
}
```

**Validation order (first failure wins):**

1. Idempotency check (see below)
2. `404 NOT_FOUND` — unknown depot/station/route id
3. `409 ROUTE_MISMATCH` — route's (depot, station) pair doesn't match request
4. `409 DEPOT_CLOSED` — depot.status not in {OPEN, CONSTRAINED}
5. `409 STATION_CLOSED` — station.status != OPEN
6. `409 ROUTE_DISRUPTED` — route.status != AVAILABLE
7. `409 ROUTE_CAPACITY_EXCEEDED` — quantity > route.max_shipment
8. `409 INSUFFICIENT_INVENTORY` — depot lacks the fuel
9. `409 DISPATCH_CAPACITY_EXCEEDED` — this tick's in-flight+pending+new > depot.dispatch_capacity_per_tick
10. `409 DESTINATION_CAPACITY_EXCEEDED` — station inventory + quantity > station capacity

**Idempotency rules:**

- Idempotency key travels in the **body**, not a header.
- Same key + same body → returns the original allocation (HTTP 201). Safe to retry on timeout.
- Same key + different body → `409 IDEMPOTENCY_KEY_MISMATCH`; must mint a new key.
- Cancelling does **not** free the key — permanently consumed once used.
- **Decision:** generate keys deterministically (hash of depot + station + fuel + tick + intent), not random UUIDs per HTTP call, so a dropped-connection retry deduplicates instead of creating a second real shipment.

**Cancel:** `POST /v1/allocations/{id}/cancel` — refunds inventory, only valid on `PENDING`. `404 ALLOCATION_NOT_FOUND` / `409 CANNOT_CANCEL` otherwise.

### 4.6 SSE stream — `GET /v1/stream`

- On connect: `: connected` comment.
- Format: `event: <name>\ndata: <json>\n\n`. Keepalive every 15s of silence.
- Per-subscriber queue caps at 200 events — fall behind and you're silently dropped; must reconnect.
- **No replay on reconnect** — only forward events from reconnect moment. Always re-fetch REST state after reconnecting.

| Event | When | Payload |
|---|---|---|
| `simulation.tick` | end of every tick | `{tick, sim_time}` |
| `allocation.status_changed` | created/departed/arrived/failed/cancelled | full allocation object |
| `inventory.updated` | depot inventory changed | `{entity_type, entity_id, inventory}` |
| `simulator.notice` | admin reset / runner exception | `{message}` or `{level, message}` |

If a `stream_disconnect` fault is active, `/v1/stream` returns `503 {"detail":{"code":"FAULT_INJECTED"}}` instead of opening.

### 4.7 Admin endpoints (`/admin/*` — bypass all faults, use for self-testing)

| Endpoint | Purpose |
|---|---|
| `GET /admin` | HTML console, auto-refresh, event/fault injection forms |
| `POST /admin/run` / `/admin/pause` / `/admin/toggle` | control ticking |
| `POST /admin/step` | advance exactly one tick, works while paused — **use for deterministic tests** |
| `POST /admin/reset` | wipe and reload baked scenario |
| `POST /admin/events` | inject crisis event: `{type, start_tick, duration_ticks, parameters}` |
| `POST /admin/faults` | inject fault: `{type, duration_seconds, parameters}`, auto-expires |
| `POST /admin/faults/clear` | clear all active faults |
| `GET /admin/audit?limit=` | ground-truth action log, clamped [1,1000] |
| `GET /admin/faults`, `/admin/events` | last 50 rows each |

**Event types:** `demand_spike` (multiplier on stations/regions), `route_disruption` (routes → DISRUPTED), `station_outage` (stations → OUTAGE), `depot_constraint` (depots → CONSTRAINED), `shipment_delay` (one-shot, delays planned_tick), `supply_shortfall` (one-shot, shrinks quantity). Empty filter lists = applies to all entities of that type.

**Fault types (affect `/v1/*` only, not `/admin/*` or `/v1/health`):**

| Type | Effect |
|---|---|
| `latency` | sleeps `delay_ms` before every request |
| `unavailable` | always `503 FAULT_INJECTED` |
| `error_rate` | `503` with given probability |
| `stale_data` | adds `X-Simulator-Stale: true` header, does not block |
| `stream_disconnect` | `/v1/stream` returns 503 |

### 4.8 Status codes

`/v1/allocations` errors: `{"detail": {"code": "...", "message": "..."}}`. Fault errors: `{"error": {"code": "FAULT_INJECTED", ...}}` (note `error` vs `detail`). Pydantic validation: FastAPI default `{"detail":[...]}`.

---

## 5. Architecture Decisions

**Pattern:** Two backend services — enough separation for parallel team work, not a full microservice mesh.

```
                     ┌─────────────────────┐
   BUP Fuel Supply   │   Core Service       │      ┌──────────────────┐
   Simulator (given) │  (FastAPI)           │      │  Intelligence     │
   /v1/*  /admin/*   │  - poller + SSE      │◄────►│  Service (FastAPI)│
   ◄─────────────────┤    listener          │      │  - forecasting    │
   ─────────────────►│  - Postgres writer   │      │  - anomaly detect │
                      │  - allocation submit │      │  - allocation opt │
                      │    (idempotency,     │      │  - LLM narration  │
                      │    error handling)   │      └──────────────────┘
                      │  - REST/WS API       │
                      └──────────┬───────────┘
                                 │ WebSocket / REST
                      ┌──────────▼───────────┐
                      │  Frontend (React)     │
                      │  Operator dashboard   │
                      └───────────────────────┘

Postgres: allocations, predictions, audit log, demand snapshots
Redis: latest-state cache, pub/sub fan-out to frontend
Prometheus + Grafana: scrape both backend services
```

**Decision rationale:** Core service owns everything simulator-facing (so idempotency and fault handling live in one place); Intelligence service is stateless-ish and swappable (heuristic → optimization → RL) without touching integration code. Frontend never talks to the simulator directly.

---

## 6. Tech Stack

| Layer | Choice | Why |
|---|---|---|
| Backend language | Python 3.11+ | Team's strongest language; best ML ecosystem |
| Backend framework | FastAPI (async) | Async fits polling + SSE consumption; auto OpenAPI docs |
| Simulator client | `httpx` + `httpx-sse` | Async HTTP and SSE support |
| Retry/circuit-breaking | `tenacity` | Backoff around simulator calls for resilience |
| Database | PostgreSQL | Persistent state: allocations, predictions, audit log |
| Cache/pub-sub | Redis | Latest-snapshot cache, fan-out to frontend |
| Forecasting/detection | scikit-learn, XGBoost | Time-series/tabular fits better than deep learning here |
| Optimization | OR-Tools or PuLP | Constrained allocation (v2, benchmarked vs. heuristic) |
| LLM | OpenAI API (Chat Completions, default `gpt-5.4-mini`, set in `.env`) | Narration/explanation layer only — not decision-making |
| Frontend | React + TypeScript + Vite | Team familiarity, fast iteration |
| Charts | Recharts / Observable Plot | Dashboard visuals |
| Realtime to browser | WebSocket (own backend, not simulator SSE) | Decouples frontend from simulator's queue/reconnect quirks |
| Metrics | Prometheus + Grafana | Standard, well-documented |
| Logging | `structlog` | Structured logs for decision/incident events |
| Load testing | Locust | Python-native, fits stack |
| CI/CD | GitHub Actions | Lint, test, build, push |
| Deployment | Docker Compose | Matches simulator's own deployment model |
| Testing | pytest + httpx test client | Contract tests against documented error codes |

---

## 7. Team Roles (4-5 people)

| Role | Owns |
|---|---|
| Integration engineer | Core service simulator client, SSE reconnect logic, idempotency-key strategy, full error-code handling (§4.5) |
| Intelligence engineer(s) | Forecasting, anomaly detection, allocation heuristic/optimization, LLM narration |
| Frontend engineer | Operator dashboard, WebSocket consumption, recommendation review UI |
| DevOps engineer | Compose setup, CI/CD, Prometheus/Grafana, load testing, resilience wiring |

---

## 8. Development Roadmap

**Phase 0 — Foundations**
Run simulator locally, explore `/admin`. Define shared Pydantic models matching simulator schemas exactly. Scaffold repo, Compose file, CI skeleton.

**Phase 1 — Ingestion (blocks everything downstream)**
Poller + SSE listener → re-GET on event → upsert into Postgres. Implement deterministic idempotency-key generation. Allocation submission wrapper handling all validation-order error codes. SSE reconnect logic (no replay, keepalive, 200-event queue drop).

**Phase 2 — Intelligence**
Baseline forecast (seasonal/naive) per station/fuel from `/v1/demand-history`. Z-score anomaly detector on demand and inventory deltas. Heuristic allocator (rank stations by hours-to-stockout, allocate from nearest eligible depot respecting dispatch/route/capacity limits) — satisfies the intelligence requirement on its own. Optimization allocator (v2) benchmarked against the heuristic to justify the added complexity. LLM narration function wrapping a finished recommendation object (station, projected stockout, recommended allocation, expected impact — mirrors the brief's ALERT format).

**Phase 3 — Application** (parallel to Phase 1-2, against a mocked contract initially)
Status grid (depots/stations/routes), alerts feed, recommendation review/approve, decision history, health page.

**Phase 4 — Resilience**
Wire the four required fallbacks (§9 below). Circuit breaker around simulator calls. Rehearse by injecting faults via `/admin/faults` and observing degrade/recover behavior live.

**Phase 5 — Observability & load testing**
Metrics across app/system/intelligence layers. Health endpoint aggregating sub-component status. Grafana dashboard. Locust run against the decision/allocation path — p50/p95/p99, throughput, error rate, concurrency, resource usage.

**Phase 6 — Polish**
Architecture diagram, README, full dry-run of the demo script (§10) at least twice. Stretch items if time remains: automated tests, model versioning, simulation replay, deployment rollback.

---

## 9. Resilience Matrix (required fallback behaviors)

| Condition | Fallback |
|---|---|
| ML model unavailable | Fallback allocation policy (heuristic) |
| Invalid simulator response | Reject input + raise alert |
| Prediction confidence too low | Request human review |
| Backend dependency unavailable | Retry / cached state / degraded mode |

---

## 10. Demo Script (organizer-suggested story — rehearse exactly)

1. Normal operations
2. Operator dashboard
3. Demand starts increasing
4. System detects risk
5. Intelligence layer predicts shortage
6. Allocation recommendation generated
7. Operator inspects recommendation
8. Allocation is simulated
9. Crisis event occurs (organizer-injected)
10. System adapts
11. Application or dependency failure is injected
12. Monitoring detects failure
13. Fallback/recovery activates
14. Operations continue

---

## 11. Observability Plan

| Layer | Metrics |
|---|---|
| Application | request rate, latency, error rate, service availability |
| System | CPU, memory, resource utilization |
| Intelligence | prediction error, model confidence, shortage-alert rate, decision frequency, fallback activation count |
| Logs | important actions, integration failures, decision events, recoveries |

Health page mirrors the brief's example: per-component status (Backend API, Database, Fuel Simulator, Prediction Service, Decision Engine) plus p95 latency and error rate.

---

## 12. Load Testing Plan

Target: allocation submission path and/or the decision-recommendation API. Report: average, p50, p95, p99 latency; throughput; error rate; concurrency; resource usage. Tool: Locust.

---

## 13. Deployment Plan

- Single `docker-compose.yml` covering the simulator and every service — one command to run everything.
- All config via env vars; `.env.example` committed; no secrets in repo.
- `/health` on every service, wired to Docker healthchecks and the status page.
- Services must tolerate `SIMULATOR_START_MODE=paused` at boot without crashing.
- Use `/admin/reset` + `/admin/step` for deterministic integration tests instead of wall-clock timing.

---

## 14. Open Decisions / Stretch Goals

- Whether to add reinforcement learning after the optimization allocator is benchmarked (optional per brief; only pursue if it demonstrably beats the heuristic/optimization baseline).
- Kubernetes/Helm, canary/blue-green deploys, autoscaling — explicitly optional in the brief; skip unless core deliverables are done early.
- Simulation replay and scenario configuration as later-stage additions once the core loop is stable.
