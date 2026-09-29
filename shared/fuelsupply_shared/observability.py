"""Shared observability: structured logging, Prometheus metrics, and readiness checks.

Both services call `setup_observability(app, service=...)` once at startup. See
`docs/contracts/observability.md` for the metric catalogue, logging rules, env vars,
and copy-paste examples for the helpers below.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import uuid
from collections.abc import Awaitable, Callable, Mapping
from time import perf_counter
from typing import Any

import structlog
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    REGISTRY,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

log = structlog.get_logger()

_NO_LOG_PATHS = frozenset({"/metrics", "/health", "/ready"})
_READY_CHECK_TIMEOUT_SECONDS = 2.0

# --- HTTP / process metrics -------------------------------------------------

HTTP_REQUESTS_TOTAL = Counter(
    "http_requests_total",
    "Total HTTP requests handled.",
    ["service", "method", "route", "status"],
)
HTTP_REQUEST_DURATION_SECONDS = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration in seconds.",
    ["service", "method", "route"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
)
HTTP_REQUESTS_IN_PROGRESS = Gauge(
    "http_requests_in_progress",
    "HTTP requests currently being handled.",
    ["service"],
)
BUILD_INFO = Gauge(
    "build_info",
    "Always 1; labels carry build metadata.",
    ["service", "version", "git_sha", "model_version"],
)

# --- Domain metrics ----------------------------------------------------------

SIMULATOR_REQUESTS_TOTAL = Counter(
    "simulator_requests_total",
    "Calls made to the simulator API.",
    ["endpoint", "status"],
)
SIMULATOR_REQUEST_DURATION_SECONDS = Histogram(
    "simulator_request_duration_seconds",
    "Simulator API call duration in seconds.",
    ["endpoint"],
)
SIMULATOR_CIRCUIT_STATE = Gauge(
    "simulator_circuit_state",
    "Simulator circuit breaker state: 0=closed, 1=half-open, 2=open.",
)

VALIDATION_REJECTIONS_TOTAL = Counter(
    "validation_rejections_total",
    "Payloads rejected during validation.",
    ["source", "reason"],
)

DEGRADED_MODE = Gauge(
    "degraded_mode",
    "1 if the service is currently serving degraded/cached data.",
    ["service"],
)
CACHED_STATE_AGE_SECONDS = Gauge(
    "cached_state_age_seconds",
    "Age in seconds of the cached state currently being served.",
)

FALLBACK_REASONS = ("ml_unavailable", "low_confidence", "simulator_invalid", "dependency_down")
FALLBACK_ACTIVATIONS_TOTAL = Counter(
    "fallback_activations_total",
    "Fallback path activations.",
    ["component", "reason"],
)

DECISIONS_TOTAL = Counter(
    "decisions_total",
    "Decisions made, by policy and outcome.",
    ["policy", "outcome"],
)
DECISION_DURATION_SECONDS = Histogram(
    "decision_duration_seconds",
    "Decision duration in seconds.",
)

HUMAN_REVIEW_REQUESTS_TOTAL = Counter(
    "human_review_requests_total",
    "Decisions routed to human review.",
    ["reason"],
)

PREDICTION_CONFIDENCE = Histogram(
    "prediction_confidence",
    "Model prediction confidence.",
    buckets=(0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0),
)
MODEL_INFERENCE_SECONDS = Histogram(
    "model_inference_seconds",
    "Model inference duration in seconds.",
    ["model"],
)
PREDICTION_ABS_ERROR = Histogram(
    "prediction_abs_error",
    "Absolute error between a prediction and the observed value.",
)

SHORTAGE_ALERTS_TOTAL = Counter(
    "shortage_alerts_total",
    "Shortage alerts raised.",
    ["fuel", "severity"],
)


# --- Domain helpers ----------------------------------------------------------


def record_simulator_call(endpoint: str, status: str, seconds: float) -> None:
    SIMULATOR_REQUESTS_TOTAL.labels(endpoint=endpoint, status=status).inc()
    SIMULATOR_REQUEST_DURATION_SECONDS.labels(endpoint=endpoint).observe(seconds)


def set_degraded(service: str, degraded: bool) -> None:
    DEGRADED_MODE.labels(service=service).set(1 if degraded else 0)


def record_fallback(component: str, reason: str) -> None:
    FALLBACK_ACTIVATIONS_TOTAL.labels(component=component, reason=reason).inc()


def record_decision(policy: str, outcome: str, seconds: float) -> None:
    DECISIONS_TOTAL.labels(policy=policy, outcome=outcome).inc()
    DECISION_DURATION_SECONDS.observe(seconds)


def log_decision(decision_id: str, **fields: Any) -> None:
    log.info("decision", decision_id=decision_id, **fields)


# --- Logging -----------------------------------------------------------------


_logging_configured = False


def _configure_logging(service: str, log_level: str, log_format: str) -> None:
    # Logging is process-global. In production each service is its own process, so
    # this only ever runs once. Guarding it also keeps repeated setup_observability()
    # calls in the same process (e.g. tests building extra apps) from clobbering the
    # first service's logging config.
    global _logging_configured
    if _logging_configured:
        return
    _logging_configured = True

    def add_service(_logger: Any, _method_name: str, event_dict: dict) -> dict:
        event_dict["service"] = service
        return event_dict

    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        add_service,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True, key="timestamp"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    structlog.configure(
        processors=[*shared_processors, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    renderer: structlog.types.Processor = (
        structlog.dev.ConsoleRenderer()
        if log_format == "console"
        else structlog.processors.JSONRenderer()
    )
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta, renderer],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers = [handler]
    root_logger.setLevel(log_level)

    for name in ("uvicorn", "uvicorn.error"):
        uv_logger = logging.getLogger(name)
        uv_logger.handlers = []
        uv_logger.propagate = True
        uv_logger.setLevel(log_level)

    # The middleware's own "http_request" event already covers every request with
    # a route template, status, duration, and request_id - a strict superset of
    # uvicorn's plain-text access line - and correctly excludes /metrics, /health,
    # and /ready from logging. Disable the separate access logger so those scraped
    # paths don't spam stdout via a path our middleware doesn't control.
    access_logger = logging.getLogger("uvicorn.access")
    access_logger.handlers = []
    access_logger.propagate = False


# --- Middleware --------------------------------------------------------------


def _install_middleware(app: FastAPI, service: str) -> None:
    @app.middleware("http")
    async def observability_middleware(request: Request, call_next: Callable) -> Response:
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        method = request.method
        path = request.url.path
        should_log = path not in _NO_LOG_PATHS

        HTTP_REQUESTS_IN_PROGRESS.labels(service=service).inc()
        start = perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            duration = perf_counter() - start
            HTTP_REQUESTS_IN_PROGRESS.labels(service=service).dec()
            route = "unmatched"
            HTTP_REQUESTS_TOTAL.labels(service=service, method=method, route=route, status="500").inc()
            HTTP_REQUEST_DURATION_SECONDS.labels(service=service, method=method, route=route).observe(
                duration
            )
            if should_log:
                log.exception("http_request_failed", method=method, route=route)
            raise

        duration = perf_counter() - start
        HTTP_REQUESTS_IN_PROGRESS.labels(service=service).dec()

        matched_route = request.scope.get("route")
        route = getattr(matched_route, "path", None) or "unmatched"
        status = str(response.status_code)

        HTTP_REQUESTS_TOTAL.labels(service=service, method=method, route=route, status=status).inc()
        HTTP_REQUEST_DURATION_SECONDS.labels(service=service, method=method, route=route).observe(
            duration
        )

        response.headers["X-Request-ID"] = request_id

        if should_log:
            log.info(
                "http_request",
                method=method,
                route=route,
                status=response.status_code,
                duration_ms=round(duration * 1000, 2),
            )
        return response


# --- Readiness ----------------------------------------------------------------


async def _run_check(name: str, check: Callable[[], Awaitable[bool]]) -> tuple[str, bool]:
    try:
        ok = await asyncio.wait_for(check(), timeout=_READY_CHECK_TIMEOUT_SECONDS)
    except Exception:  # noqa: BLE001 - a failing/misbehaving check must not crash /ready
        return name, False
    return name, bool(ok)


async def _evaluate_readiness(
    readiness_checks: Mapping[str, Callable[[], Awaitable[bool]]],
) -> tuple[bool, dict[str, str]]:
    if not readiness_checks:
        return True, {}

    results = await asyncio.gather(
        *(_run_check(name, check) for name, check in readiness_checks.items())
    )
    checks = {name: ("ok" if ok else "fail") for name, ok in results}
    healthy = all(ok for _, ok in results)
    return healthy, checks


# --- Setup ---------------------------------------------------------------------


def setup_observability(
    app: FastAPI,
    service: str,
    version: str | None = None,
    readiness_checks: Mapping[str, Callable[[], Awaitable[bool]]] | None = None,
) -> None:
    """Wire structured logging, Prometheus metrics, and /metrics + /ready onto `app`.

    See `docs/contracts/observability.md` for the full contract, env vars, and
    examples for registering readiness checks and using the domain-metric helpers.
    """
    log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
    log_format = os.environ.get("LOG_FORMAT", "json").lower()
    git_sha = os.environ.get("GIT_SHA", "dev")
    model_version = os.environ.get("MODEL_VERSION", "none")

    _configure_logging(service, log_level, log_format)

    BUILD_INFO.labels(
        service=service,
        version=version or "unknown",
        git_sha=git_sha,
        model_version=model_version,
    ).set(1)
    HTTP_REQUESTS_IN_PROGRESS.labels(service=service).set(0)
    DEGRADED_MODE.labels(service=service).set(0)

    _install_middleware(app, service)

    checks = dict(readiness_checks or {})

    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        return Response(generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)

    @app.get("/ready", include_in_schema=False)
    async def ready() -> JSONResponse:
        healthy, results = await _evaluate_readiness(checks)
        payload = {"status": "ready" if healthy else "not_ready", "checks": results}
        return JSONResponse(payload, status_code=200 if healthy else 503)
