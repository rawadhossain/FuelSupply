# CONTRACT-OBSERVABILITY

Shared module: `shared/fuelsupply_shared/observability.py`. Wired into each service
with exactly one call in `app/main.py`:

```python
from fuelsupply_shared.observability import setup_observability

setup_observability(app, service="core")  # or "intelligence"
```

`setup_observability(app, service, version=None, readiness_checks=None)` configures
structlog, installs the request middleware, and adds `/metrics` and `/ready`.
`/health` is untouched and stays owned by each service's `main.py`.

## Metric catalogue

| Metric | Type | Labels | Set by |
|---|---|---|---|
| `http_requests_total` | Counter | `service, method, route, status` | middleware (automatic) |
| `http_request_duration_seconds` | Histogram (5ms–10s) | `service, method, route` | middleware (automatic) |
| `http_requests_in_progress` | Gauge | `service` | middleware (automatic) |
| `build_info` | Gauge (always 1) | `service, version, git_sha, model_version` | `setup_observability` at startup, from `GIT_SHA`/`MODEL_VERSION` env |
| `simulator_requests_total` | Counter | `endpoint, status` | `record_simulator_call(endpoint, status, seconds)` |
| `simulator_request_duration_seconds` | Histogram | `endpoint` | `record_simulator_call(endpoint, status, seconds)` |
| `simulator_circuit_state` | Gauge (0/1/2) | — | set directly: `observability.SIMULATOR_CIRCUIT_STATE.set(...)` |
| `validation_rejections_total` | Counter | `source, reason` | increment directly on the label pair |
| `degraded_mode` | Gauge (0/1) | `service` | `set_degraded(service, bool)` |
| `cached_state_age_seconds` | Gauge | — | set directly: `observability.CACHED_STATE_AGE_SECONDS.set(seconds)` |
| `fallback_activations_total` | Counter | `component, reason` (reason ∈ `ml_unavailable`, `low_confidence`, `simulator_invalid`, `dependency_down`) | `record_fallback(component, reason)` |
| `decisions_total` | Counter | `policy, outcome` | `record_decision(policy, outcome, seconds)` |
| `decision_duration_seconds` | Histogram | — | `record_decision(policy, outcome, seconds)` |
| `human_review_requests_total` | Counter | `reason` | increment directly on the reason |
| `prediction_confidence` | Histogram (buckets 0.1–1.0) | — | `observability.PREDICTION_CONFIDENCE.observe(value)` |
| `model_inference_seconds` | Histogram | `model` | `observability.MODEL_INFERENCE_SECONDS.labels(model=...).observe(seconds)` |
| `prediction_abs_error` | Histogram | — | `observability.PREDICTION_ABS_ERROR.observe(abs_error)` |
| `shortage_alerts_total` | Counter | `fuel, severity` | increment directly on the label pair |

`build_info`, `http_requests_in_progress{service}`, and `degraded_mode{service}` are
pre-initialised for the current service at startup so their series read `0`
instead of being absent before the first real event.

`/metrics` is excluded from the OpenAPI schema. `/metrics`, `/health`, and `/ready`
are all counted in `http_requests_total`/`http_request_duration_seconds`, but none
of the three emit an `http_request` log line (avoids log spam from scrapers/probes).

## Logging rules

- Use `structlog` only — no `print`, no bare `logging.getLogger(...)` calls in
  application code. Get a logger with `structlog.get_logger()`.
- Every log line is a single JSON object on stdout: `timestamp` (UTC ISO-8601),
  `level`, `service`, `event`, plus whatever fields you pass as kwargs.
- The middleware binds `request_id` (from the incoming `X-Request-ID` header, or a
  generated UUID4 if absent) into structlog's contextvars for the lifetime of the
  request, so it appears on every log line emitted while handling that request,
  including ones from code you write deeper in the call stack. The same
  `request_id` is echoed back on the response header.
- `uvicorn`, `uvicorn.access`, and `uvicorn.error` are routed through the same
  JSON formatter, so their output matches the app's.
- When logging about a decision, bind `decision_id` — use the `log_decision(
  decision_id, **fields)` helper (emits an `INFO "decision"` event) rather than
  calling `log.info("decision", ...)` by hand, so the event name stays consistent
  for dashboards/alerts built on it.

## Env vars

| Var | Default | Effect |
|---|---|---|
| `LOG_LEVEL` | `INFO` | Root logger level (`DEBUG`, `INFO`, `WARNING`, ...) |
| `LOG_FORMAT` | `json` | `json` (default, stdout JSON lines) or `console` (human-readable, for local runs) |
| `GIT_SHA` | `dev` | Reported in `build_info` |
| `MODEL_VERSION` | `none` | Reported in `build_info`; Intelligence sets this when it deploys a new model |

## Registering readiness checks

`readiness_checks` is a `dict[str, Callable[[], Awaitable[bool]]]`. Each check runs
concurrently with the others, with a 2-second timeout; a raised exception or a
timeout counts as a failure for that check, not a crash of `/ready`. No checks
registered means `/ready` is always `200 {"status": "ready", "checks": {}}`.

No checks are registered yet (TASK-013 for Postgres/Redis, TASK-010/030 for the
simulator and Intelligence clients will add them). The wiring, once those clients
exist, is a single call in each service's `main.py`:

```python
# core/app/main.py, once the dependent clients exist:
setup_observability(
    app,
    service="core",
    readiness_checks={
        "postgres": check_postgres,       # async def check_postgres() -> bool
        "redis": check_redis,             # async def check_redis() -> bool
        "simulator": check_simulator,     # async def check_simulator() -> bool
        "intelligence": check_intelligence,  # async def check_intelligence() -> bool
    },
)
```

## Copy-paste examples

**Wrap a simulator call:**

```python
from time import perf_counter
from fuelsupply_shared.observability import record_simulator_call

start = perf_counter()
try:
    response = await client.get(f"/v1/depots/{depot_id}")
    record_simulator_call("get_depot", "ok", perf_counter() - start)
except httpx.HTTPError:
    record_simulator_call("get_depot", "error", perf_counter() - start)
    raise
```

**Record a fallback:**

```python
from fuelsupply_shared.observability import record_fallback

if not intelligence_available:
    record_fallback(component="recommendation_engine", reason="ml_unavailable")
    recommendation = heuristic_allocator.recommend(state)
```

**Record a decision:**

```python
from time import perf_counter
from fuelsupply_shared.observability import log_decision, record_decision

start = perf_counter()
outcome = allocate(request)
record_decision(policy="heuristic_v1", outcome=outcome.status, seconds=perf_counter() - start)
log_decision(outcome.decision_id, policy="heuristic_v1", outcome=outcome.status, station_id=request.station_id)
```
