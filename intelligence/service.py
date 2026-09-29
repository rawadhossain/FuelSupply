"""Intelligence Service (SPEC §5): FastAPI wrapper around Assessor + Narrator.

    uvicorn intelligence.service:app --host 0.0.0.0 --port 8100

Endpoints (contract: docs/api-contracts.md, CONTRACT-INTEL-OUTPUT):
  POST /intel/assess   snapshot (+ new demand rows) -> forecasts, signals, risks, recommendations ...
  POST /intel/ask      operator question about the latest assessment (investigation assistant)
  GET  /intel/summary  incident explanation + state summary of the latest assessment
  GET  /health         liveness + component status (model, LLM, last tick)
  GET  /metrics        Prometheus metrics incl. SPEC §11 intelligence metrics
The service never calls the simulator; Core sends it the snapshot (SPEC §5).
"""
from __future__ import annotations

import os
import threading
import time
from typing import Literal, Optional

import numpy as np
from fastapi import FastAPI, HTTPException, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from pydantic import BaseModel, Field

from shared.models import DemandRow, SimSnapshot

from .assess import Assessor, InvalidInput
from .narrate import Narrator

POLICY = os.environ.get("INTEL_POLICY", "heuristic")

# ------------------------------------------------------------------------------ metrics (§11)
ASSESS_REQ = Counter("intel_assess_requests_total", "Assess requests", ["policy", "outcome"])
ASSESS_LAT = Histogram("intel_assess_latency_seconds", "Assess latency (s)",
                       buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10))
PRED_ERR = Gauge("intel_prediction_error_mape", "Rolling 1-step forecast error over recent ticks (fraction)")
CONFIDENCE = Histogram("intel_model_confidence", "Confidence of each recommendation",
                       buckets=(0.2, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0))
SHORTAGE_ALERTS = Counter("intel_shortage_alerts_total", "High-risk station-fuel pairs and systemic shortages reported")
DECISIONS = Counter("intel_decisions_total", "Recommendations produced", ["policy", "review"])
FALLBACKS = Counter("intel_fallback_total", "Assessments that fell back to the heuristic policy")
SIGNALS = Counter("intel_signals_total", "Signals raised", ["type"])
INVALID = Counter("intel_invalid_input_total", "Snapshots rejected by validation")
LLM_CALLS = Counter("intel_llm_calls_total", "LLM narration calls", ["outcome"])
LAST_TICK = Gauge("intel_last_tick", "Tick of the latest assessment")
PROJ_UNMET = Gauge("intel_projected_unmet_liters", "Projected unmet demand next 24 h, with plan")

app = FastAPI(title="BUP Fuel Intelligence Service", version="1.0.0",
              description="Forecast, detection, allocation and explanations over a SIMULATED fuel network.")


class _State:
    def __init__(self):
        self.lock = threading.Lock()
        self.assessor: Optional[Assessor] = None
        self.narrator: Optional[Narrator] = None
        self.last: Optional[dict] = None
        self.started = time.time()
        self.load_error: Optional[str] = None
        self.llm_seen = {"llm_ok": 0, "llm_failed": 0, "rejected_unverified": 0, "fallback_template": 0}


S = _State()


def _load() -> None:
    try:
        pr = os.environ.get("INTEL_PRIORITIES", "")      # e.g. "station-mirpur:2,station-tongi:1.5"
        priorities = {k: float(v) for k, v in (p.split(":") for p in pr.split(",") if ":" in p)}
        S.assessor = Assessor(priorities=priorities)
        S.narrator = Narrator.from_env()
    except Exception as ex:                                # model files missing/corrupt -> degraded
        S.load_error = f"{type(ex).__name__}: {ex}"


_load()


def _record_llm() -> None:
    m = S.narrator.metrics()
    for k, lbl in (("llm_ok", "ok"), ("llm_failed", "error"), ("rejected_unverified", "rejected"),
                   ("fallback_template", "template")):
        d = m[k] - S.llm_seen[k]
        if d > 0:
            LLM_CALLS.labels(lbl).inc(d)
        S.llm_seen[k] = m[k]


# ------------------------------------------------------------------------------ schemas
class AssessRequest(BaseModel):
    snapshot: SimSnapshot
    demand_rows: list[DemandRow] = Field(default_factory=list,
                                         description="New /v1/demand-history rows since the last call")
    stale: bool = Field(False, description="True if any /v1/* GET carried X-Simulator-Stale: true")
    policy: Literal["heuristic", "lp"] = POLICY
    narrate: bool = Field(False, description="Add LLM explanations (adds ~2-4 s when OpenAI is enabled)")


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=500)


# ------------------------------------------------------------------------------ endpoints
@app.post("/intel/assess")
def assess(req: AssessRequest) -> dict:
    if S.assessor is None:
        ASSESS_REQ.labels(req.policy, "unavailable").inc()
        raise HTTPException(503, {"code": "MODEL_UNAVAILABLE", "message": S.load_error or "model not loaded",
                                  "fallback": "Core should run shared.heuristic.plan"})
    t0 = time.perf_counter()
    with S.lock:                                  # Assessor keeps per-run memory (detector, history)
        try:
            out = S.assessor.assess(req.snapshot.to_snapshot(stale=req.stale),
                                    [r.model_dump() for r in req.demand_rows], policy=req.policy)
        except InvalidInput as ex:
            INVALID.inc(); ASSESS_REQ.labels(req.policy, "invalid").inc()
            raise HTTPException(422, {"code": "INVALID_SNAPSHOT", "message": str(ex), "alert": True})
        if req.narrate and S.narrator is not None:
            S.narrator.enrich(out)
            _record_llm()
        S.last = out
        errs = S.assessor.abs_err
        vals = [np.mean(v) for v in errs.values() if len(v) >= 4]
    ASSESS_LAT.observe(time.perf_counter() - t0)
    ASSESS_REQ.labels(out["policy"], "ok").inc()
    if out["policy"] == "fallback":
        FALLBACKS.inc()
    if vals:
        PRED_ERR.set(float(np.mean(vals)))
    for r in out["recommendations"]:
        CONFIDENCE.observe(r["confidence"])
        DECISIONS.labels(out["policy"], r["review"]).inc()
    SHORTAGE_ALERTS.inc(sum(r["risk"] == "high" for r in out["risks"]) +
                        sum(s["type"] == "systemic_shortage" for s in out["signals"]))
    for s in out["signals"]:
        SIGNALS.labels(s["type"]).inc()
    LAST_TICK.set(out["tick"])
    PROJ_UNMET.set(out["projected"]["unmet_l_24h_with_plan"])
    return out


@app.post("/intel/ask")
def ask(req: AskRequest) -> dict:
    if S.last is None:
        raise HTTPException(409, {"code": "NO_ASSESSMENT", "message": "call /intel/assess first"})
    with S.lock:
        ans = S.narrator.investigate(req.question, S.last)
        _record_llm()
    return {"tick": S.last["tick"], **ans}


@app.get("/intel/summary")
def summary() -> dict:
    if S.last is None:
        raise HTTPException(409, {"code": "NO_ASSESSMENT", "message": "call /intel/assess first"})
    with S.lock:
        inc, st = S.narrator.explain_incident(S.last), S.narrator.summarize_state(S.last)
        _record_llm()
    return {"tick": S.last["tick"], "incident": inc, "state": st}


@app.get("/health")
def health(response: Response) -> dict:
    ok = S.assessor is not None
    llm = S.narrator.metrics() if S.narrator else {"enabled": False, "disabled_reason": "not loaded"}
    body = {"status": "healthy" if ok else "degraded",
            "components": {"prediction_model": "healthy" if ok else f"down: {S.load_error or 'model unavailable'}",
                           "decision_engine": "healthy" if ok else "down (Core must use shared.heuristic)",
                           "llm_narration": "healthy" if llm.get("enabled") else f"template-only ({llm.get('disabled_reason')})"},
            "model_version": S.assessor.model.version if ok else None,
            "default_policy": POLICY, "last_tick": S.last["tick"] if S.last else None,
            "uptime_s": round(time.time() - S.started, 1)}
    if not ok:
        response.status_code = 503
    return body


@app.get("/metrics")
def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
