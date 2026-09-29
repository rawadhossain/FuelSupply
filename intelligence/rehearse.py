"""Rehearse the brief's 14-step demo story (SPEC §10) through the Intelligence Service, offline.

    python -m intelligence.rehearse            # writes intelligence/artifacts/demo_rehearsal.md

Uses the validated replay simulator (matches the live one within 0.2%) with the scripted crisis,
drives the FastAPI app in-process, executes recommendations like an operator would, and injects a
model failure to show monitoring + fallback. Uses OpenAI texts if OPENAI_API_KEY is set, else templates.
"""
from __future__ import annotations

import copy
import os

import pandas as pd
from fastapi.testclient import TestClient

from shared import heuristic
from shared.projection import Move

from . import service
from .payloads import sim_json
from .replay import ROOT, ReplaySim

OUT = os.path.join(ROOT, "intelligence", "artifacts", "demo_rehearsal.md")


def main() -> int:
    c = TestClient(service.app)
    sim = ReplaySim(pd.read_csv(os.path.join(ROOT, "dataset", "ml", "demand_full_features.csv")), crisis=True)
    log, rows = [], []

    def say(step, title, text):
        log.append(f"### {step}. {title}\n\n{text}\n")
        print(f"[{step}] {title}: {text.splitlines()[0][:140]}")

    def assess(narrate=False):
        r = c.post("/intel/assess", json={"snapshot": sim_json(sim), "demand_rows": rows, "narrate": narrate})
        return r

    def advance(to_tick, execute=True):
        nonlocal rows
        out = None
        while sim.snap.tick < to_tick:
            out = assess().json()
            if execute:
                for rec in out["recommendations"]:
                    a = rec["action"]; sim.submit(Move(a["route_id"], rec["fuel_type"], a["quantity"], 0))
            rows = sim.step()
        return out

    advance(40)
    out = assess().json()
    say(1, "Normal operations", f"Tick {out['tick']}: {sum(r['risk']=='high' for r in out['risks'])} high-risk pairs, "
        f"{len(out['recommendations'])} recommendations, policy {out['policy']}. Network: " +
        "; ".join(f"{f} {v['mode']}" for f, v in out["network"].items()))
    say(2, "Operator dashboard", "Top risks:\n\n" + "\n".join(
        f"- {r['station_id']} {r['fuel_type']}: {r['inventory']:,.0f} L, stockout in {r['stockout_in_hours_p50']} h, risk {r['risk']}"
        for r in out["risks"][:4]))
    advance(101)
    out = assess().json()
    sig = [s for s in out["signals"] if s["type"] in ("event_active", "demand_anomaly", "demand_anomaly_active")]
    say(3, "Demand starts increasing", f"Tick {out['tick']}: " + "; ".join(
        f"{s['type']} {s.get('evidence', {}).get('event_type', '')} {s['entity_id']}" for s in sig[:4]))
    say(4, "System detects risk", f"{len(out['signals'])} signals; types: " +
        ", ".join(sorted({s['type'] for s in out['signals']})))
    high = [r for r in out["risks"] if r["risk"] == "high"]
    say(5, "Intelligence predicts shortage", "\n".join(
        f"- {r['station_id']} {r['fuel_type']}: stockout in {r['stockout_in_hours_p50']} h (pessimistic {r['stockout_in_hours_p90']} h)"
        for r in (high or out["risks"][:3])))
    advance(121, execute=True)
    out = assess(narrate=True).json()
    rec = out["recommendations"][0]
    say(6, "Allocation recommendation generated", f"{rec['action']['quantity']:,.0f} L {rec['fuel_type']} "
        f"{rec['action']['source_depot_id']} -> {rec['station_id']} via {rec['action']['route_id']}; review {rec['review']}, "
        f"confidence {rec['confidence']}")
    say(7, "Operator inspects recommendation", f"{rec.get('explanation_text', rec['explanation'])}\n\n"
        f"(source: {rec.get('explanation_source', 'template')}) Alternatives: " +
        "; ".join(f"{a['route_id']} ({a['why_not']})" for a in rec["alternatives"]))
    before = sim.served
    for r in out["recommendations"]:
        a = r["action"]; sim.submit(Move(a["route_id"], r["fuel_type"], a["quantity"], 0))
    rows = sim.step()
    say(8, "Allocation is simulated", f"{len(out['recommendations'])} trucks accepted by the (replay) simulator; "
        f"served this tick {sim.served - before:,.0f} L")
    advance(150)
    # Organiser-style combined crisis while fuel still exists (all supply lands by ~tick 212, so a demo
    # crisis must come before ~tick 200): Gazipur->Mirpur road closed + Mirpur demand x1.6, ticks 151-200.
    from shared.snapshot import Event
    n = len(sim.snap.events)
    sim.snap.events += [Event(n + 1, "route_disruption", 151, 200, "SCHEDULED", {"route_ids": ["route-gazipur-mirpur"]}),
                        Event(n + 2, "demand_spike", 151, 200, "SCHEDULED", {"station_ids": ["station-mirpur"], "multiplier": 1.6})]
    adapted = None
    pre = assess().json()                                 # tick 150: events only SCHEDULED yet
    m0 = [r for r in pre["recommendations"] if r["station_id"] == "station-mirpur"]
    for r in pre["recommendations"]:
        a = r["action"]; sim.submit(Move(a["route_id"], r["fuel_type"], a["quantity"], 0))
    if m0:
        adapted = (pre["tick"], m0, "ahead of the closure, from the scheduled event")
    rows = sim.step()                                     # simulator processes tick 150 -> events start at 151
    advance(152)
    out = assess(narrate=True).json()
    say(9, "Crisis event occurs", out["incident_summary"]["text"])
    for _ in range(12):                                   # follow the next ticks until the system acts for Mirpur
        o = assess().json()
        m = [r for r in o["recommendations"] if r["station_id"] == "station-mirpur"]
        for r in o["recommendations"]:
            a = r["action"]; sim.submit(Move(a["route_id"], r["fuel_type"], a["quantity"], 0))
        if m and adapted is None:
            adapted = (o["tick"], m, "after the closure")
        if adapted:
            rows = sim.step(); break
        rows = sim.step()
    say(10, "System adapts", (f"Tick {adapted[0]} ({adapted[2]}): " + "; ".join(
        f"{r['action']['quantity']:,.0f} L {r['fuel_type']} to station-mirpur via {r['action']['route_id']} "
        f"(Gazipur road closed, so rerouted; {r['review']})" for r in adapted[1])) if adapted else
        "No Mirpur truck needed within 12 ticks. Bottlenecks: " + str(o["bottlenecks"]["at_risk_not_addressed"][:2]))
    saved = service.S.assessor
    service.S.assessor = None                                  # inject failure: model unavailable
    r = assess()
    say(11, "Dependency failure injected", f"Prediction model made unavailable -> /intel/assess returned "
        f"{r.status_code} {r.json()['detail']['code']}")
    h = c.get("/health")
    fb = [l for l in c.get("/metrics").text.splitlines() if l.startswith("intel_assess_requests_total")]
    say(12, "Monitoring detects failure", f"/health -> {h.status_code} {h.json()['status']}: "
        f"{h.json()['components']['prediction_model']}. Metrics: {'; '.join(fb)}")
    from .forecast import ProfileForecaster
    base = ProfileForecaster.load(os.path.join(ROOT, "intelligence", "artifacts", "profile-v1"))
    snap = copy.deepcopy(sim.snap)
    d = {(s, f): base.point([s] * 96, [f] * 96, [(snap.tick + k) % 96 for k in range(96)]) for s in snap.stations
         for f in snap.stations[s].capacity}
    import time as _t
    t0 = _t.perf_counter(); moves = heuristic.plan(snap, d, 96); ms = (_t.perf_counter() - t0) * 1000
    say(13, "Fallback activates", f"Core runs shared.heuristic.plan without the service ({ms:.0f} ms, no model files): " +
        (f"{len(moves)} truck(s), all marked fallback + HUMAN_REVIEW: " + "; ".join(
            f"{m.quantity:,.0f} L {m.fuel_type} via {m.route_id}" for m in moves[:3]) if moves else
         "no truck is urgent this tick, so none planned; operations stay covered by trucks already on the way"))
    service.S.assessor = saved
    r = assess()
    say(14, "Operations continue", f"Service restored: /health {c.get('/health').status_code}, /intel/assess {r.status_code}, "
        f"tick {r.json()['tick']}, policy {r.json()['policy']}")
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write("# Demo rehearsal (offline replay, crisis scenario)\n\nGenerated by `python -m intelligence.rehearse`. "
                 "All situations come from the validated replay of the real simulator. [Simulated environment]\n\n")
        fh.write("\n".join(log))
    print("written", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
