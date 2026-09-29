# Architecture

Governed by `docs/decisions.md` ADR-002 to ADR-006, which source `/SPEC.md`. Requirement IDs refer to `docs/requirements.md`.

## Component graph

```
 BUP Fuel Supply Simulator (given)            Prometheus ─► Grafana
  /v1/*  /admin/*                                  ▲  scrape
        ▲  │ REST (truth) + SSE (hint)             │
        │  ▼                                       │
 ┌──────────────────────────────┐  REST   ┌────────┴─────────────────┐
 │ Core Service (FastAPI)       │◄───────►│ Intelligence Service     │
 │ poller + SSE listener        │         │ (FastAPI)                │
 │ Postgres writer              │         │ forecast, anomaly,       │
 │ allocation executor          │         │ allocator, LLM narration │
 │ REST + WebSocket API         │         └──────────────────────────┘
 └───────┬───────────┬──────────┘
         │           │ pub/sub, latest-state cache
    Postgres       Redis
         │
  WebSocket / REST
         ▼
 React + TS + Vite operator dashboard
```

## Components

| Component | Responsibility | Technology | Requirement IDs |
|---|---|---|---|
| Simulator client (in Core) | Poll REST, listen to SSE, re-GET on every event; normalize both error envelopes (`detail` vs `error`); detect `X-Simulator-Stale`; retry/backoff/timeout | httpx, httpx-sse, tenacity | REQ-002, 003, 004, 010 |
| State store (in Core) | Postgres for allocations, predictions, audit log, demand snapshots; Redis for latest-state cache and fan-out | PostgreSQL, Redis | REQ-002, 022 |
| Intelligence Service | Per-station/fuel forecast, z-score anomaly detection, heuristic then optimization allocator, LLM narration | scikit-learn, XGBoost, OR-Tools/PuLP, Anthropic API | REQ-007, 008, 035 |
| Fallback allocator | Deterministic heuristic that works when Intelligence is down | Python module (location: open item) | REQ-009 |
| Allocation executor (in Core) | Only writer to `POST /v1/allocations` and `/cancel`; deterministic keys (ADR-004); confirms each write with a follow-up REST read | FastAPI, httpx | REQ-005, 006 |
| Core API | REST + WebSocket for the dashboard; the frontend never reaches the simulator | FastAPI | REQ-001 |
| Operator dashboard | Status grid (depots/stations/routes), alerts feed, recommendation review/approve, decision history, health page; labelled as simulated data | React, TS, Vite, Recharts | REQ-001, 008, 014 |
| Observability | App, system, and intelligence metrics; structured logs; health aggregation; Grafana dashboard | Prometheus, Grafana, structlog | REQ-013, 014, 026 |
| Load test | Locust against the allocation/decision path | Locust | REQ-015 |
| CI/CD | Lint, test, build, push | GitHub Actions | REQ-020 |

## Flows and boundaries

- **Steady state:** Core polls REST and re-GETs the affected resource on each SSE event, then upserts Postgres and refreshes the Redis snapshot, then pushes over WebSocket to the dashboard. Core asks the Intelligence Service for forecasts, risk, and recommendations from bounded recent history; the dashboard shows them.
- **Decision to action:** Operator approves a recommendation, and Core's executor submits an allocation with a deterministic key. The response (201/200/409/...) is reconciled into Postgres and the audit log, and the dashboard follows PENDING → IN_TRANSIT → ARRIVED/FAILED. Auto-submit is off by default (human review of consequential decisions).
- **AI flow:** Intelligence Service input is Core-supplied state and history; output is forecast/risk with confidence, ranked allocation candidates, and a narration string. It has no simulator access. Optimization output is compared with the heuristic (ADR-006).
- **External calls:** Simulator (no auth; explicit timeout on every call) and Anthropic API (isolated, timeout, templated fallback). Anthropic key via env var only.
- **Startup:** Both services must tolerate `SIMULATOR_START_MODE=paused` and a not-yet-ready simulator without crashing; health checks gate Compose startup order.
- **Testing:** Use `/admin/reset` + `/admin/step` for deterministic integration tests, not wall-clock sleeps.

## Failure and fallback paths

| Failure | User-visible behavior | Fallback | Owner | Verified |
|---|---|---|---|---|
| Simulator `unavailable` / `error_rate` (503) | Status page: simulator degraded; last-known-good state shown with age | Retry with backoff, circuit breaker, cached state from Redis/Postgres; new submissions blocked or clearly queued | Integration | NO |
| Simulator `latency` | Slower updates; p95 metric rises | Per-call timeout ceiling; loading/degraded indicator | Integration | NO |
| `stale_data` header | Affected panels flagged stale | Stale inputs lower confidence; may trigger human review | Integration | NO |
| `stream_disconnect` / SSE queue overflow | Updates continue at polling freshness | Backoff reconnect; full REST resync on reconnect | Integration | NO |
| Invalid simulator response | Alert raised; input rejected | Pydantic validation at the client boundary; state not corrupted | Integration | NO |
| Intelligence Service down or model error | Recommendation still produced, marked "fallback policy" | Fallback heuristic in Core | Intelligence | NO |
| Low prediction confidence | Recommendation flagged "human review required" | Manual approval path | Intelligence + Frontend | NO |
| Anthropic API down or slow | Recommendation shown with templated explanation | Templated narration | Intelligence | NO |
| Postgres or Redis down | Health page red for that component; dashboard serves what it can | Redis down: read direct from Postgres/simulator. Postgres down: degraded mode, audit writes buffered or logged | DevOps | NO |
| Allocation 409 family | Specific reason shown (e.g. route disrupted) | Suggest alternate route/depot/quantity | Integration + Intelligence | NO |
