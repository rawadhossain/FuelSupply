# API and shared interface contracts

Define a contract before independent producers and consumers implement it. Schemas may be JSON, TypeScript-like, or concise tables; include examples for risky interfaces.

## CONTRACT-SIM-REST — Simulator read endpoints (`/v1/*` GET)

| Field | Value |
|---|---|
| Endpoint / interface | `GET /v1/instance`, `/v1/regions`, `/v1/depots[/{id}]`, `/v1/stations[/{id}]`, `/v1/routes`, `/v1/supply-arrivals`, `/v1/events`, `/v1/allocations`, `/v1/demand-history?station_id=&limit=`, `/v1/metrics`, `/v1/health` |
| Method / invocation | HTTP GET, JSON |
| Owner → consumer | BUP Fuel Supply Simulator (`asifmahmoud414/bup-fuel-supply-simulator:1.0.0`) → Core Service simulator client |
| Authentication | None |
| Request schema | Query params only (`station_id`, `limit` on demand-history; `limit` clamped [1,2000], default 200 — always pass explicitly, table grows unboundedly) |
| Response schema | See `shared/fuelsupply_shared/models.py`: `InstanceState`, `Region`, `Depot`, `Station`, `Route`, `SupplyArrival`, `DomainEvent`, `DemandHistoryEntry`, `SimulatorMetrics`, `HealthResponse`. All fields verified against a live simulator instance (2026-09-29), not guessed from docs. |
| Errors / status | `200` normal. Under `stale_data` fault: `200` + header `X-Simulator-Stale: true` (verified). Under `unavailable`/`error_rate` fault: `503 {"error": {"code": "FAULT_INJECTED", "message": "..."}}` (verified — note `error` key, not `detail`). `/v1/health` bypasses all faults (verified: returned 200 while `unavailable` fault was active). |
| Version / status | **VERIFIED** against live simulator 2026-09-29 |

**Examples (captured live, tick 0 unless noted):**

```json
// GET /v1/instance
{"id":1,"scenario_id":"baseline","scenario_version":"1.0","seed":12345,"sim_time":"2026-01-01T00:00:00","tick":0,"tick_minutes":15,"status":"PAUSED"}

// GET /v1/depots (element)
{"id":"depot-gazipur","name":"Gazipur Depot","region_id":"region-dhaka","status":"OPEN","dispatch_capacity_per_tick":12000.0,"capacity":{"DIESEL":90000,"PETROL":70000,"OCTANE":45000},"inventory":{"DIESEL":60000,"PETROL":45000,"OCTANE":26000}}

// GET /v1/stations (element)
{"id":"station-mirpur","name":"Mirpur Fuel Station","region_id":"region-dhaka","status":"OPEN","demand_profile":"urban_high","demand_multiplier":1.0,"capacity":{"DIESEL":15000,"PETROL":14000,"OCTANE":9000},"inventory":{"DIESEL":9000,"PETROL":9000,"OCTANE":5000}}

// GET /v1/routes (element)
{"id":"route-gazipur-mirpur","source_depot_id":"depot-gazipur","destination_station_id":"station-mirpur","transit_ticks":2,"max_shipment":7000.0,"status":"AVAILABLE"}

// GET /v1/demand-history?station_id=station-mirpur&limit=5 (element, after 15 admin/step ticks)
{"id":171,"station_id":"station-mirpur","fuel_type":"OCTANE","tick":14,"sim_time":"2026-01-01T03:30:00","demand_liters":40.089,"served_liters":40.089,"unmet_liters":0.0}

// GET /v1/metrics
{"served_demand_liters":0.0,"unmet_demand_liters":0.0,"service_level":1.0,"allocation_liters":0.0,"allocation_failures":0}

// GET /v1/health (bypasses fault injection, verified while `unavailable` fault active)
{"status":"ok","database":"ok","simulation":{"status":"PAUSED","tick":15}}

// GET /v1/depots under `unavailable` fault
// HTTP 503
{"error":{"code":"FAULT_INJECTED","message":"Simulator API temporarily unavailable."}}
```

## CONTRACT-SIM-ALLOC — Allocation write path

| Field | Value |
|---|---|
| Endpoint / interface | `POST /v1/allocations`, `POST /v1/allocations/{id}/cancel` |
| Method / invocation | HTTP POST, JSON body |
| Owner → consumer | Simulator → Core Service allocation executor (the **only** writer) |
| Authentication | None |
| Request schema | `AllocationRequest` in `shared/fuelsupply_shared/models.py`: `idempotency_key` (str, 1-150 chars, in body not header), `source_depot_id`, `destination_station_id`, `route_id`, `fuel_type` (`DIESEL`\|`PETROL`\|`OCTANE`), `quantity` (float > 0) |
| Response schema | `Allocation` model. Validation order verified live (first failure wins): idempotency check → `404 NOT_FOUND` → `409 ROUTE_MISMATCH` → `409 DEPOT_CLOSED` → `409 STATION_CLOSED` → `409 ROUTE_DISRUPTED` → `409 ROUTE_CAPACITY_EXCEEDED` → `409 INSUFFICIENT_INVENTORY` → `409 DISPATCH_CAPACITY_EXCEEDED` → `409 DESTINATION_CAPACITY_EXCEEDED` |
| Errors / status | All error bodies use `{"detail": {"code": ..., "message": ...}}` (verified — distinct from the fault `{"error": {...}}` shape). Same key + same body → **201** replay with the original record (verified; guide's own status table says 200 elsewhere — ASM-010 resolved: 201 is what the live image actually returns, code must accept both 200 and 201 as success regardless). Same key + different body → `409 IDEMPOTENCY_KEY_MISMATCH`. Key is permanently consumed even by a cancelled allocation — verified: replaying a key after cancel returns the same `CANCELLED` record at 201, not a new allocation. Cancel on non-PENDING → `409 CANNOT_CANCEL`. |
| Version / status | **VERIFIED** against live simulator 2026-09-29 (success, replay, mismatch, 404, route-mismatch, cancel, double-cancel, post-cancel replay all exercised) |

**Examples (captured live):**

```json
// POST /v1/allocations — success, HTTP 201
// request
{"idempotency_key":"test-key-001","source_depot_id":"depot-gazipur","destination_station_id":"station-mirpur","route_id":"route-gazipur-mirpur","fuel_type":"DIESEL","quantity":1000}
// response
{"id":1,"idempotency_key":"test-key-001","source_depot_id":"depot-gazipur","destination_station_id":"station-mirpur","route_id":"route-gazipur-mirpur","fuel_type":"DIESEL","quantity":1000.0,"created_tick":15,"departure_tick":null,"expected_arrival_tick":null,"actual_arrival_tick":null,"status":"PENDING","failure_reason":null}

// POST /v1/allocations — same key, different quantity, HTTP 409
{"detail":{"code":"IDEMPOTENCY_KEY_MISMATCH","message":"Idempotency key was already used with different parameters."}}

// POST /v1/allocations — unknown depot, HTTP 404
{"detail":{"code":"NOT_FOUND","message":"Source depot not found"}}

// POST /v1/allocations — route doesn't connect the given depot/station, HTTP 409
{"detail":{"code":"ROUTE_MISMATCH","message":"Route does not connect selected depot and station"}}

// POST /v1/allocations/{id}/cancel — HTTP 200
{"id":1, "...": "...", "status":"CANCELLED","failure_reason":null}

// POST /v1/allocations/{id}/cancel again — HTTP 409
{"detail":{"code":"CANNOT_CANCEL","message":"Only pending allocations can be cancelled"}}
```

## CONTRACT-OBSERVABILITY

Metrics, structured logging, and `/ready` contract for `shared/fuelsupply_shared/observability.py`,
used identically by `core` and `intelligence`. Full catalogue, env vars, and examples:
`docs/contracts/observability.md`. Status: ACCEPTED (TASK-031).

## CONTRACT-001 — Template

| Field | Value |
|---|---|
| Endpoint / interface | — |
| Method / invocation | — |
| Owner → consumer | — → — |
| Authentication | — |
| Request schema | — |
| Response schema | — |
| Errors / status | — |
| Version / status | DRAFT |

**Notes / examples:** —

## CONTRACT-INTEL-OUTPUT — Intelligence Service API (v1.0, IMPLEMENTED)

| Field | Value |
|---|---|
| Endpoint / interface | `POST /intel/assess`, `POST /intel/ask`, `GET /intel/summary`, `GET /health`, `GET /metrics` on the Intelligence Service (default port 8100; `INTELLIGENCE_PORT`) |
| Method / invocation | HTTP/JSON. Core calls `/intel/assess` once per new simulator tick (latest wins). Interactive docs at `/docs` |
| Owner → consumer | Intelligence (`intelligence/service.py`) → Core service → frontend |
| Authentication | None inside the Compose network (not exposed publicly). Operator actions (approve / execute) are gated in Core, not here |
| Request schema | `AssessRequest` (below); simulator JSON validated by the Pydantic models in `shared/models.py` |
| Response schema | Assessment object (below); full example in `loadtest/sample_assess_response.json` |
| Errors / status | 200 ok · 422 `INVALID_SNAPSHOT` (business validation, `alert: true`) or FastAPI validation list (malformed body) · 503 `MODEL_UNAVAILABLE` (use the fallback) · 409 `NO_ASSESSMENT` (ask/summary before any assess) |
| Version / status | 1.0 · IMPLEMENTED · verified by `intelligence/tests/test_service.py` (VER-013) and the load test (VER-014) |

### `POST /intel/assess`

Request (`loadtest/sample_assess_request.json` is a real example):

```jsonc
{
  "snapshot": {                         // the /v1/* responses of ONE tick, keyed by endpoint name
    "instance": {...}, "regions": [...], "stations": [...], "depots": [...], "routes": [...],
    "supply-arrivals": [...], "allocations": [...], "events": [...]
  },
  "demand_rows": [...],                 // new /v1/demand-history rows since the last call (normally the last tick's 12)
  "stale": false,                       // true if any /v1/* GET carried X-Simulator-Stale: true
  "policy": "heuristic",                // "heuristic" (default) | "lp"
  "narrate": false                      // true adds OpenAI texts (+2-4 s); keep false on the per-tick path
}
```

Response (top level):

| Field | Type | Meaning |
|---|---|---|
| `tick`, `model_version`, `policy`, `degraded`, `latency_ms` | scalar | `policy` is `heuristic` / `lp` / `fallback`; `degraded` = the fallback was used |
| `network` | `{fuel: {cover_ticks, stock_l, demand_next_24h_l, systemic_shortage, mode}}` | Network-wide cover per fuel; `mode` normal / rationing |
| `projected` | `{unmet_l_24h, overflow_l_24h, unmet_l_24h_with_plan, overflow_l_24h_with_plan}` | Impact of the whole plan |
| `signals` | `[{type, entity_id, severity, evidence?, since_tick?, advice?}]` | Types: `demand_anomaly`, `demand_anomaly_active`, `station_outage`, `route_disrupted`, `depot_constrained`, `depot_closed`, `supply_delayed`, `supply_shortfall`, `event_active`, `event_scheduled`, `overflow_risk`, `systemic_shortage`, `inventory_anomaly`, `allocation_at_risk`, `data_stale`, `simulation_reset` |
| `risks` | 12 × `{station_id, fuel_type, inventory, stockout_in_hours_p50, stockout_in_hours_p90, projected_unmet_l_24h, stockout_risk, risk_window_h, risk}` | `risk` band: high ≤ 3 h, medium ≤ 12 h, low |
| `forecasts` | 12 × `{station_id, fuel_type, horizon, ratio, q10[16], q50[16], q90[16], cum_q50_24h}` | Next 4 h per tick + 24 h total |
| `recommendations` | list (below) | Sorted by risk, then litres saved |
| `bottlenecks` | `{binding_constraint_counts, depot_dispatch_utilisation, at_risk_not_addressed[]}` | Why fuel cannot go where it is needed |
| `supply_outlook` | `[{id, depot_id, fuel_type, quantity, status, planned_tick, delayed_by_ticks, eta_tick, eta_hours, basis, on_time_rate}]` | Upcoming deliveries |
| `incident_summary`, `state_summary`, `genai_stats` | only when `narrate: true` | `{text, source: llm/template, reason}` |

Recommendation:

| Field | Meaning |
|---|---|
| `id` | `rec-<tick>-<station>-<fuel>-<route>`, stable within a tick |
| `station_id`, `fuel_type`, `action {source_depot_id, route_id, quantity}` | What to send. Core builds `POST /v1/allocations` from `action` + a deterministic idempotency key (ADR-004) |
| `impact {stockout_before_h, stockout_after_h, unmet_before_l, unmet_after_l, unmet_avoided_l, overflow_before_l, overflow_after_l, risk_before, risk_after}` | Before/after for this truck alone |
| `alternatives[]` | Other routes (+ "do nothing") with projected result and `why_not` |
| `constraints[]`, `binding_constraints[]` | All limits; the ones that capped the quantity |
| `confidence` (0–1), `review` (`AUTO_ELIGIBLE` / `HUMAN_REVIEW`), `review_reasons[]`, `confidence_notes[]` | `review_reasons` only when review is required |
| `policy`, `explanation`, `explanation_text?`, `explanation_source?` | Template text always; LLM text when `narrate: true` |

### Other endpoints

| Endpoint | Request | Response |
|---|---|---|
| `POST /intel/ask` | `{"question": "Why is station-mirpur at risk?"}` (3–500 chars) | `{tick, text, source, reason}`, answered from the latest assessment |
| `GET /intel/summary` | — | `{tick, incident {text, source, reason}, state {…}}` |
| `GET /health` | — | `{status: healthy/degraded, components {prediction_model, decision_engine, llm_narration}, model_version, default_policy, last_tick, uptime_s}`; 503 when the model is not loaded |
| `GET /metrics` | — | Prometheus text: `intel_assess_requests_total`, `intel_assess_latency_seconds`, `intel_prediction_error_mape`, `intel_model_confidence`, `intel_shortage_alerts_total`, `intel_decisions_total`, `intel_fallback_total`, `intel_signals_total`, `intel_invalid_input_total`, `intel_llm_calls_total`, `intel_last_tick`, `intel_projected_unmet_liters`, plus process CPU/memory |

### Core-side rules (what the consumer must do)

1. Call `/intel/assess` with a 2 s timeout (`narrate:false`), behind a circuit breaker.
2. On timeout, 5xx or 503 `MODEL_UNAVAILABLE`: run `shared.heuristic.plan(snapshot, demand, 96)` locally, mark every result `policy: fallback` and HUMAN_REVIEW, and show the degraded banner (REQ-009a/d).
3. On 422 `INVALID_SNAPSHOT`: keep the last good state, raise an alert, do not act (REQ-009b).
4. Never execute a `HUMAN_REVIEW` recommendation without operator approval (REQ-019).
5. After `/admin/reset` the tick goes backwards; the service resets its memory by itself, and Core does nothing special.
6. Request LLM texts separately (`narrate:true` on demand, or `/intel/summary`, `/intel/ask`); they are cached and never block decisions.
