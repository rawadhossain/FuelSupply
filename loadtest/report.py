#!/usr/bin/env python3
"""Turn a k6 JSON summary into docs/evidence/loadtest-<timestamp>.md. Stdlib only."""
import json
import sys
import urllib.request
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROM_URL = "http://localhost:9090"

# k6's Prometheus remote-write (-o experimental-prometheus-rw) publishes each
# metric as k6_<metric_name>, with trend metrics suffixed per stat, e.g.:
K6_METRIC_NAMES = [
    "k6_http_req_duration_p50",
    "k6_http_req_duration_p95",
    "k6_http_req_duration_p99",
    "k6_http_req_duration_avg",
    "k6_http_req_duration_max",
    "k6_http_reqs_total",
    "k6_http_req_failed_rate",
]


def prom_query(expr: str):
    try:
        url = f"{PROM_URL}/api/v1/query?{urllib.parse.urlencode({'query': expr})}"
        with urllib.request.urlopen(url, timeout=3) as r:
            data = json.load(r)
        result = data.get("data", {}).get("result", [])
        return float(result[0]["value"][1]) if result else None
    except Exception:
        return None


def hpa_replicas(target: str):
    import subprocess

    try:
        out = subprocess.run(
            ["kubectl", "get", "hpa", target, "-o", "jsonpath={.status.currentReplicas}"],
            capture_output=True, text=True, timeout=5,
        )
        return out.stdout.strip() if out.returncode == 0 else None
    except Exception:
        return None


def main():
    if len(sys.argv) < 3:
        print("usage: report.py <test-name> <summary.json>", file=sys.stderr)
        sys.exit(1)
    test_name, summary_path = sys.argv[1], sys.argv[2]
    summary = json.loads(Path(summary_path).read_text())

    metrics = summary.get("metrics", {})
    http_dur = metrics.get("http_req_duration", {}).get("values", {})
    http_fail = metrics.get("http_req_failed", {}).get("values", {})
    http_reqs = metrics.get("http_reqs", {}).get("values", {})
    thresholds_ok = all(
        all(v.get("ok", True) for v in m.get("thresholds", {}).values())
        for m in metrics.values()
    )

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    evidence_dir = ROOT / "docs" / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    out_path = evidence_dir / f"loadtest-{ts}.md"

    cpu = prom_query('sum(rate(process_cpu_seconds_total{job=~"core|intelligence"}[1m]))')
    mem = prom_query('sum(process_resident_memory_bytes{job=~"core|intelligence"})')
    replicas = hpa_replicas("intelligence")

    lines = [
        f"# Load test: {test_name}",
        "",
        f"- Generated: {ts}",
        f"- Workload: core+intelligence /health, /ready (see loadtest/k6/lib.js — TODO swap in the real decision endpoint)",
        f"- Duration: {summary.get('state', {}).get('testRunDurationMs', 'unknown')} ms",
        "",
        "## Results",
        "",
        f"- Total requests: {http_reqs.get('count', 'n/a')}",
        f"- Req/s: {http_reqs.get('rate', 'n/a')}",
        f"- avg: {http_dur.get('avg', 'n/a')} ms",
        f"- p50: {http_dur.get('med', 'n/a')} ms",
        f"- p90: {http_dur.get('p(90)', 'n/a')} ms",
        f"- p95: {http_dur.get('p(95)', 'n/a')} ms",
        f"- p99: {http_dur.get('p(99)', 'n/a')} ms",
        f"- max: {http_dur.get('max', 'n/a')} ms",
        f"- error rate: {http_fail.get('rate', 'n/a')}",
        f"- thresholds: {'PASS' if thresholds_ok else 'FAIL'}",
        "",
        "## Target resource usage (Prometheus, if reachable)",
        "",
        f"- CPU rate (core+intelligence): {cpu if cpu is not None else 'BLOCKED (prometheus unreachable)'}",
        f"- RSS bytes (core+intelligence): {mem if mem is not None else 'BLOCKED (prometheus unreachable)'}",
        f"- intelligence HPA replicas: {replicas if replicas else 'BLOCKED (kubectl unreachable or HPA absent)'}",
        "",
        "## k6 Prometheus metric names (for Grafana panel alignment)",
        "",
    ] + [f"- `{m}`" for m in K6_METRIC_NAMES]

    out_path.write_text("\n".join(lines) + "\n")
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
