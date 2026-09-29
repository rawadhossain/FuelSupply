"""Evaluate the demand-anomaly detector on REAL simulator crisis data.

    python -m intelligence.eval_crisis

Input: dataset/ml/crisis_demand_labeled.csv + crisis_events.csv (from export_crisis_dataset.py).
Two modes:
  undeclared — expected = normal profile only: can the detector catch spikes nobody announced?
  declared   — expected includes the event multiplier (as the live system does): residual alarms
               should be rare, because declared events are reported by state signals instead.
Writes intelligence/artifacts/profile-v1/crisis_eval.json.
"""
from __future__ import annotations

import json
import os

import numpy as np
import pandas as pd

from .detect import CusumDetector
from .forecast import ProfileForecaster

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ART = os.path.join(ROOT, "intelligence", "artifacts", "profile-v1")


def main() -> int:
    d = pd.read_csv(os.path.join(ROOT, "dataset", "ml", "crisis_demand_labeled.csv"))
    ev = pd.read_csv(os.path.join(ROOT, "dataset", "ml", "crisis_events.csv"))
    m = ProfileForecaster.load(ART)
    dj = json.load(open(os.path.join(ART, "detector.json")))
    sigma = {tuple(k.split("|")): v for k, v in dj["sigma"].items()}
    # Re-derive labels from the events with the verified window (ticks start .. end inclusive);
    # the first export labelled [start, end), missing the last tick.
    stations = {"station-mirpur": "region-dhaka", "station-tongi": "region-dhaka",
                "station-karnaphuli": "region-chattogram", "station-coxsbazar": "region-chattogram"}
    d["mult"], d["event_id"], d["is_event"] = 1.0, 0, False
    for e in ev.itertuples():
        p = json.loads(e.parameters)
        win = (d.tick >= e.start_tick) & (d.tick <= e.end_tick)
        if e.type == "demand_spike":
            sids, rids = p.get("station_ids") or [], p.get("region_ids") or []
            hit = d.station_id.map(lambda s: (not sids and not rids) or s in sids or stations.get(s) in rids)
        elif e.type == "station_outage":
            hit = d.station_id.isin(p.get("station_ids") or list(stations))
        else:
            continue
        sel = win & hit
        if e.type == "demand_spike":
            d.loc[sel, "mult"] *= p.get("multiplier", 1.5)
            d.loc[sel & (d.event_id == 0), "event_id"] = e.id
        d.loc[sel, "is_event"] = True
    d["tod"] = d.tick % 96
    d["expected"] = m.point(d.station_id, d.fuel_type, d.tod)

    # how well does "profile x multiplier" explain crisis demand?
    spk = d[d.mult != 1.0]
    ratio = (spk.demand_liters / (spk.expected * spk.mult))
    fit = {"spike_rows": int(len(spk)), "actual_over_expected_x_multiplier_mean": round(float(ratio.mean()), 4),
           "mape_pct_with_multiplier": round(float((abs(ratio - 1)).mean() * 100), 2),
           "mape_pct_without_multiplier": round(float((abs(spk.demand_liters / spk.expected - 1)).mean() * 100), 2)}

    out = {"fit_during_spikes": fit}
    for mode in ("undeclared", "declared"):
        exp = d.expected * (d.mult if mode == "declared" else 1.0)
        det = CusumDetector(sigma=sigma, k=dj["k"], h=dj["h"])
        alarms = []
        for r, e in zip(d.sort_values(["tick", "station_id", "fuel_type"]).itertuples(),
                        exp.loc[d.sort_values(["tick", "station_id", "fuel_type"]).index]):
            s = det.update(r.station_id, r.fuel_type, int(r.tick), float(r.demand_liters), float(e))
            if s:
                alarms.append({**s, "during_event": bool(r.is_event)})
        per_event = []
        for e in ev.itertuples():
            if e.type != "demand_spike":
                continue
            rows = d[(d.tick >= e.start_tick) & (d.tick <= e.end_tick) & (d.mult != 1.0) &
                     ((d.event_id == e.id) | (e.id == 4))]
            series = rows.groupby(["station_id", "fuel_type"]).size().index.tolist()
            delays = []
            for s_, f_ in series:
                hits = [a["since_tick"] - e.start_tick + 1 for a in alarms if a["station_id"] == s_ and a["fuel_type"] == f_
                        and e.start_tick <= a["since_tick"] <= e.end_tick]
                delays.append(min(hits) if hits else None)
            found = [x for x in delays if x is not None]
            per_event.append({"event_id": e.id, "multiplier": json.loads(e.parameters).get("multiplier"),
                              "series": len(series), "detected": len(found),
                              "median_ticks": float(np.median(found)) if found else None,
                              "max_ticks": max(found) if found else None})
        normal_rows = d[~d.is_event]
        fa = [a for a in alarms if not a["during_event"]]
        out[mode] = {"alarms_total": len(alarms), "false_alarms_outside_events": len(fa),
                     "false_alarms_per_series_day": round(len(fa) / (len(normal_rows) / 96), 4),
                     "spike_events": per_event,
                     "note": "outages & route events are reported by state signals (signals.py), not by CUSUM"}
    json.dump(out, open(os.path.join(ART, "crisis_eval.json"), "w"), indent=2)
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
