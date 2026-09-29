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
