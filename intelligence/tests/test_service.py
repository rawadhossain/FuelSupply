"""API tests for the FastAPI Intelligence service (no network, LLM forced to template mode)."""
import copy
import os

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from intelligence import service
from intelligence.narrate import Narrator
from intelligence.replay import ROOT

pytestmark = pytest.mark.skipif(not os.path.exists(os.path.join(ROOT, "dataset", "ml", "demand_full_features.csv")),
                                reason="needs dataset/ml CSVs")


@pytest.fixture(scope="module")
def client():
    service.S.narrator = Narrator(client=None, disabled_reason="disabled in tests")   # never call OpenAI in tests
    return TestClient(service.app)


@pytest.fixture(scope="module")
def body():
    from intelligence.payloads import crisis_request
    return crisis_request(121)


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "healthy"
    assert set(r.json()["components"]) == {"prediction_model", "decision_engine", "llm_narration"}


def test_assess_contract(client, body):
    r = client.post("/intel/assess", json=body)
    assert r.status_code == 200, r.text
    out = r.json()
    for k in ("tick", "model_version", "policy", "degraded", "network", "projected", "signals", "risks",
              "forecasts", "recommendations", "bottlenecks", "supply_outlook"):
        assert k in out
    assert out["tick"] == 121 and out["recommendations"]
    rec = out["recommendations"][0]
    for k in ("action", "impact", "alternatives", "binding_constraints", "confidence", "review",
              "review_reasons", "confidence_notes", "explanation"):
        assert k in rec


def test_assess_lp_and_narrate(client, body):
    b = copy.deepcopy(body); b["policy"] = "lp"; b["narrate"] = True
    out = client.post("/intel/assess", json=b).json()
    assert out["policy"] == "lp" and "state_summary" in out and out["state_summary"]["source"] == "template"


def test_stale_flag_becomes_signal(client, body):
    b = copy.deepcopy(body); b["stale"] = True
    out = client.post("/intel/assess", json=b).json()
    assert any(s["type"] == "data_stale" for s in out["signals"])


def test_invalid_snapshot_rejected_with_alert(client, body):
    b = copy.deepcopy(body)
    b["snapshot"]["stations"][0]["inventory"]["DIESEL"] = -50
    r = client.post("/intel/assess", json=b)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "INVALID_SNAPSHOT" and r.json()["detail"]["alert"]


def test_malformed_body_rejected(client, body):
    b = copy.deepcopy(body)
    del b["snapshot"]["routes"][0]["max_shipment"]
    assert client.post("/intel/assess", json=b).status_code == 422


def test_ask_and_summary(client, body):
    client.post("/intel/assess", json=body)
    a = client.post("/intel/ask", json={"question": "Why is station-mirpur at risk?"})
    assert a.status_code == 200 and "station-mirpur" in a.json()["text"]
    s = client.get("/intel/summary").json()
    assert "incident" in s and "state" in s


def test_metrics_expose_spec_intelligence_metrics(client, body):
    client.post("/intel/assess", json=body)
    txt = client.get("/metrics").text
    for name in ("intel_assess_latency_seconds", "intel_prediction_error_mape", "intel_model_confidence",
                 "intel_shortage_alerts_total", "intel_decisions_total", "intel_fallback_total",
                 "intel_signals_total", "intel_invalid_input_total", "intel_llm_calls_total"):
        assert name in txt, name


def test_model_unavailable_is_503_with_fallback_hint(client, body):
    saved = service.S.assessor
    service.S.assessor = None
    try:
        r = client.post("/intel/assess", json=body)
        assert r.status_code == 503 and "intelligence.heuristic" in r.json()["detail"]["fallback"]
        assert client.get("/health").status_code == 503
    finally:
        service.S.assessor = saved
