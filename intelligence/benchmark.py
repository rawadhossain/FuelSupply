"""LIVE policy benchmark against the real simulator (REQ-028). Authoritative version of replay.py.

    python -m intelligence.benchmark --ticks 288 [--crisis] [--policies none,heuristic,lp]

For each policy: POST /admin/reset → (optional) inject the scripted crisis via /admin/events →
loop: read /v1/* snapshot, assess, POST /v1/allocations for every recommendation (auto-executed
for the benchmark only), POST /admin/step. Reads /v1/metrics at the end.
Writes intelligence/artifacts/benchmark_live_<scenario>_<ticks>.json and .md. RESETS THE SIMULATOR.
"""
from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request

from .assess import Assessor, InvalidInput
from .replay import CRISIS
from .snapshot import Snapshot

BASE = os.environ.get("SIM_URL", "http://localhost:8000")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENDPOINTS = ["instance", "regions", "stations", "depots", "routes", "supply-arrivals", "allocations", "events"]


def call(method, path, body=None, retries=5):
    data = json.dumps(body).encode() if body is not None else (b"" if method == "POST" else None)
    req = urllib.request.Request(BASE + path, data=data, method=method, headers={"Content-Type": "application/json"})
    for i in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if raw else None), dict(r.headers)
        except urllib.error.HTTPError as e:
            raw = e.read()
            if e.code in (409, 404, 422) or i == retries - 1:
                return e.code, (json.loads(raw) if raw else None), dict(e.headers)
        except urllib.error.URLError:
            if i == retries - 1:
                raise
        time.sleep(0.3 * (i + 1))


def snapshot() -> Snapshot:
    data, stale = {}, False
    for ep in ENDPOINTS:
        code, body, hdr = call("GET", f"/v1/{ep}")
        if code != 200:
            raise RuntimeError(f"GET /v1/{ep} -> {code}")
        stale |= hdr.get("X-Simulator-Stale", "").lower() == "true"
        data[ep] = body
    return Snapshot.from_api(data, stale=stale)


def run(policy: str, ticks: int, crisis: bool) -> dict:
    call("POST", "/admin/reset")
    if crisis:
        for t, st, dur, p in CRISIS:
            call("POST", "/admin/events", {"type": t, "start_tick": st, "duration_ticks": dur, "parameters": p})
    ass = Assessor()
    codes: dict[str, int] = {}
    lat, n_ok, invalid = [], 0, 0
    for _ in range(ticks):
        snap = snapshot()
        if policy != "none":
            _, rows, _ = call("GET", "/v1/demand-history?limit=24")
            rows = [r for r in (rows or []) if r["tick"] == snap.tick - 1]
            try:
                out = ass.assess(snap, rows, policy=policy)
            except InvalidInput:
                invalid += 1
                out = {"recommendations": [], "latency_ms": None}
            if out["latency_ms"] is not None:
                lat.append(out["latency_ms"])
            for rec in out["recommendations"]:
                a = rec["action"]
                body = {"idempotency_key": f"bench-{policy}-{snap.tick}-{a['route_id']}-{rec['fuel_type']}",
                        "source_depot_id": a["source_depot_id"], "destination_station_id": rec["station_id"],
                        "route_id": a["route_id"], "fuel_type": rec["fuel_type"], "quantity": a["quantity"]}
                code, resp, _ = call("POST", "/v1/allocations", body)
                key = str(code) if code in (200, 201) else (resp or {}).get("detail", {}).get("code", str(code)) \
                    if isinstance((resp or {}).get("detail"), dict) else str(code)
                codes[key] = codes.get(key, 0) + 1
                n_ok += code in (200, 201)
        call("POST", "/admin/step")
    _, m, _ = call("GET", "/v1/metrics")
    lat.sort()
    return {"policy": policy, "scenario": "crisis" if crisis else "baseline", "ticks": ticks, **m,
            "allocations_accepted": n_ok, "allocation_responses": codes, "invalid_snapshots": invalid,
            "assess_p50_ms": lat[len(lat) // 2] if lat else None,
            "assess_p95_ms": lat[int(len(lat) * 0.95)] if lat else None}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticks", type=int, default=288)
    ap.add_argument("--policies", default="none,heuristic,lp")
    ap.add_argument("--crisis", action="store_true")
    a = ap.parse_args()
    try:
        call("GET", "/v1/health", retries=2)
    except Exception:
        raise SystemExit(f"Simulator not reachable at {BASE}. Run: docker compose up -d")
    res = []
    for p in a.policies.split(","):
        t0 = time.time(); r = run(p, a.ticks, a.crisis); r["wall_s"] = round(time.time() - t0, 1)
        res.append(r); print(json.dumps(r))
    name = f"benchmark_live_{'crisis' if a.crisis else 'baseline'}_{a.ticks}"
    out = os.path.join(ROOT, "intelligence", "artifacts", name)
    json.dump(res, open(out + ".json", "w"), indent=2)
    with open(out + ".md", "w") as fh:
        fh.write(f"# Live benchmark — {res[0]['scenario']}, {a.ticks} ticks\n\n| policy | service_level | served L | unmet L | "
                 "allocations | failures | responses | p95 ms |\n|---|---|---|---|---|---|---|---|\n")
        for r in res:
            fh.write(f"| {r['policy']} | {r.get('service_level')} | {r.get('served_demand_liters')} | "
                     f"{r.get('unmet_demand_liters')} | {r['allocations_accepted']} | {r.get('allocation_failures')} | "
                     f"{r['allocation_responses']} | {r['assess_p95_ms']} |\n")
    print("written", out + ".json/.md")


if __name__ == "__main__":
    main()
