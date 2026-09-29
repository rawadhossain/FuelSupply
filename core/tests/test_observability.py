import asyncio
import io
import json
import logging

from app.main import app
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fuelsupply_shared import observability as obs
from fuelsupply_shared.observability import setup_observability


def test_metrics_has_http_requests_total_after_request() -> None:
    client = TestClient(app)
    client.get("/health")
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "http_requests_total" in response.text
    assert 'route="/health"' in response.text


def test_route_label_is_a_template_not_a_raw_path() -> None:
    client = TestClient(app)
    client.get("/this-path-does-not-exist")
    response = client.get("/metrics")
    assert 'route="unmatched"' in response.text


def test_ready_with_no_checks_returns_200() -> None:
    client = TestClient(app)
    response = client.get("/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready", "checks": {}}


async def _failing_check() -> bool:
    raise RuntimeError("dependency unreachable")


async def _slow_check() -> bool:
    await asyncio.sleep(5)
    return True


def test_ready_returns_503_when_a_check_fails() -> None:
    test_app = FastAPI()
    setup_observability(test_app, service="test-core-fail", readiness_checks={"dep": _failing_check})
    client = TestClient(test_app)
    response = client.get("/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    assert body["checks"]["dep"] == "fail"


def test_ready_returns_503_on_timeout() -> None:
    test_app = FastAPI()
    setup_observability(test_app, service="test-core-timeout", readiness_checks={"dep": _slow_check})
    client = TestClient(test_app)
    response = client.get("/ready")
    assert response.status_code == 503
    assert response.json()["checks"]["dep"] == "fail"


def test_request_id_is_echoed_when_provided() -> None:
    client = TestClient(app)
    response = client.get("/health", headers={"X-Request-ID": "given-id-123"})
    assert response.headers["X-Request-ID"] == "given-id-123"


def test_request_id_is_generated_when_absent() -> None:
    client = TestClient(app)
    response = client.get("/health")
    assert response.headers["X-Request-ID"]


def test_logs_are_valid_json_with_request_id() -> None:
    root_logger = logging.getLogger()
    buffer = io.StringIO()
    handler = logging.StreamHandler(buffer)
    handler.setFormatter(root_logger.handlers[0].formatter)
    root_logger.addHandler(handler)
    try:
        client = TestClient(app)
        client.get("/not-a-real-route", headers={"X-Request-ID": "log-test-id"})
    finally:
        root_logger.removeHandler(handler)

    lines = [line for line in buffer.getvalue().splitlines() if line.strip()]
    assert lines, "expected at least one JSON log line"
    records = [json.loads(line) for line in lines]
    http_request_records = [r for r in records if r.get("event") == "http_request"]
    assert http_request_records, f"no http_request event in {records}"
    record = http_request_records[-1]
    assert record["request_id"] == "log-test-id"
    assert record["service"] == "core"
    assert "timestamp" in record
    assert "level" in record


def test_helpers_change_metric_values() -> None:
    before = obs.SIMULATOR_REQUESTS_TOTAL.labels(endpoint="test_endpoint", status="ok")._value.get()
    obs.record_simulator_call("test_endpoint", "ok", 0.1)
    after = obs.SIMULATOR_REQUESTS_TOTAL.labels(endpoint="test_endpoint", status="ok")._value.get()
    assert after == before + 1

    obs.set_degraded("core", True)
    assert obs.DEGRADED_MODE.labels(service="core")._value.get() == 1
    obs.set_degraded("core", False)
    assert obs.DEGRADED_MODE.labels(service="core")._value.get() == 0

    before_fallback = obs.FALLBACK_ACTIVATIONS_TOTAL.labels(
        component="simulator_client", reason="dependency_down"
    )._value.get()
    obs.record_fallback("simulator_client", "dependency_down")
    after_fallback = obs.FALLBACK_ACTIVATIONS_TOTAL.labels(
        component="simulator_client", reason="dependency_down"
    )._value.get()
    assert after_fallback == before_fallback + 1

    before_decisions = obs.DECISIONS_TOTAL.labels(policy="heuristic", outcome="approved")._value.get()
    obs.record_decision("heuristic", "approved", 0.05)
    after_decisions = obs.DECISIONS_TOTAL.labels(policy="heuristic", outcome="approved")._value.get()
    assert after_decisions == before_decisions + 1
