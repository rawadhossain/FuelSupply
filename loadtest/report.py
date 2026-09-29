#!/usr/bin/env python3
"""Turn a k6 JSON summary into docs/evidence/loadtest-<timestamp>.md. Stdlib only."""
import json
import subprocess
import sys
import urllib.request
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROM_URL = "http://localhost:9090"

SCENARIO_PATHS = {
    "assess": "POST http://intelligence:8200/intel/assess",
    "recommend": "POST http://core:8100/internal/recommendations",
    "state": "GET http://core:8100/internal/state",
}

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


def docker_stats_fallback():
    """Fallback to `docker stats` (one-shot) if Prometheus process_* metrics
    aren't scraped for core/intelligence."""
    try:
        out = subprocess.run(
            ["docker", "stats", "--no-stream", "--format", "{{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}"],
            capture_output=True, text=True, timeout=10,
        )
        lines = [l for l in out.stdout.splitlines() if "core" in l or "intelligence" in l]
        return "\n".join(f"  - {l}" for l in lines) if lines else None
    except Exception:
        return None


def main():
    if len(sys.argv) < 3:
        print("usage: report.py <test-name> <summary.json> [scenario]", file=sys.stderr)
        sys.exit(1)
    test_name, summary_path = sys.argv[1], sys.argv[2]
    scenario = sys.argv[3] if len(sys.argv) > 3 else "recommend"
    summary = json.loads(Path(summary_path).read_text())

    metrics = summary.get("metrics", {})

    def metric_values(name):
        # k6's --summary-export shape varies by version: some nest stats
        # under .values, some put them directly on the metric object.
        m = metrics.get(name, {})
        return m.get("values", m)

    http_dur = metric_values("http_req_duration")
    http_fail = metric_values("http_req_failed")
    http_reqs = metric_values("http_reqs")
    vus_max_m = metric_values("vus_max")
    vus_max = vus_max_m.get("value", vus_max_m.get("max", "n/a")) if isinstance(vus_max_m, dict) else "n/a"

    # This k6 build's rate metric (http_req_failed) reports passes/fails/value
    # rather than a `rate` key. `value` is the already-computed rate (0..1)
    # and is authoritative - passes/fails are k6's internal true/false tallies
    # for the underlying boolean, not something to re-derive a rate from.
    if "rate" not in http_fail and "value" in http_fail:
        http_fail = {**http_fail, "rate": http_fail["value"]}
    reqs_count = http_reqs.get("count")
    if reqs_count is None:
        # fall back to failed-metric passes+fails as the total request count
        reqs_count = (http_fail.get("passes") or 0) + (http_fail.get("fails") or 0) or "n/a"
    reqs_rate = http_reqs.get("rate", "n/a")

    # p99 isn't emitted by this k6 build's summary trend stats (only p50/p90/
    # p95/avg/max); record that explicitly rather than silently showing n/a.
    p99_val = http_dur.get("p(99)", "not emitted by this k6 build (only p50/p90/p95 trends configured)")

    # This k6 build's per-metric `thresholds.<expr>: true/false` values are
    # unreliable here (observed p(95)=20s reported as `true` for a p(95)<500
    # threshold) - compute PASS/FAIL ourselves from the actual measured p95
    # and error rate instead of trusting k6's own threshold flag.
    p95_val = http_dur.get("p(95)")
    err_rate = http_fail.get("rate")
    thresholds_ok = (
        isinstance(p95_val, (int, float)) and p95_val < 500
        and isinstance(err_rate, (int, float)) and err_rate < 0.01
    )
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    evidence_dir = ROOT / "docs" / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    out_path = evidence_dir / f"loadtest-{ts}.md"

    # NOTE: these are point-in-time queries run just after the k6 process
    # exits, not a true peak-during-run (Prometheus has no default recording
    # rule capturing that here). max_over_time[10m] is used as a best-effort
    # approximation of the peak observed in roughly the run's own window.
    cpu_all = prom_query('max_over_time(rate(process_cpu_seconds_total{job="core"}[1m])[10m:1m])')
    cpu_all_i = prom_query('max_over_time(rate(process_cpu_seconds_total{job="intelligence"}[1m])[10m:1m])')
    mem_core = prom_query('max_over_time(process_resident_memory_bytes{job="core"}[10m])')
    mem_intel = prom_query('max_over_time(process_resident_memory_bytes{job="intelligence"}[10m])')
    stats_fallback = None
    if cpu_all is None and cpu_all_i is None:
        stats_fallback = docker_stats_fallback()

    lines = [
        f"# Load test: {test_name} ({scenario})",
        "",
        f"- Generated: {ts}",
        f"- Workload: {SCENARIO_PATHS.get(scenario, scenario)}",
        f"- Duration: {summary.get('state', {}).get('testRunDurationMs', 'unknown')} ms",
        "",
        "## Results",
        "",
        f"- Total requests: {reqs_count}",
        f"- Req/s: {reqs_rate}",
        f"- avg: {http_dur.get('avg', 'n/a')} ms",
        f"- p50: {http_dur.get('med', 'n/a')} ms",
        f"- p90: {http_dur.get('p(90)', 'n/a')} ms",
        f"- p95: {http_dur.get('p(95)', 'n/a')} ms",
        f"- p99: {p99_val}",
        f"- max: {http_dur.get('max', 'n/a')} ms",
        f"- error rate: {http_fail.get('rate', 'n/a')}",
        f"- peak concurrency (VUs): {vus_max}",
        f"- PASS/FAIL (p95<500ms and errors<1%): {'PASS' if thresholds_ok else 'FAIL'}",
        "",
        "## Target resource usage (peak over ~10min lookback, Prometheus process_* metrics)",
        "",
        f"- core peak process CPU rate (cores): {cpu_all if cpu_all is not None else 'BLOCKED (not scraped)'}",
        f"- intelligence peak process CPU rate (cores): {cpu_all_i if cpu_all_i is not None else 'BLOCKED (not scraped)'}",
        f"- core peak RSS bytes: {mem_core if mem_core is not None else 'BLOCKED (not scraped)'}",
        f"- intelligence peak RSS bytes: {mem_intel if mem_intel is not None else 'BLOCKED (not scraped)'}",
        "- LLM disabled during this run (OPENAI_API_KEY empty -> intelligence uses template narration, no OpenAI network hop)",
    ]
    if stats_fallback:
        lines += ["", "### docker stats fallback (Prometheus process_* unavailable)", "", stats_fallback]
    lines += [
        "",
        "## k6 Prometheus metric names (for Grafana panel alignment)",
        "",
    ] + [f"- `{m}`" for m in K6_METRIC_NAMES]

    out_path.write_text("\n".join(lines) + "\n")
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
