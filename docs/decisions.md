# Architecture decisions

Record a decision before it becomes a dependency. Supersede—do not erase—an accepted decision.

Source for ADR-002 to ADR-006: `/SPEC.md` (team specification, written by a teammate). SPEC.md §5–§6 is the decision record; this file indexes it and records consequences and risks. If they disagree, update both together.

## ADR-001 — Actual challenge supersedes any prior placeholder scenario

- **Status:** ACCEPTED
- **Decision:** `docs/problem.md` (from `/ProblemStatement.md` and `/Fuel_Supply_Simulator_Integration_Guide.md`) is the sole source of truth for scope. Any earlier agriculture-scenario placeholder is void.
- **Owner / date:** Team, 2026-09-29.

## ADR-002 — Technology stack

- **Status:** ACCEPTED (per SPEC.md §6). Supersedes the earlier PROPOSED Option A/B/C analysis, which is kept in `docs/research.md` as history.
- **Decision:**

| Layer | Choice |
|---|---|
| Backend | Python 3.11+, FastAPI (async) |
| Simulator client | `httpx` + `httpx-sse`, `tenacity` for retry/backoff |
| Database | PostgreSQL (allocations, predictions, audit log, demand snapshots) |
| Cache / pub-sub | Redis (latest-state cache, fan-out to frontend) |
| Forecasting / detection | scikit-learn, XGBoost |
| Optimization | OR-Tools or PuLP (v2, benchmarked against heuristic) |
| LLM | OpenAI API, narration/explanation only (changed from Anthropic on 2026-09-29, team decision) |
| Frontend | React + TypeScript + Vite; Recharts / Observable Plot |
| Browser realtime | WebSocket from our backend (not the simulator SSE) |
| Metrics / logs | Prometheus + Grafana; `structlog` |
| Load test | Locust |
| CI/CD | GitHub Actions |
| Deploy | Docker Compose |
| Tests | pytest + httpx test client |

- **Reason (SPEC.md):** Python is the team's strongest language; async fits polling + SSE; team knows React.
- **Consequences:** Resolves ASM-006 and RES-OPEN-2 (an LLM is wanted). Compose grows to about 8 containers: simulator, core, intelligence, frontend, Postgres, Redis, Prometheus, Grafana. That is more infrastructure than the earlier minimal recommendation, so it is tracked as RISK-011 and ordered in `docs/cut-list.md`.
- **Requirements / research:** REQ-001…REQ-017, REQ-020…REQ-026, REQ-035.

## ADR-003 — Two backend services (Core + Intelligence)

- **Status:** ACCEPTED (per SPEC.md §5).
- **Decision:** **Core Service** (FastAPI) owns everything simulator-facing: poller, SSE listener, Postgres writer, allocation submission (idempotency, error handling), REST/WebSocket API to the frontend. **Intelligence Service** (FastAPI, stateless-ish) owns forecasting, anomaly detection, allocation optimization, and LLM narration. The frontend never talks to the simulator directly. Both services are scraped by Prometheus.
- **Reason:** Idempotency and fault handling live in one place; the intelligence layer can be swapped (heuristic → optimization → RL) without touching integration code; enough separation for 4–5 people to work in parallel.
- **Consequences:** Adds a service-to-service contract (CONTRACT-INTEL-OUTPUT) and a new failure mode: Intelligence unreachable. That maps directly to the "ML model unavailable → fallback policy" requirement, so the fallback heuristic must be callable from Core without the Intelligence Service.
- **Open:** Where the fallback heuristic lives. Recommended: a small shared Python module that Core imports directly, so fallback works when the Intelligence container is down. Needs team confirmation (see Open items).

## ADR-004 — Deterministic idempotency keys

- **Status:** ACCEPTED (per SPEC.md §4.5), with refinements required.
- **Decision:** Keys are a hash of depot + station + fuel + tick + intent, not a random UUID per HTTP call, so a retry after a dropped connection deduplicates.
- **Required refinements (not in SPEC.md, derived from the simulator guide §5.4):**
  1. **Include `quantity` (and `route_id`) in the hashed intent.** Same key + different body returns 409 `IDEMPOTENCY_KEY_MISMATCH`, and the first submission wins. If quantity is not in the hash, a re-planned quantity in the same tick collides.
  2. **Add an `attempt`/generation counter to intent.** A FAILED allocation (e.g. route disrupted at departure) is not retriable under the same key; a deliberate re-attempt for the same tick needs a new key. Network retries reuse the key; deliberate re-decisions increment the counter.
  3. Keys are permanently consumed, including by cancelled allocations.
  4. Treat both 200 and 201 as success on replay. The guide's §5.4 says 201 while its status table says 200.
- **Owner:** Integration engineer.

## ADR-005 — LLM is narration only

- **Status:** ACCEPTED (per SPEC.md §6, §8 Phase 2).
- **Decision:** The OpenAI API (changed from Anthropic, 2026-09-29) wraps a finished recommendation object (station, projected stockout, recommended allocation, expected impact) into human-readable text. It never chooses allocations or quantities.
- **Consequences:** The LLM is an optional dependency. Every narration path needs a timeout and a templated non-LLM fallback so an API or network failure cannot block a recommendation (REQ-008 must be satisfied without it). The API key comes from an env var, never committed (REQ-016). Model ID and cost limits are RES-006.

## ADR-006 — Allocator progression: heuristic first, optimization second, RL optional

- **Status:** ACCEPTED (per SPEC.md §8 Phase 2, §14).
- **Decision:** Ship the heuristic allocator first (rank stations by hours-to-stockout, allocate from the nearest eligible depot within dispatch/route/capacity limits); this satisfies the intelligence requirement alone. The OR-Tools/PuLP allocator is v2 and must be benchmarked against the heuristic to justify itself. RL only if it demonstrably beats both.
- **Consequences:** The heuristic doubles as the resilience fallback, so it is MVP-critical regardless of what follows.

## ADR-007 — Two simulator-contract model definitions kept separate, not unified

- **Status:** ACCEPTED (as a deliberate deferral, per `abrar/sync` integration 2026-09-29).
- **Decision:** `shared/fuelsupply_shared/models.py` (Core's models — strict, live-verified field-by-field against the running simulator, used by `core/app/simulator_client`) and `intelligence/models.py` (Intelligence's own `SimSnapshot`/`Instance`/`Station`/etc — looser, `extra="allow"`, feeding its internal `Snapshot` working representation) both parse the same simulator JSON but are separate, independently-maintained Pydantic model sets. They are not merged into one shared definition.
- **Reason:** Intelligence's 53+ tests (built and passing before this integration) depend on its own model shapes and the internal `Snapshot` conversion (`SimSnapshot.to_snapshot()`) they feed. Rewriting Intelligence to consume `fuelsupply_shared.models` instead would touch `assess.py`, `signals.py`, `policy_lp.py`, and every test fixture that constructs a `Snapshot` — a real refactor with real regression risk, not something to do silently while integrating two branches under time pressure.
- **Consequences:** A simulator field that changes shape must be updated in two places, not one. If a future session has time for a dedicated refactor, the direction would be: make `intelligence.models.SimSnapshot` parse from `fuelsupply_shared.models` instances (or replace it outright) rather than re-declaring the same fields. Not urgent — the simulator's schema is fixed for the event per the integration guide.

## Open items (need team input, not blocking Phase 0–1)

1. Fallback heuristic location (ADR-003) — resolved in practice: `intelligence/heuristic.py`, callable by Core directly per `intelligence/service.py`'s own 503 fallback hint, without needing the Intelligence container up.
2. ~~OpenAI model choice and per-request timeout/cost cap (RES-006).~~ Resolved: `OPENAI_MODEL` (default `gpt-5.4-mini`), 8 s timeout, 400 output tokens, 1 retry — all in `.env`.
3. Is Redis pub/sub actually needed at one Core instance? SPEC.md specifies it; it is kept, but it is first in line for cutting (see `docs/cut-list.md`).
4. ADR-007's model duplication — revisit if a dedicated refactor window opens; not blocking.
