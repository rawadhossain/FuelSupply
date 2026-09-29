"""Generative-AI layer tests with a fake LLM (no API key or network needed)."""
import copy
import os

import pandas as pd
import pytest

from shared.projection import Move
from intelligence.assess import Assessor
from intelligence.narrate import Narrator, SIM_TAG, build_context, unverified_numbers
from intelligence.replay import ROOT, ReplaySim

DEM = os.path.join(ROOT, "dataset", "ml", "demand_full_features.csv")
pytestmark = pytest.mark.skipif(not os.path.exists(DEM), reason="needs dataset/ml CSVs")


class FakeLLM:
    def __init__(self, reply=None, fail=None):
        self.reply, self.fail, self.calls = reply, fail, []

    def complete(self, system, user, json_mode=False):
        self.calls.append((system, user))
        if self.fail:
            raise self.fail
        return self.reply(user) if callable(self.reply) else self.reply


@pytest.fixture(scope="module")
def crisis_out():
    """Real pipeline output mid-crisis (Dhaka spike + disrupted route) from the validated replay."""
    dem = pd.read_csv(DEM)
    sim, ass, rows = ReplaySim(dem, crisis=True), Assessor(), []
    for _ in range(300):
        out = ass.assess(copy.deepcopy(sim.snap), rows, policy="heuristic")
        if sim.snap.tick >= 100 and out["recommendations"] and \
                any(s["type"] == "event_active" for s in out["signals"]):
            return out          # first crisis tick with recommendations + an active event
        for r in out["recommendations"]:
            a = r["action"]; sim.submit(Move(a["route_id"], r["fuel_type"], a["quantity"], 0))
        rows = sim.step()
    pytest.fail("no crisis tick with recommendations found")


def test_number_guardrail():
    facts = {"quantity": 2990.0, "risk_before": 0.72, "stockout_before_h": 16.25}
    assert unverified_numbers("Send 2,990 L; risk 72% -> lower; runs out in 16.25 h", facts) == []
    assert unverified_numbers("Send 4,500 L now", facts) == ["4,500"]


def test_no_key_uses_template(crisis_out):
    n = Narrator(client=None, disabled_reason="OPENAI_API_KEY not set")
    rec = crisis_out["recommendations"][0]
    r = n.explain_recommendation(rec)
    assert r["source"] == "template" and r["text"] == rec["explanation"] and "not set" in r["reason"]


def test_llm_text_used_and_tagged(crisis_out):
    rec = crisis_out["recommendations"][0]
    q = rec["action"]["quantity"]
    fake = FakeLLM(f"{rec['station_id']} needs fuel; send {q:,.0f} L from {rec['action']['source_depot_id']}.")
    r = Narrator(client=fake).explain_recommendation(rec)
    assert r["source"] == "llm" and SIM_TAG in r["text"]
    assert "SIMULATED" in fake.calls[0][0] and str(int(q)) in fake.calls[0][1].replace(",", "")


def test_hallucinated_number_falls_back(crisis_out):
    rec = crisis_out["recommendations"][0]
    n = Narrator(client=FakeLLM("Send 123,456 L immediately."))
    r = n.explain_recommendation(rec)
    assert r["source"] == "template" and "123,456" in r["reason"] and n.stats["rejected_unverified"] == 1


def test_api_error_falls_back(crisis_out):
    n = Narrator(client=FakeLLM(fail=TimeoutError("read timed out")))
    r = n.summarize_state(crisis_out)
    assert r["source"] == "template" and "TimeoutError" in r["reason"] and "summary" in r["text"]
    assert n.metrics()["llm_failed"] == 1


def test_cache_avoids_repeat_calls(crisis_out):
    rec = crisis_out["recommendations"][0]
    fake = FakeLLM(f"Recommended {rec['action']['quantity']:,.0f} L.")
    n = Narrator(client=fake)
    n.explain_recommendation(rec); n.explain_recommendation(rec)
    assert len(fake.calls) == 1


def test_incident_and_context_contain_crisis_facts(crisis_out):
    ctx = build_context(crisis_out)
    types = {s["type"] for s in ctx["signals"]}
    assert "event_active" in types
    r = Narrator(client=None).explain_incident(crisis_out)
    assert r["source"] == "template" and "event active" in r["text"]


def test_investigate_offline_answers_from_facts(crisis_out):
    r = Narrator(client=None).investigate("Why is mirpur at risk?", crisis_out)
    assert "station-mirpur" in r["text"] and "offline" in r["text"]


def test_investigate_llm_gets_question_and_state(crisis_out):
    fake = FakeLLM("station-mirpur demand is elevated by the active demand_spike event.")
    r = Narrator(client=fake).investigate("Why is mirpur at risk?", crisis_out)
    assert r["source"] == "llm" and "QUESTION: Why is mirpur at risk?" in fake.calls[0][1]
    assert "station-mirpur" in fake.calls[0][1] and "demand_spike" in fake.calls[0][1]


def test_enrich_adds_all_outputs(crisis_out):
    out = copy.deepcopy(crisis_out)
    Narrator(client=None).enrich(out)
    assert {"incident_summary", "state_summary", "genai_stats"} <= set(out)
    assert all("explanation_text" in r for r in out["recommendations"][:3])
