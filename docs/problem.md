# Official problem statement

**Source of truth (authoritative, do not edit):**
- `/ProblemStatement.md` — BUP CSE Fest 2026 Hackathon Finals: *Fuel Supply Intelligence & Resilience Platform*
- `/Fuel_Supply_Simulator_Integration_Guide.md` — BUP Fuel Supply Simulator Integration and Interaction Guide

Team decisions on stack and roles are in `/SPEC.md` (indexed by `docs/decisions.md`). SPEC.md restates the simulator reference; where it and the Integration Guide differ, the Guide wins.

This file summarizes and structures those two documents for planning. If this summary and the source files ever disagree, **the source files win** — fix this file, not your mental model.

> Supersedes any earlier agriculture-scenario example that may have been used to test this workflow kit. That scenario is not part of the real challenge and must not influence scope, requirements, or architecture.

## Core problem

Build and operate an intelligent decision-support platform for a simulated Bangladeshi fuel supply network (2 regions, 2 depots, 4 stations, 6 routes, 3 fuel types: DIESEL/PETROL/OCTANE). The platform must let an operations team:

- observe current network state (inventory, demand, supply, routes, events),
- detect/predict emerging shortages and risk,
- recommend fuel allocations from depots to stations,
- respond to injected disruptions (crises) and software/service faults,
- explain the reasoning behind recommendations,
- stay usable and observable when parts of the system (or the simulator) fail,
- demonstrate measured performance under load.

This is an application/backend + AI/decision-intelligence + DevOps/reliability challenge together — not a pure ML challenge. A notebook alone is not a valid submission; a usable operator-facing application is mandatory.

## Required system capabilities (from ProblemStatement.md §5–§18)

| Capability area | What's required |
|---|---|
| Operator application | Usable UI showing a meaningful subset of: inventory, depot/station status, regional demand, shortage alerts, projected risk, incoming supply, disruptions, recommended allocations, expected impact, system alerts, decision history, service health. |
| Intelligence | ≥1 meaningful capability from Prediction, Detection, Decision Intelligence, or Generative AI (RL optional). |
| Decision support | Important recommendations must be inspectable: signals used, constraints, expected impact, confidence/uncertainty, alternatives. |
| Crisis/event handling | Detect, evaluate, respond, explain, and monitor recovery for shipment delay, demand spike, depot constraint, regional disruption, and combined crises. |
| Resilience | Explicit fallback behavior for: ML unavailable, invalid simulator response, low prediction confidence, backend dependency unavailable. |
| DevOps | Reproducible deployment (`docker compose up` or documented equivalent); CI/CD strongly encouraged, not mandatory. |
| Observability | Application metrics (rate/latency/error/availability), system metrics (optional depth), intelligence metrics (prediction error, confidence, alert rate, decision frequency, fallback activation), and logs of important actions/failures/decisions/recoveries. |
| Health/status | Expose health of major components (backend, simulator connectivity, prediction, decision engine) plus p95 latency and error rate, judge-legible. |
| Load testing | Load-test ≥1 meaningful path (prediction API, decision API, simulator integration, dashboard backend, or e2e decision request) and report avg/p50/p95/p99 latency, throughput, error rate, concurrency, resource usage. |
| Security/hygiene | No hard-coded secrets, validate external input, handle failed requests, document required config, don't expose credentials, restrict sensitive operator actions where appropriate. |

## Simulator's role

The organizer-provided **BUP Fuel Supply Simulator** *is* the operational world — teams do not build a supply-chain simulation themselves. It is a deterministic, locally-runnable Docker service (`asifmahmoud414/bup-fuel-supply-simulator:1.0.0`) modeling the world and executing allocation decisions; it does not predict, optimize, recommend, or decide anything itself. Our platform is the brain that sits outside it and calls its HTTP API.

Key properties:
- **Deterministic**: same scenario + seed + actions + injected events ⇒ byte-identical state (including per-tick demand jitter).
- **Single-tenant**: one simulator instance per team, no central server.
- Scenario is baked into the image at build time (default `baseline`, no preloaded events); cannot be switched at runtime.
- Runs on a controllable tick clock (`TICK_MINUTES`, default 15 simulated minutes/tick) and wall-clock speed (`SIMULATION_SPEED`, default 8 ticks/sec while RUNNING), and can be started `paused` or `running`.

## Simulator API surface

Base URL local: `http://localhost:8000`. Docs at `/docs` (Swagger) and `/redoc`. Admin console at `/admin`.

**Public, fault-affected `/v1/*` (read-only except allocations):**
- `GET /v1/health` — liveness probe, **bypasses all fault injection**.
- `GET /v1/instance` — tick, sim_time, status (PAUSED/RUNNING), seed, scenario id/version.
- `GET /v1/regions`, `/v1/depots[/{id}]`, `/v1/stations[/{id}]`, `/v1/routes` — static-ish topology + live status/inventory/capacity.
- `GET /v1/supply-arrivals` — scheduled/delayed/arrived incoming shipments.
- `GET /v1/events` — domain crisis events (scheduled/active/resolved).
- `GET /v1/allocations` — our own allocation ledger (source of truth for what we've done).
- `GET /v1/demand-history?station_id=&limit=` — time series for forecasting; limit clamped [1,2000], table grows unboundedly, always page/filter.
- `GET /v1/metrics` — aggregate ground truth: served/unmet demand, service_level, allocation_liters, allocation_failures.
- `POST /v1/allocations` — **the only domain write**. Body: idempotency_key, source_depot_id, destination_station_id, route_id, fuel_type, quantity.
- `POST /v1/allocations/{id}/cancel` — refunds inventory, only valid while PENDING.

**Push notifications:** `GET /v1/stream` (SSE). Events: `simulation.tick`, `allocation.status_changed`, `inventory.updated`, `simulator.notice`. 15s keepalive comments. Per-subscriber queue capped at 200 — falling behind silently drops the queue (must reconnect). No Last-Event-ID replay; on reconnect only future events are seen — always re-fetch REST state after reconnect.

**Admin `/admin/*` (bypasses fault injection, for orchestration and self-testing, organizers can also use it against us):**
- `run` / `pause` / `toggle` / `step` (deterministic single-tick advance, works regardless of run state) / `reset` (wipes and reloads baseline scenario).
- `events` — inject crisis events: `demand_spike`, `route_disruption`, `station_outage`, `depot_constraint`, `shipment_delay`, `supply_shortfall` (see integration guide §7.8 for parameter shapes and reversal behavior).
- `faults` (+ `faults/clear`) — inject `latency`, `unavailable`, `error_rate`, `stale_data`, `stream_disconnect`, auto-expiring after `duration_seconds`.
- `audit?limit=` — ground-truth action log (tick advances, allocation lifecycle, supply arrivals, event/fault lifecycle).
- `GET /admin` — HTML console with live state + injection forms, auto-refreshing every 2s.

## REST vs SSE responsibilities

- **REST is the source of truth.** Every read of world state (inventory, allocation status, events, metrics) must come from `/v1/*` GETs.
- **SSE is a notification/wake-up mechanism only**, never authoritative. On any SSE event, our system must re-GET the affected REST resource(s) — it must never trust the SSE payload as final state.
- SSE can silently drop us (queue overflow) or be actively severed by a `stream_disconnect` fault (503). The platform must keep working via REST polling alone if SSE is unavailable, and must always refetch full state on reconnect.

## Allocation write path

`POST /v1/allocations` is the **only** way our platform can act on the world. Validation runs in strict, documented order (idempotency check → NOT_FOUND → ROUTE_MISMATCH → DEPOT_CLOSED → STATION_CLOSED → ROUTE_DISRUPTED → ROUTE_CAPACITY_EXCEEDED → INSUFFICIENT_INVENTORY → DISPATCH_CAPACITY_EXCEEDED → DESTINATION_CAPACITY_EXCEEDED). `idempotency_key` is mandatory, permanently consumed even after cancellation, and safe-retryable only with an identical body (same key + same body → replay returns the existing allocation; same key + different body → 409). Cancellation only works while `PENDING` and refunds depot inventory.

## Intelligence requirements

At least one meaningful capability from: demand forecasting / shortage prediction / stockout probability / supply-arrival or transport-delay prediction (Prediction); anomalous demand / abnormal inventory changes / bottlenecks / emerging disruptions (Detection); heuristic, priority-based, constrained-optimization, or RL-based allocation (Decision Intelligence); incident explanation / state summarization / decision explanation via LLM (Generative AI, must support the operational workflow, not be a bolt-on chatbot). RL is explicitly optional and must be justified against a simpler baseline if used.

## Decision-support requirements

Important recommendations (e.g., "allocate 5,000L Diesel from DEPOT-03 to reduce stockout risk 72%→19%") must show, where appropriate: why an area is at risk, which signals drove it, relevant constraints, expected impact, confidence/uncertainty, and alternative actions. Humans must remain able to inspect and (implicitly) override consequential decisions before they're executed against the simulator.

## Resilience requirements

Explicit, demonstrable behavior for:
- ML/prediction unavailable → fallback allocation policy (e.g., rule-based).
- Invalid simulator response → reject input, raise alert (don't silently corrupt state).
- Prediction confidence too low → request human review instead of auto-acting.
- Backend dependency (simulator) unavailable → retry with backoff, serve cached/last-known-good state, enter a visibly degraded mode.

Must also demonstrate response to the simulator's actual fault types (`latency`, `unavailable`, `error_rate`, `stale_data`, `stream_disconnect`) and crisis event types (`demand_spike`, `route_disruption`, `station_outage`, `depot_constraint`, `shipment_delay`, `supply_shortfall`), including a **combined** crisis.

## Observability requirements

Application layer (request rate, latency incl. p95, error rate, service availability), system layer (CPU/memory, optional depth), intelligence layer (prediction error, model confidence, shortage-alert rate, decision frequency, fallback activation rate), and logs of important actions, integration failures, decision events, and recoveries. Distributed tracing optional. Health/status view must be judge-legible (component-by-component health + key numbers), matching the §15 example format.

## Deployment requirements

Must be reproducibly runnable, preferably containerized, minimum bar `docker compose up` (or documented equivalent) producing a running, health-checked application. Source→Build→Test→Package→Deploy→Health Check→Running Application delivery flow should be demonstrable; CI/CD is recommended, not mandatory. Kubernetes/Helm/IaC/GitOps/autoscaling/blue-green/canary are explicitly optional "advanced" work — do not add unless it improves the solution.

## Load-testing requirements

Load-test at least one meaningful application path end-to-end (a prediction call, a decision/allocation call, the simulator integration itself, the dashboard backend, or a full decision request). Report avg/p50/p95/p99 latency, throughput, error rate, concurrency, and resource usage. Emphasis is on understanding real behavior/limits, not hitting an arbitrary number.

## Security / engineering-hygiene requirements

No hard-coded secrets; validate all external input (including simulator responses, since it can be told to misbehave); handle failed requests gracefully; document required configuration (env vars, ports, credentials if any); never expose credentials; restrict sensitive operator actions (e.g., who/what can trigger a real allocation) where appropriate. Enterprise-grade security is explicitly not expected.

## Required deliverables (§19)

1. Working, runnable end-to-end application.
2. Source repository with setup, dependency, and deployment instructions.
3. Demonstrated integration with the official simulator.
4. At least one meaningful AI/ML/optimization/detection/decision-support capability.
5. A usable operator interface with meaningful operational information.
6. Architecture diagram: simulator → data/backend → intelligence → decision → application → monitoring.
7. Reproducible deployment method.
8. Observability evidence (logs, metrics, dashboards, or alerts).
9. Resilience demonstration against ≥1 real failure condition.
10. Load-test evidence (workload definition + measured results).
11. A live or judge-supervised final demo.

Recommended (not required, score-additive/robustness): CI/CD, automated tests, experiment tracking, model versioning, decision audit history, deployment versioning, simulation replay, scenario configuration, automated fallback, rollback.

Optional/advanced (do not chase for its own sake): RL, multi-agent systems, optimization+ML hybrids, uncertainty-aware allocation, counterfactual simulation, automated incident detection, policy rollback, drift detection, event-driven/streaming architecture, Kubernetes/autoscaling, GenAI ops assistants.

## Evaluation criteria (§23, total 100%)

| Criterion | Weight |
|---|---|
| Working Product & User Experience | 20% |
| Intelligence & Decision Quality | 20% |
| Architecture & Integration | 15% |
| DevOps & Engineering Quality | 15% |
| Resilience & Incident Response | 10% |
| Observability & Performance | 10% |
| Demo & Problem Understanding | 10% |

## Important simulator constraints and failure modes (engineering constraints, not suggestions)

- `/admin/*` and `/v1/health` bypass all fault injection — only `/v1/*` (non-health) paths can be faulted; use `/admin/*` freely for our own deterministic self-tests.
- Faults auto-expire (`duration_seconds` ≤ 3600) but can be cleared early via `/admin/faults/clear`.
- `unavailable` and `error_rate` faults return HTTP 503 with `{"error": {...}}` (note: **not** the `{"detail": {...}}` shape used by ordinary 4xx validation errors) — client error parsing must handle both shapes.
- `stale_data` fault does not block requests; it adds `X-Simulator-Stale: true` to `/v1/*` GET responses — must be checked and surfaced, not ignored.
- `stream_disconnect` fault makes `GET /v1/stream` return 503 `{"detail": {"code": "FAULT_INJECTED"}}` — SSE consumer must back off and retry, and the rest of the system must keep functioning on REST polling alone during this.
- SSE has no replay and a 200-event queue cap per subscriber — treat every reconnect as "state unknown, refetch everything."
- `demand-history` table grows unboundedly; must always call with `limit` and reason over a bounded recent window.
- Allocation `idempotency_key` is permanently consumed (even by cancelled allocations) — key generation strategy must guarantee uniqueness without needing to "free" keys.
- The scenario, seed, and initial world (2 regions, 2 depots, 4 stations, 6 routes, 3 fuel types, fixed demand profiles/hour-of-day factors, fixed 22-arrival supply schedule) are fixed and documented in the integration guide §8 — usable directly for building/testing forecasting and allocation logic before the live event.
- Organizers may inject additional surprise domain/engineering events during development or judging beyond what we script ourselves — the system should be robust to fault/event types it's already told about, not just the ones we chose to demo.
