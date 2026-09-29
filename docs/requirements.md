# Requirements (Revision 2 — 2026-09-29)

Derived from the official brief (`/ProblemStatement.md` = BUP-Q-Set.pdf) and the simulator guide (`/Fuel_Supply_Simulator_Integration_Guide.md`). Team decisions live in `/SPEC.md`, `docs/decisions.md`, `docs/architecture.md`, `docs/ml-architecture.md`.
Priority: **MUST** (viable submission), **SHOULD** (score-additive), **COULD** (only after MUST+SHOULD verified). One testable requirement per row. IDs from Rev 1 are kept; new IDs are marked **NEW**. See the change log at the end.

## MUST

| ID | Type | Requirement | Source | Acceptance criteria | Depends | Status |
|---|---|---|---|---|---|---|
| REQ-001 | UX | Web operator app showing, from live simulator-derived state, at least: station & depot inventory by fuel, depot/station/route status, shortage alerts with projected stockout, incoming supply, active disruptions, recommended allocations with expected impact, decision history, service health. (8 of the brief's 12 items; regional demand and system alerts are SHOULD via REQ-027.) | Brief §6 | A judge opens the app and finds each listed item within 2 clicks, without reading code. | REQ-002 | UNPLANNED |
| REQ-002 | Integration | Backend ingests all read endpoints (`/v1/instance, regions, depots, stations, routes, supply-arrivals, events, allocations, demand-history, metrics`) on each tick (SSE `simulation.tick`) with a polling fallback, and **persists demand history itself** (the API returns ≤2000 rows ≈ 166 ticks). | Guide §4, §4.11 | Internal state matches a fresh GET of each endpoint within one tick or one poll interval; stored demand history has no tick gaps over a 500-tick run. | — | UNPLANNED |
| REQ-003 | Integration | SSE is a change hint only; every event triggers a REST re-GET. No displayed state or decision is taken from an SSE payload. | Guide §2, §6 | For `allocation.status_changed`, the displayed allocation is the REST value (verified by test/log). | REQ-002 | UNPLANNED |
| REQ-004 | Resilience | When SSE is unavailable (`stream_disconnect` 503, dropped queue, silent drop detected by a missing tick) the system keeps working on polling and does a full REST resync on reconnect. | Guide §6.2, §6.4 | With `stream_disconnect` active for 60 s the dashboard keeps updating; after it clears, state matches REST. | REQ-003 | UNPLANNED |
| REQ-005 | Integration | Allocations are written only via `POST /v1/allocations` with deterministic idempotency keys (ADR-004). Handles 201 and 200 replay, 404 NOT_FOUND, all **9** 409 codes (IDEMPOTENCY_KEY_MISMATCH, ROUTE_MISMATCH, DEPOT_CLOSED, STATION_CLOSED, ROUTE_DISRUPTED, ROUTE_CAPACITY_EXCEEDED, INSUFFICIENT_INVENTORY, DISPATCH_CAPACITY_EXCEEDED, DESTINATION_CAPACITY_EXCEEDED), 422 and 503 FAULT_INJECTED; each outcome is shown to the operator with its reason. | Guide §5, §9 | A retried identical request yields exactly one allocation; each 409 code has a test or a recorded manual check with the UI message. | REQ-002 | UNPLANNED |
| REQ-006 | Integration | Operator can cancel a PENDING allocation (`POST /v1/allocations/{id}/cancel`); 404/409 CANNOT_CANCEL handled. | Guide §5.5 | Cancel on PENDING → inventory refunded per follow-up GET; cancel on IN_TRANSIT → clear message, no crash. | REQ-005 | UNPLANNED |
| REQ-007 | AI | At least one meaningful intelligence capability. **Team scope:** (a) demand forecast per station×fuel with uncertainty; (b) projected stockout per station×fuel (and depot×fuel); (c) constraint-aware allocation recommender. | Brief §7 | Each output is visible in the app and traceable to named inputs (forecast inputs, inventory, in-transit, arrivals). | REQ-002 | UNPLANNED |
| REQ-008 | AI | Recommendations are inspectable: station, fuel, projected stockout time, expected demand over horizon, recommended depot/route/quantity, **binding constraints**, **expected impact before/after** (stockout risk and unmet litres), confidence, ≥1 alternative, signals that triggered it. Mirrors the brief's ALERT example. | Brief §9 | Every recommendation in API and UI has all fields populated (none placeholder). | REQ-007 | UNPLANNED |
| REQ-009 | Resilience | Four required fallbacks: (a) Intelligence/model unavailable → deterministic heuristic policy (runs without the Intelligence service); (b) invalid simulator response → rejected, alert raised, state not overwritten; (c) low confidence → HUMAN_REVIEW, never auto-executed; (d) simulator/DB unavailable → retry + backoff + circuit breaker, last-known-good state shown with its age, visible degraded mode. | Brief §11 | Each condition is triggered on demand (container stop, `/admin/faults`, malformed-input test) and the documented behaviour is observed and recorded in `docs/verification.md`. | REQ-002, REQ-007 | UNPLANNED |
| REQ-010 | Integration | Distinguish error envelopes `{"detail":…}` vs `{"error":…}`; honour `X-Simulator-Stale: true` by marking data stale in UI and lowering confidence. | Guide §4, §9 | With `stale_data` active the UI shows a stale badge and recommendations carry a stale reason. | REQ-002 | UNPLANNED |
| REQ-011 | Crisis | Detect → evaluate → respond → explain → recover for at least **demand_spike** and **route_disruption**, plus one **combined** crisis (the brief's 5 scenarios map to simulator events: shipment delay→`shipment_delay`, demand spike→`demand_spike`, depot constraint→`depot_constraint`, regional disruption→`route_disruption`/`station_outage`, combined→any two). | Brief §10, §22 | An injected event produces within ≤3 ticks: an alert, a changed risk, a changed or new recommendation; after it resolves, risk returns and this is visible. | REQ-007, REQ-018 | UNPLANNED |
| REQ-012 | DevOps | One-command reproducible launch: `docker compose up` brings up simulator + all our services with health checks; documented in README. | Brief §12, §19.7 | Clean clone → one command → app and `/health` green within a stated time (target ≤5 min incl. image pull). | — | UNPLANNED |
| REQ-013 | Observability | Metrics for all four layers: **application** (request rate, latency p50/p95, error rate, availability), **system** (CPU, memory per container), **intelligence** (prediction error, confidence, shortage-alert rate, decision frequency, fallback count, human-review count), **logs** (structured: actions, integration failures, decisions, recoveries). | Brief §14 | During a demo run each metric shows real non-zero values somewhere inspectable; logs are JSON with event names. | REQ-002, REQ-007, REQ-009 | UNPLANNED |
| REQ-014 | Observability | Health/status view: Backend API, Database, Fuel Simulator, Prediction/Intelligence Service, Decision Engine, each healthy/degraded/down, plus p95 latency and error rate. | Brief §15 | One screen answers "is the system healthy?"; stopping the Intelligence container turns its row red within 10 s. | REQ-013 | UNPLANNED |
| REQ-015 | Performance | Load test ≥1 meaningful path (our decision/recommendation API; e2e decision request as second path), reporting avg/p50/p95/p99, throughput, error rate, concurrency, resource usage, and the **saturation point**. Must not flood the simulator's write path. | Brief §17 | `docs/verification.md` holds workload definition + results at ≥3 concurrency levels. | REQ-002/007 | UNPLANNED |
| REQ-016 | Security | No hard-coded secrets (`.env.example`), validate all external input (simulator + operator), failed requests don't crash, config documented, credentials not exposed, **approve/execute/cancel actions restricted** (at minimum an operator token/role). | Brief §18 | Repo scan finds no secrets; approve endpoint rejects unauthenticated calls; malformed input returns 422 not 500. | — | UNPLANNED |
| REQ-017 | Submission | All 11 required deliverables exist: app, repo+setup, simulator integration, intelligence, operator UI, architecture diagram, deployment, observability evidence, resilience demo, load-test evidence, final demo. | Brief §19 | Each deliverable has a linked artifact in README before submission. | ALL | UNPLANNED |
| REQ-018 | Detection | **NEW.** Risk detection covers all six simulator event types, not only demand: demand anomaly (statistical), and state-change detection for station OUTAGE, route DISRUPTED, depot CONSTRAINED, supply arrival DELAYED, supply quantity reduced; plus data-health signals (stale, missing ticks). | Brief §7 Detection, §10; Guide §7.8 | On the labelled crisis dataset, each event type is detected; demand spikes detected within ≤3 ticks (precision/recall recorded). | REQ-002 | UNPLANNED |
| REQ-019 | Guardrail | **NEW.** Human-in-the-loop: consequential allocations (policy-defined, e.g. HUMAN_REVIEW flag, large share of depot capacity, active crisis) require explicit operator approval; low-risk routine top-ups may be auto-executed only if auto-mode is switched on by the operator. Every approval/override is logged with who/when/why. | Brief §24, §9 | With auto-mode off nothing is submitted without approval; with it on, HUMAN_REVIEW items still wait. | REQ-008, REQ-016 | UNPLANNED |
| REQ-040 | Guardrail | **NEW.** UI and explanations clearly label all data and outcomes as **simulated**. | Brief §24 | Persistent "Simulated environment" banner; LLM/template text never claims real-world conditions. | REQ-001 | UNPLANNED |
| REQ-041 | Adaptability | **NEW.** No hard-coded world: stations, depots, routes, fuels and capacities are read from the simulator at runtime; unknown/new entities or new events injected during judging are handled without restart. | Brief "Hackathon dynamic", §26 | Test: logic reads topology from API; a scenario with a disabled route or new event mid-run needs no code change. | REQ-002 | UNPLANNED |
| REQ-042 | Demo | **NEW.** Demo runs at a human-usable speed: `SIMULATION_SPEED` and tick control configurable (e.g. 1 tick/s or paused + step), so an operator can inspect and approve before the situation changes (default 8 ticks/s moves 2 sim-hours per real second). | Guide §1; Brief §22 | Demo profile in `.env` documented; one full 14-step run rehearsed and timed. | REQ-012 | UNPLANNED |

## SHOULD

| ID | Type | Requirement | Source | Why |
|---|---|---|---|---|
| REQ-020 | DevOps | CI (GitHub Actions): lint, test, build images, (push); badge in README. Demonstrates Build→Test→Package→Deploy→Health Check. | Brief §12, §20 | DevOps 15% |
| REQ-021 | DevOps | Automated tests: allocation validation + error codes, idempotency, projection engine, allocator constraints, fallback paths; deterministic via `/admin/reset` + `/admin/step`. | Brief §20 | DevOps, Integration |
| REQ-022 | Data | Decision audit history: recommendation, inputs snapshot, rationale, operator action, simulator outcome (ARRIVED/FAILED + reason). | Brief §9, §20 | Intelligence, Demo |
| REQ-023 | DevOps | Versioning: image tags per release, model/policy version shown in UI and logs. (Rev 1 split deployment versioning; model versioning added.) | Brief §20 | DevOps |
| REQ-024 | Demo | Scenario configuration / replay: scripted scenarios (reset + events + steps) for rehearsal and for the policy benchmark. | Brief §20 | Demo, Resilience |
| REQ-025 | Resilience | Fallback activation, degraded mode and stale data are surfaced in the UI in real time. | Brief §11, §20 | Resilience |
| REQ-026 | Observability | Grafana dashboard provisioned as code + at least 2 alert rules (simulator down, fallback active / error rate). | Brief §14, §19.8 ("alerts") | Observability |
| REQ-027 | UX | **NEW.** Regional demand view (actual vs forecast per region) and a system-alerts feed. | Brief §6 | Completes the 12-item list |
| REQ-028 | AI | **NEW.** Policy benchmark: no-action vs heuristic (vs optimizer if built) on the same seed + events; report service_level, unmet litres, allocation failures. | Brief §8, §23 "decision quality" | Strongest evidence for Intelligence 20% |
| REQ-029 | AI | **NEW.** Forecast/detection evaluation on held-out data (time split) with metrics recorded (MAE/MAPE, interval coverage, detection P/R). | Brief §20 experiment tracking, §23 "appropriate methodology" | Intelligence |
| REQ-035 | GenAI | LLM narration of finished recommendations and incident summaries via Anthropic API; numbers come from the object; timeout + template fallback. **Moved from COULD to SHOULD** (ADR-005 puts it in scope; brief lists "human-readable decision explanations"). | Brief §7, SPEC §6 | Intelligence, Demo |
| REQ-043 | Data | **NEW.** Document data used/generated (simulator exports, derived features, crisis scenarios) per brief §16. | Brief §16 | Compliance |

## COULD

| ID | Requirement | Source |
|---|---|---|
| REQ-030 | Reinforcement learning with comparison vs heuristic (only if REQ-028 benchmark exists). | Brief §8 |
| REQ-031 | Multi-agent decision systems. | Brief §21 |
| REQ-032 | Optimization + ML hybrid (OR-Tools allocator using forecast quantiles). | Brief §21 |
| REQ-033 | Uncertainty-aware allocation / counterfactual simulation beyond the REQ-008 before/after. | Brief §21 |
| REQ-034 | Kubernetes, autoscaling, blue/green, canary. | Brief §13, §21 |
| REQ-036 | Distributed tracing (OpenTelemetry). | Brief §14 |
| REQ-037 | **NEW.** Drift detection (online error vs test error) with alert. | Brief §21 |
| REQ-038 | **NEW.** Operator investigation assistant (LLM Q&A over current state). | Brief §7, §21 |
| REQ-039 | **NEW.** Rollback: automated deployment or policy rollback. | Brief §20, §21 |

## Change log (Rev 1 → Rev 2)

| Change | Reason |
|---|---|
| REQ-001 names the 8 items we commit to | "meaningful subset" was untestable |
| REQ-002 adds tick-driven fetch + own demand-history persistence | API caps history at 2000 rows; forecasting needs ≥96 ticks per series |
| REQ-005: "8 conflict codes" → 9 named, plus 503 | Guide lists IDEMPOTENCY_KEY_MISMATCH as a 9th 409 |
| REQ-007 states the team's concrete scope | Makes acceptance testable |
| REQ-008 adds binding constraints, expected demand, triggering signals | Brief §9 lists "which signals influenced", "relevant constraints" |
| REQ-011 names two event types + combined crisis and a ≤3-tick bar | "at least one" under-serves the brief's 5 scenarios |
| REQ-013 adds the **System** layer (CPU/memory) and human-review count | Was missing from Rev 1 |
| REQ-014 lists the brief's 5 component rows | Matches brief §15 example |
| REQ-015 adds saturation point, no write-flooding | Brief: "understand behaviour and limits" |
| REQ-016 makes action restriction testable | Brief §18 |
| New MUST REQ-018 multi-signal detection | 5 of 6 event types don't change demand |
| New MUST REQ-019 human-in-the-loop | Brief §24 guardrail was only implicit |
| New MUST REQ-040 "simulated" labelling | Brief §24 guardrail was missing |
| New MUST REQ-041 no hard-coded world | Brief: surprise events during judging |
| New MUST REQ-042 demo speed | Default speed makes human review impossible |
| New SHOULD REQ-027/028/029/043; REQ-035 promoted; new COULD REQ-037/038/039 | Brief §6, §8, §16, §20, §21 coverage |
