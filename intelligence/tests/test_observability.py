import io
import json
import logging

from fastapi.testclient import TestClient
from fuelsupply_shared import observability as obs

from intelligence.service import app


def test_metrics_has_http_requests_total_and_build_info() -> None:
    client = TestClient(app)
    client.get("/health")
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "http_requests_total" in response.text
    assert 'route="/health"' in response.text
    assert 'service="intelligence"' in response.text


def test_ready_reports_model_check() -> None:
    # service.py registers a "model" readiness check (the trained forecaster/
    # allocator artifacts loaded successfully) — no longer the generic
    # no-checks-registered case core/other services may still be in.
    client = TestClient(app)
    response = client.get("/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready", "checks": {"model": "ok"}}


def test_request_id_is_echoed_when_provided() -> None:
    client = TestClient(app)
    response = client.get("/health", headers={"X-Request-ID": "intel-req-1"})
    assert response.headers["X-Request-ID"] == "intel-req-1"


def test_logs_are_valid_json_with_request_id() -> None:
    root_logger = logging.getLogger()
    buffer = io.StringIO()
    handler = logging.StreamHandler(buffer)
    handler.setFormatter(root_logger.handlers[0].formatter)
    root_logger.addHandler(handler)
    try:
        client = TestClient(app)
        client.get("/not-a-real-route", headers={"X-Request-ID": "intel-log-1"})
    finally:
        root_logger.removeHandler(handler)

    lines = [line for line in buffer.getvalue().splitlines() if line.strip()]
    assert lines, "expected at least one JSON log line"
    records = [json.loads(line) for line in lines]
    http_request_records = [r for r in records if r.get("event") == "http_request"]
    assert http_request_records, f"no http_request event in {records}"
    record = http_request_records[-1]
    assert record["request_id"] == "intel-log-1"
    assert record["service"] == "intelligence"


def test_helpers_change_prediction_metrics() -> None:
    obs.PREDICTION_CONFIDENCE.observe(0.85)
    obs.MODEL_INFERENCE_SECONDS.labels(model="xgboost-v1").observe(0.02)
    obs.PREDICTION_ABS_ERROR.observe(1.5)

    before = obs.SHORTAGE_ALERTS_TOTAL.labels(fuel="DIESEL", severity="high")._value.get()
    obs.SHORTAGE_ALERTS_TOTAL.labels(fuel="DIESEL", severity="high").inc()
    after = obs.SHORTAGE_ALERTS_TOTAL.labels(fuel="DIESEL", severity="high")._value.get()
    assert after == before + 1
