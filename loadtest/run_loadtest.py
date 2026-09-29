"""Start the service, run Locust at several concurrency levels, sample server CPU/memory, write results.

    python loadtest/run_loadtest.py            # levels 1, 10, 50 users x 30 s, heuristic; plus LP at 10
Writes loadtest/results/summary.json and summary.md.
"""
import csv
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request

import psutil

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "loadtest", "results")
PORT = int(os.environ.get("LOAD_PORT", "8177"))
RUNS = [("heuristic", 1), ("heuristic", 10), ("heuristic", 50), ("lp", 10)]
DURATION = os.environ.get("LOAD_DURATION", "30s")


def wait_up(url, secs=60):
    for _ in range(secs * 4):
        try:
            if urllib.request.urlopen(url, timeout=1).status == 200:
                return True
        except Exception:
            time.sleep(0.25)
    return False


def main():
    os.makedirs(OUT, exist_ok=True)
    env = {**os.environ, "OPENAI_API_KEY": ""}          # keep the LLM out of the load path
    srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "intelligence.service:app", "--host", "127.0.0.1",
                            "--port", str(PORT), "--log-level", "warning"], cwd=ROOT, env=env)
    try:
        if not wait_up(f"http://127.0.0.1:{PORT}/health"):
            raise SystemExit("service did not start")
        proc = psutil.Process(srv.pid)
        results = []
        for policy, users in RUNS:
            samples, stop = [], threading.Event()
            def sample():
                proc.cpu_percent(None)
                while not stop.is_set():
                    time.sleep(0.5)
                    samples.append((proc.cpu_percent(None), proc.memory_info().rss / 2**20))
            th = threading.Thread(target=sample); th.start()
            prefix = os.path.join(OUT, f"{policy}_{users}u")
            subprocess.run([sys.executable, "-m", "locust", "-f", os.path.join(ROOT, "loadtest", "locustfile.py"),
                            "--headless", "-u", str(users), "-r", str(users), "-t", DURATION,
                            "--host", f"http://127.0.0.1:{PORT}", "--csv", prefix, "--only-summary"],
                           cwd=ROOT, env={**env, "LOAD_POLICY": policy}, check=False,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            stop.set(); th.join()
            rows = {r["Name"]: r for r in csv.DictReader(open(prefix + "_stats.csv"))}
            a = rows["POST /intel/assess"]; agg = rows["Aggregated"]
            cpu = [c for c, _ in samples]; mem = [m for _, m in samples]
            res = {"policy": policy, "users": users, "duration": DURATION,
                   "assess_requests": int(a["Request Count"]), "assess_failures": int(a["Failure Count"]),
                   "assess_avg_ms": round(float(a["Average Response Time"]), 1),
                   "assess_p50_ms": float(a["50%"]), "assess_p95_ms": float(a["95%"]), "assess_p99_ms": float(a["99%"]),
                   "assess_rps": round(float(a["Requests/s"]), 1), "all_rps": round(float(agg["Requests/s"]), 1),
                   "error_rate_pct": round(100 * int(agg["Failure Count"]) / max(int(agg["Request Count"]), 1), 2),
                   "server_cpu_avg_pct": round(sum(cpu) / len(cpu), 1) if cpu else None,
                   "server_cpu_max_pct": round(max(cpu), 1) if cpu else None,
                   "server_rss_max_mb": round(max(mem), 1) if mem else None}
            results.append(res); print(json.dumps(res))
        json.dump(results, open(os.path.join(OUT, "summary.json"), "w"), indent=2)
        with open(os.path.join(OUT, "summary.md"), "w") as fh:
            fh.write("| policy | users | assess req | fail | avg ms | p50 | p95 | p99 | assess req/s | error % | CPU avg/max % | RSS MB |\n"
                     "|---|---|---|---|---|---|---|---|---|---|---|---|\n")
            for r in results:
                fh.write(f"| {r['policy']} | {r['users']} | {r['assess_requests']} | {r['assess_failures']} | {r['assess_avg_ms']} | "
                         f"{r['assess_p50_ms']} | {r['assess_p95_ms']} | {r['assess_p99_ms']} | {r['assess_rps']} | {r['error_rate_pct']} | "
                         f"{r['server_cpu_avg_pct']}/{r['server_cpu_max_pct']} | {r['server_rss_max_mb']} |\n")
        print("written", OUT)
    finally:
        srv.terminate(); srv.wait(10)


if __name__ == "__main__":
    main()
