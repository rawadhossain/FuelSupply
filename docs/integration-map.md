# Integration map

Track boundaries, owners, and what must be checked after merge.

| Integration | Producer | Consumer | Contract | Dependency state | Test / evidence | Risk |
|---|---|---|---|---|---|---|
| Simulator REST read | BUP Fuel Supply Simulator `/v1/*` GETs | Our backend state-sync layer | See `docs/api-contracts.md` CONTRACT-SIM-REST (to be finalized once stack is chosen) | PLANNED | Poll each endpoint against a running simulator; assert shape matches Guide §4 examples. | RISK-006 (unbounded demand-history), RISK-004 (error envelope shapes) |
| Simulator SSE notifications | Simulator `/v1/stream` | Our backend SSE listener | Advisory only — every event triggers a REST re-GET of the named resource; never a data source itself | PLANNED | Inject `allocation.status_changed` via a manual allocation; confirm displayed state comes from a follow-up GET, not the event body. | RISK-001 |
| Allocation write | Our decision engine | Simulator `POST /v1/allocations` (+ `/cancel`) | See `docs/api-contracts.md` CONTRACT-SIM-ALLOC | PLANNED | Submit, retry with same key/body (expect 200/201 replay), retry with same key/different body (expect 409). | RISK-002 |
| Core to Intelligence Service | Core Service | Intelligence Service (FastAPI) | CONTRACT-INTEL-OUTPUT (TASK-003); timeout + circuit breaker; fallback heuristic in Core | PLANNED | Stop the Intelligence container; recommendations still appear, marked "fallback policy". | RISK-013 |
| Core to dashboard | Core Service REST + WebSocket | React dashboard | CONTRACT-CORE-API (TASK-003); FE builds against a mock first | PLANNED | Dashboard renders from mock, then live Core, with no code change. | RISK-005 |
| LLM narration | Intelligence Service | OpenAI API | Finished recommendation object in, prose out; timeout; template fallback (ADR-005) | IMPLEMENTED (`intelligence/narrate.py`) | Block the API; recommendation still renders with template text; numbers in prose match the object. | RISK-014 |
| Intelligence layer output | Forecasting/detection/optimization component | Decision engine + operator UI | See `docs/api-contracts.md` CONTRACT-INTEL-OUTPUT | PLANNED | Given fixed demand-history input, forecast/risk output is inspectable with signals + confidence per REQ-008. | RISK-007 |
| Fault/degraded-mode signal | Simulator client (detects `X-Simulator-Stale`, 503 `FAULT_INJECTED`, timeouts) | Health/status view + decision engine (fallback trigger) | See `docs/api-contracts.md` CONTRACT-HEALTH | PLANNED | Inject each fault type via `/admin/faults`; confirm status view and decision engine both react per REQ-009. | RISK-003 |
| Admin self-test harness | Our test/demo scripts | Simulator `/admin/*` (run/pause/step/reset/events/faults) | Bypasses fault injection by design — used for deterministic setup, not production behavior | PLANNED | Scripted demo sequence using `/admin/step` for reproducible ticks. | RISK-009, RISK-010 |
| Observability export | Backend metrics/log emitters | Health/status page (+ optional Prometheus/Grafana) | See `docs/api-contracts.md` CONTRACT-METRICS | PLANNED | Manual load test produces visible p50/p95/p99, error rate, fallback-activation count. | RISK-005 |

Contracts referenced above are placeholders (`CONTRACT-*` IDs) to be written into `docs/api-contracts.md` by TASK-002 and TASK-003 (stack is accepted, ADR-002). Do not implement against a contract until it exists there.
