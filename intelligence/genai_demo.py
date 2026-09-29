"""Live check of the Generative-AI layer with your OpenAI key (reads .env).

    python -m intelligence.genai_demo

Builds a real mid-crisis situation with the validated replay (Dhaka demand spike, delayed Gazipur
deliveries, a route that just reopened), runs the pipeline, then asks OpenAI for: a decision
explanation, an incident explanation, a state summary and two investigation questions.
Prints each text with its source (llm/template) and writes intelligence/artifacts/genai_demo_output.json.
Never prints the API key.
"""
from __future__ import annotations

import copy
import json
import os

import pandas as pd

from shared.projection import Move

from .assess import Assessor
from .narrate import Narrator
from .replay import ROOT, ReplaySim

QUESTIONS = ["Why is station-mirpur at risk right now, and what is being done about it?",
             "Which depot is under the most pressure, and is any fuel type in network-wide shortage?"]


def crisis_state():
    dem = pd.read_csv(os.path.join(ROOT, "dataset", "ml", "demand_full_features.csv"))
    sim, ass, rows = ReplaySim(dem, crisis=True), Assessor(), []
    for _ in range(300):
        out = ass.assess(copy.deepcopy(sim.snap), rows, policy="heuristic")
        if sim.snap.tick >= 100 and out["recommendations"] and any(s["type"] == "event_active" for s in out["signals"]):
            return out
        for r in out["recommendations"]:
            a = r["action"]; sim.submit(Move(a["route_id"], r["fuel_type"], a["quantity"], 0))
        rows = sim.step()
    raise RuntimeError("no crisis tick found")


def main() -> int:
    n = Narrator.from_env()
    print(f"OpenAI enabled: {n.client is not None}" + (f"  ({n.disabled_reason})" if n.disabled_reason else "")
          + (f"  model={os.environ.get('OPENAI_MODEL', 'gpt-5.4-mini')}" if n.client else ""))
    out = crisis_state()
    print(f"Situation: tick {out['tick']}, {len(out['recommendations'])} recommendations, {len(out['signals'])} signals\n")
    n.enrich(out)
    report = {"tick": out["tick"], "recommendation": {"template": out["recommendations"][0]["explanation"],
                                                      "text": out["recommendations"][0]["explanation_text"],
                                                      "source": out["recommendations"][0]["explanation_source"]},
              "incident": out["incident_summary"], "state": out["state_summary"], "questions": []}
    print("== DECISION EXPLANATION [%s]\n%s\n" % (report["recommendation"]["source"], report["recommendation"]["text"]))
    print("== INCIDENT [%s]\n%s\n" % (out["incident_summary"]["source"], out["incident_summary"]["text"]))
    print("== STATE SUMMARY [%s]\n%s\n" % (out["state_summary"]["source"], out["state_summary"]["text"]))
    for q in QUESTIONS:
        a = n.investigate(q, out)
        report["questions"].append({"q": q, **a})
        print("== Q: %s [%s]\n%s\n" % (q, a["source"], a["text"]))
    report["stats"] = n.metrics()
    for part in [report["recommendation"], out["incident_summary"], out["state_summary"], *report["questions"]]:
        if part.get("reason"):
            print("note:", part["reason"])
    print("stats:", json.dumps(report["stats"]))
    path = os.path.join(ROOT, "intelligence", "artifacts", "genai_demo_output.json")
    json.dump(report, open(path, "w"), indent=2)
    print("written", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
