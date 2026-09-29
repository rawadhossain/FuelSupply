# Research log

Research only requirements that matter to the MVP. Prefer primary/official sources. Record enough to reproduce a choice; do not implement here.

## Resolved by SPEC.md

RES-001 to RES-005 (backend, frontend, storage, observability, deployment) were compared earlier against the 8-hour constraint. The team chose the stack in `/SPEC.md`, recorded as ADR-002. The earlier comparison, kept as history:

| ID | Topic | Earlier recommendation | Team decision (SPEC.md) | Note |
|---|---|---|---|---|
| RES-001 | Backend | Python/FastAPI | Python/FastAPI | Match |
| RES-002 | Frontend | Plain HTML/JS served by backend (lowest risk) | React + TS + Vite | Team knows React. Costs a separate frontend container/build; RISK-005 stands. |
| RES-003 | Storage | In-memory + SQLite | PostgreSQL + Redis | Heavier than needed at single-instance scale; kept by team decision, Redis first to cut. |
| RES-004 | Observability | Minimal `/status` first, Grafana later | Prometheus + Grafana + structlog | Fine, but the `/health` aggregation page must ship first; Grafana is polish on top. |
| RES-005 | Deployment | Docker Compose | Docker Compose | Match |

## Open research items

| ID | Requirement | Question | Official source | Capabilities & limits to confirm | Fallback | Decision |
|---|---|---|---|---|---|---|
| RES-006 | REQ-008 / ADR-005 | Which Anthropic model, timeout, and per-request cost/token cap for narration? Streaming needed? | Anthropic API docs (use the `claude-api` skill) | Latency for a short structured-to-prose call; rate limits; API key handling in Compose | Templated narration string | OPEN |
| RES-007 | REQ-007 | Forecast approach per station/fuel: seasonal-naive baseline vs XGBoost. Use the fixed hour-of-day factors and demand profiles in guide §8.5–8.6 as features; how much `demand-history` exists early in a run (cold start)? | scikit-learn / XGBoost docs; simulator guide §4.11, §8 | Cold-start behavior, retrain cadence, confidence estimate (e.g. quantile or residual-based) | Seasonal-naive baseline | RESOLVED 2026-09-29: time-of-day profile × event multiplier schedule × EWMA correction; split-conformal intervals. MSTL (MAE 4.30) and LightGBM (+2% 1-step only) benchmarked, not adopted. See `docs/ml-architecture.md` §3, §12. |
| RES-008 | REQ-007 | OR-Tools vs PuLP for the v2 allocator (small problem: 2 depots x 4 stations x 3 fuels, 6 routes) | OR-Tools / PuLP docs | Solve time per tick, install size in Docker image, handling of dispatch-per-tick and route max constraints | Heuristic allocator | RESOLVED 2026-09-29: neither — SciPy `linprog` (HiGHS) solves 24 h rolling LP in 0.077 s with no new dependency; LP is primary policy, heuristic fallback. See `docs/ml-architecture.md` §4, §12. |
| RES-009 | REQ-013 | Metric set and Grafana provisioning (dashboards as code) for both services | Prometheus / Grafana docs | Avoid hand-built dashboards that don't survive `docker compose up` | `/health` page + raw `/metrics` | OPEN |
| RES-010 | REQ-004 | `httpx-sse` behavior on 503, dropped connections, and 15 s keepalive comments; confirm reconnect with backoff | `httpx-sse` docs; guide §6 | Silent drop when the 200-event queue overflows: detect via tick gaps / missing `simulation.tick` | REST polling only | OPEN |
