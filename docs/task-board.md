# Task board

No substantial implementation without a task. Use one ID consistently. Move only owned tasks; record blockers rather than guessing. Owners are roles from SPEC.md §7 until names are assigned: **INT** integration, **INTEL** intelligence, **FE** frontend, **OPS** DevOps.

Stack is ACCEPTED (ADR-002 to ADR-006). Tasks follow SPEC.md §8 phases. Phase 0 is READY; later phases stay in BACKLOG until the contracts they depend on are written into `docs/api-contracts.md`.

## BACKLOG

| ID | Req | Description | Owner | Depends on | Files / components | Acceptance criteria |
|---|---|---|---|---|---|---|
| TASK-010 | REQ-002 | Simulator REST client: typed wrappers for all `/v1/*` reads, both error envelopes normalized, bounded `demand-history` calls (always `limit`) | INT | TASK-002 | core/simulator_client | Each endpoint parses against a live simulator; malformed payload rejected with alert |
| TASK-011 | REQ-003, 004 | Poller + SSE listener: re-GET on every event, backoff reconnect, full resync after reconnect, detect silent drop via tick gaps | INT | TASK-010 | core/simulator_client | With `stream_disconnect` fault, state keeps updating by polling and fully resyncs on recovery |
| TASK-012 | REQ-005, 006, 010 | Allocation executor: deterministic keys per ADR-004 (quantity + attempt counter), all 404/409/422 codes, cancel, follow-up REST confirm | INT | TASK-010 | core/allocation_executor | Replay returns existing record (200 or 201); mismatch gives 409; post-FAILED retry uses a new key |
| TASK-013 | REQ-022 | Postgres schema + writer: allocations mirror, predictions, audit log, demand snapshots; Redis latest-state cache | INT | TASK-010 | core/store | Rows match REST after a run; simulator wins on conflict |
| TASK-014 | REQ-007 | Baseline forecast per station/fuel (seasonal-naive), then XGBoost if it beats baseline; confidence estimate | INTEL | TASK-010, RES-007 | intelligence/forecast | Forecast + confidence for all 12 station/fuel pairs from bounded history |
| TASK-015 | REQ-007 | Z-score anomaly detection on demand and inventory deltas | INTEL | TASK-010 | intelligence/anomaly | Injected `demand_spike` flagged within a few ticks |
| TASK-016 | REQ-007, 008, 009 | Heuristic allocator (hours-to-stockout ranking, nearest eligible depot, dispatch/route/capacity limits) as shared module used by Intelligence and by Core fallback | INTEL | TASK-014 | shared/allocator | Recommendation passes simulator validation order in dry-run; works with Intelligence Service down |
| TASK-017 | REQ-008 | Recommendation object: signals, constraints, expected impact (risk before/after), confidence, at least one alternative; templated narration | INTEL | TASK-016 | intelligence/recommend | All fields present; renders without the LLM |
| TASK-018 | REQ-035 | LLM narration wrapper (ADR-005): finished object in, prose out, timeout, template fallback | INTEL | TASK-017, RES-006 | intelligence/narrate | Numbers in prose match the object; API blocked gives template text |
| TASK-019 | REQ-030 | Optimization allocator v2 (OR-Tools/PuLP), benchmarked against heuristic on service_level / unmet demand | INTEL | TASK-016, RES-008 | intelligence/optimizer | Benchmark table in `docs/verification.md`; keep only if it wins |
| TASK-020 | REQ-001 | Dashboard against mocked Core contract: status grid, alerts feed, recommendation review/approve, decision history, simulated-data labelling | FE | TASK-003 | frontend | Renders from mock; then from live Core with no code change |
| TASK-021 | REQ-001, 014 | WebSocket client, degraded/stale banners, health page view | FE | TASK-020, TASK-030 | frontend | Banner appears under `unavailable` and `stale_data` faults |
| TASK-030 | REQ-009 | Resilience wiring: tenacity backoff, circuit breaker on simulator and Intelligence calls, cached-state degraded mode, low-confidence human-review flag | INT + INTEL | TASK-010, 016 | core/resilience | Each of the four SPEC.md §9 conditions reproducible and observed |
| TASK-032 | REQ-026 | Grafana dashboard provisioned as code | OPS | TASK-031 | ops/grafana | Dashboard appears after `docker compose up` with no manual steps |
| TASK-033 | REQ-015 | Locust load test on allocation/decision path; record avg/p50/p95/p99, throughput, error rate, concurrency, resources | OPS | TASK-012 or 017 | loadtest/ | Results recorded in `docs/verification.md` |
| TASK-034 | REQ-021 | pytest contract tests for every documented error code, replay/mismatch, fault handling; use `/admin/reset` + `/admin/step` | INT | TASK-012 | tests/ | Suite green in CI |
| TASK-035 | REQ-011 | Scripted crisis rehearsal (`/admin/events`, `/admin/faults`) per `docs/demo-flow.md`, run at least twice | ALL | TASK-030, 021 | scripts/demo | Steps verified in `docs/verification.md` |
| TASK-036 | REQ-017 | README, `.env.example`, architecture diagram, submission checklist | OPS | ALL | README, docs | All 11 deliverables mapped to artifacts |

## READY

| ID | Req | Description | Owner | Depends on | Files / components | Acceptance criteria |
|---|---|---|---|---|---|---|
| TASK-003 | REQ-001, 008 | Write CONTRACT-CORE-API (REST + WebSocket payloads for dashboard) and CONTRACT-INTEL-OUTPUT (forecast, risk, recommendation) so FE and INTEL can work in parallel | INT + INTEL + FE | TASK-002 | docs/api-contracts.md | Contracts reviewed by all three owners; FE mock matches them |

## IN PROGRESS
| ID | Req | Description | Owner | Depends on | Files / components | Acceptance criteria |
|---|---|---|---|---|---|---|

## BLOCKED

| ID | Req | Blocker | Owner | Needed from | Proposed alternative |
|---|---|---|---|---|---|
| TASK-018 | REQ-035 | Anthropic model, timeout, cost cap not chosen (RES-006); API key not provisioned | INTEL | Team | Build the template path first; wire the LLM after |

## REVIEW
| ID | Req | Description | Owner | Reviewer | Verification evidence |
|---|---|---|---|---|---|
| TASK-031 | REQ-013, 014 | Shared observability module (Prometheus metrics, structlog JSON logging, `/metrics`, `/ready`); one import + `setup_observability(...)` call added to `core/app/main.py` and `intelligence/app/main.py`; `/health` untouched; aggregated status page deferred to TASK-032/FE | OPS (Claude, this session) | Unassigned | VER-006, VER-007 in `docs/verification.md` |

## DONE
| ID | Req | Description | Owner | Evidence | Remaining risk |
|---|---|---|---|---|---|
| TASK-000 | — | Re-planned project docs around the real challenge and folded in SPEC.md decisions (stack, two-service split, idempotency, LLM scope) | Claude planning session | `docs/*.md`, 2026-09-29 | No code yet; no contracts written yet (TASK-002/003) |
| TASK-001 | ADR-002 | Stack decision | Team | SPEC.md §5–6, ADR-002 ACCEPTED | Open items listed at bottom of `docs/decisions.md` |
| TASK-002 | REQ-002 | Ran simulator standalone, explored `/admin`, wrote `shared/fuelsupply_shared/models.py` and CONTRACT-SIM-REST/CONTRACT-SIM-ALLOC in `docs/api-contracts.md` | INT (Claude, Phase 0 session) | VER-002, VER-003, VER-004 in `docs/verification.md`; ASM-010 resolved | None blocking — models cover every documented endpoint; CONTRACT-CORE-API / CONTRACT-INTEL-OUTPUT (TASK-003) still to write before FE/INTEL start |
| TASK-004 | REQ-012, 020 | Repo scaffold (core/intelligence/frontend/shared skeletons), `docker-compose.yml` (8 services), healthchecks + depends_on, `.env.example`, `.gitignore`/`.dockerignore`, GitHub Actions CI skeleton | OPS (Claude, Phase 0 session) | VER-001, VER-005 in `docs/verification.md`: clean `docker compose up -d --build` → all 8 containers healthy; CI's own lint/test/build commands all pass | Prometheus scrape targets are `down` until TASK-031 adds `/metrics` routes (expected, not a defect). CI workflow not yet run in GitHub Actions itself (no push/PR triggered) — commands verified locally/in containers only. Frontend/core/intelligence are empty skeletons — no real functionality yet |
