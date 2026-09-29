# Scope cut list

When behind schedule, protect a tested end-to-end core journey, mandatory AI/API integration, and demo fallback. Cut deliberately; update requirements and tasks.

## NEVER CUT

- Required submission and judging constraints from `docs/problem.md` (all MUST requirements REQ-001…REQ-017).
- The tested core loop: OBSERVE (simulator state ingestion) → DETECT/PREDICT (intelligence layer) → DECIDE (inspectable recommendation) → ACT (allocation write) → MONITOR (health/status) → RECOVER (one resilience path demonstrated).
- Treating REST as source of truth / SSE as advisory-only (Guide hard rule) — cutting this correctly is free (it's a design discipline, not a feature) and its absence is an instant credibility loss with judges who wrote the guide.

## MVP

| Capability | Requirement | Why essential | Current state |
|---|---|---|---|
| Simulator REST client with polling refresh | REQ-002 | Nothing else works without live state. | UNPLANNED — blocked on ADR-002 |
| SSE listener (advisory refetch trigger) | REQ-003, REQ-004 | Explicit hard rule in the guide; also enables snappier demo updates. | UNPLANNED |
| Allocation write path with idempotency + error handling | REQ-005, REQ-006, REQ-010 | The only way the platform can act; judged directly. | UNPLANNED |
| One forecasting/detection capability | REQ-007 | Mandatory intelligence requirement. | UNPLANNED |
| Inspectable allocation recommendation | REQ-008 | Directly judged under Intelligence & Decision Quality (20%) and Decision Support (Problem §9). | UNPLANNED |
| One resilience path fully demonstrable (recommend: `unavailable`/`error_rate` fault → degraded mode → recovery) | REQ-009 | Mandatory resilience deliverable; simplest single fault to script reliably via `/admin/faults`. | UNPLANNED |
| Minimal operator UI (≥6 of 12 data points) | REQ-001 | Mandatory application requirement; "notebook alone" is disqualifying. | UNPLANNED |
| `/health` + `/status` view | REQ-013, REQ-014 | Mandatory observability/health deliverable. | UNPLANNED |
| Docker Compose deployment | REQ-012 | Mandatory deployment deliverable. | UNPLANNED |
| One load test with recorded evidence | REQ-015 | Mandatory deliverable; can be run once, doesn't need to be live in the demo. | UNPLANNED |
| Basic input validation + no hard-coded secrets | REQ-016 | Mandatory hygiene deliverable; cheap to do right from the start. | UNPLANNED |

## CUT ORDER (SPEC.md stack is larger than the minimal design; cut infrastructure before product)

1. Grafana dashboards (keep Prometheus `/metrics` and the app's own health page)
2. Redis pub/sub (Core pushes to WebSocket directly; cache in-process)
3. Optimization allocator v2 (heuristic alone satisfies the requirement)
4. CI beyond lint + test
5. Charts (tables are enough)
6. Never cut: heuristic allocator/fallback, allocation executor, health page, one demonstrated fault, load-test evidence, Compose startup.

## STRETCH / CUTTABLE

| Capability | Requirement | Cut trigger | How to remove safely | Owner |
|---|---|---|---|---|
| CI/CD pipeline | REQ-020 | <2 hours left and MVP not yet fully verified | Deploy manually via documented `docker compose up`; note CI/CD as future work in README. | TBD |
| Automated test suite | REQ-021 | Time pressure on MVP resilience/intelligence work | Keep manual verification evidence in `docs/verification.md` instead. | TBD |
| Second resilience path (beyond the one in MVP) | REQ-009 (extra scenarios) | MVP loop not yet demoed once end-to-end | Demonstrate only the one rehearsed fault; mention others as "also handled" with code reference, not live demo. | TBD |
| Combined-crisis demo (two simultaneous events) | REQ-011 | Single-event demo not yet reliable | Demo a single crisis type only; document combined-crisis handling as designed-but-not-rehearsed. | TBD |
| Prometheus/Grafana dashboards | REQ-026 | `/status` page already satisfies REQ-013/014 and time is short | Keep the lightweight `/status` view as the only observability surface. | TBD |
| Next.js/React frontend (if Option B/C chosen) | REQ-001 (presentation only) | Frontend build/tooling eating >2h without working data flow (RISK-005) | Fall back to the plain server-rendered/vanilla-JS dashboard (Option A) serving the same data. | TBD |
| Decision audit history UI (beyond raw log) | REQ-022 | Time pressure late in build | Keep audit trail in SQLite/logs only, not surfaced in UI, and say so if asked. | TBD |
| Reinforcement learning allocation policy | REQ-030 | Any time pressure at all | Never start; REQ-007 is already satisfied by forecasting + heuristic/optimization allocation. | TBD |
| Kubernetes / cloud deployment | REQ-034 | Any time pressure at all | Never start; Docker Compose already satisfies REQ-012. | TBD |
| LLM narration (in scope per ADR-005) | REQ-035 | API key missing, venue network unreliable, or narration contradicts structured data | Serve templated explanation strings; satisfies REQ-008 on its own. | Intelligence |
