"""Run a scripted crisis scenario on the simulator and export LABELLED data
for evaluating anomaly/risk detection. All values come from the simulator;
labels come from the events we injected via /admin/events.

Run (simulator up):  python dataset/export_crisis_dataset.py
Output: dataset/ml/crisis_demand_labeled.csv, dataset/ml/crisis_events.csv
NOTE: this RESETS the simulator.
"""
import csv, json, os, sys, time, urllib.request

BASE = os.environ.get("SIM_URL", "http://localhost:8000")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ml")
TICKS = 672  # 7 sim-days

EVENTS = [  # (type, start, duration, parameters) -- spread out, one combined crisis
    ("demand_spike", 100, 48, {"region_ids": ["region-dhaka"], "multiplier": 1.8}),
    ("demand_spike", 250, 24, {"station_ids": ["station-karnaphuli"], "multiplier": 2.5}),
    ("station_outage", 350, 32, {"station_ids": ["station-coxsbazar"]}),
    ("demand_spike", 480, 48, {"multiplier": 1.4}),                      # combined crisis
    ("route_disruption", 480, 48, {"route_ids": ["route-gazipur-mirpur"]}),
    ("demand_spike", 600, 40, {"station_ids": ["station-tongi"], "multiplier": 1.3}),  # subtle
]


def call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else (b"" if method == "POST" else None)
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    for i in range(5):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                b = r.read(); return json.loads(b) if b else None
        except Exception:
            if i == 4: raise
            time.sleep(0.5 * (i + 1))


def main():
    os.makedirs(OUT, exist_ok=True)
    try: call("GET", "/v1/health")
    except Exception: sys.exit(f"Simulator not reachable at {BASE}. Run: docker compose up -d")
    call("POST", "/admin/reset")
    for t, s, dur, p in EVENTS:
        call("POST", "/admin/events", {"type": t, "start_tick": s, "duration_ticks": dur, "parameters": p})

    stations = {s["id"]: s["region_id"] for s in call("GET", "/v1/stations")}
    rows = {}
    for tick in range(1, TICKS + 1):
        call("POST", "/admin/step")
        if tick % 100 == 0 or tick == TICKS:
            for r in call("GET", "/v1/demand-history?limit=1500"): rows[r["id"]] = r
            print(f"tick {tick}/{TICKS} rows={len(rows)}")

    events = sorted(call("GET", "/v1/events"), key=lambda e: e["id"])
    def labels(r):
        hits = []
        for e in events:
            if not (e["start_tick"] <= r["tick"] <= e["end_tick"]): continue   # simulator applies events to ticks start..end inclusive
            p = e.get("parameters") or {}
            if e["type"] == "demand_spike":
                sids, rids = p.get("station_ids") or [], p.get("region_ids") or []
                if (not sids and not rids) or r["station_id"] in sids or stations[r["station_id"]] in rids:
                    hits.append(e)
            elif e["type"] == "station_outage":
                sids = p.get("station_ids") or []
                if not sids or r["station_id"] in sids: hits.append(e)
        return hits

    out = []
    for r in sorted(rows.values(), key=lambda x: x["id"]):
        h = labels(r)
        out.append({**r, "is_anomaly": int(bool(h)),
                    "event_types": "|".join(e["type"] for e in h),
                    "event_ids": "|".join(str(e["id"]) for e in h),
                    "spike_multiplier": next((e["parameters"].get("multiplier", 1.5) for e in h
                                              if e["type"] == "demand_spike"), 1.0)})
    with open(os.path.join(OUT, "crisis_demand_labeled.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(out[0].keys())); w.writeheader(); w.writerows(out)
    with open(os.path.join(OUT, "crisis_events.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["id", "type", "start_tick", "end_tick", "status", "parameters"])
        w.writeheader()
        for e in events:
            w.writerow({k: (json.dumps(e[k]) if k == "parameters" else e.get(k)) for k in w.fieldnames})
    print(f"Done: {len(out)} rows, {sum(o['is_anomaly'] for o in out)} labelled anomalous")


if __name__ == "__main__":
    main()
