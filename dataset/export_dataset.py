"""Export a dataset from the local BUP Fuel Supply Simulator.

Usage (simulator must be running: `docker compose up -d`):
    python dataset/export_dataset.py              # 500 ticks
    python dataset/export_dataset.py --ticks 2000 --reset

Standard library only. Writes to dataset/data/:
  demand_history.csv   full per-tick demand series (all ticks, deduplicated)
  <endpoint>.json      final snapshot of every /v1 read endpoint
"""
import argparse, csv, json, os, sys, time, urllib.request, urllib.error

BASE = os.environ.get("SIM_URL", "http://localhost:8000")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
SNAPSHOTS = ["instance", "regions", "depots", "stations", "routes",
             "supply-arrivals", "events", "allocations", "metrics"]
ROWS_PER_TICK = 12   # 4 stations x 3 fuels
BATCH = 100          # ticks between demand-history pulls (1200 rows < 2000 cap)


def call(method, path, retries=5):
    for i in range(retries):
        try:
            req = urllib.request.Request(BASE + path, method=method,
                                         data=b"" if method == "POST" else None)
            with urllib.request.urlopen(req, timeout=30) as r:
                body = r.read()
                return json.loads(body) if body else None
        except (urllib.error.HTTPError, urllib.error.URLError) as e:
            if i == retries - 1:
                raise
            time.sleep(0.5 * (i + 1))  # faults (503) -> back off and retry


def pull_demand(rows):
    data = call("GET", f"/v1/demand-history?limit={min(2000, BATCH * ROWS_PER_TICK + 24)}")
    for r in data:
        rows[r["id"]] = r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticks", type=int, default=500)
    ap.add_argument("--reset", action="store_true", help="POST /admin/reset first")
    a = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)

    try:
        print("health:", call("GET", "/v1/health"))
    except Exception as e:
        sys.exit(f"Simulator not reachable at {BASE} ({e}). Run: docker compose up -d")
    if a.reset:
        call("POST", "/admin/reset")

    rows = {}
    pull_demand(rows)
    for t in range(1, a.ticks + 1):
        call("POST", "/admin/step")
        if t % BATCH == 0 or t == a.ticks:
            pull_demand(rows)
            print(f"tick {t}/{a.ticks}  rows={len(rows)}")

    ordered = sorted(rows.values(), key=lambda r: r["id"])
    if ordered:
        with open(os.path.join(OUT, "demand_history.csv"), "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(ordered[0].keys()))
            w.writeheader(); w.writerows(ordered)
    for ep in SNAPSHOTS:
        with open(os.path.join(OUT, ep.replace("-", "_") + ".json"), "w") as f:
            json.dump(call("GET", f"/v1/{ep}"), f, indent=2)
    print(f"Done. {len(ordered)} demand rows + {len(SNAPSHOTS)} snapshots in {OUT}")


if __name__ == "__main__":
    main()
