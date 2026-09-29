#!/usr/bin/env bash
# Run a k6 test (smoke|load|stress|spike) via the grafana/k6 docker image,
# optionally pushing live metrics to Prometheus and Grafana annotations.
set -euo pipefail

TEST="${1:-smoke}"
case "$TEST" in
  smoke|load|stress|spike) ;;
  *) echo "usage: $0 <smoke|load|stress|spike>"; exit 1 ;;
esac

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RESULTS_DIR="$ROOT/loadtest/results"
mkdir -p "$RESULTS_DIR"

TS="$(date -u +%Y%m%dT%H%M%SZ)"
SUMMARY_JSON="$RESULTS_DIR/k6-${TEST}-${TS}.json"

NETWORK_ARG=(--network host)
if [ -n "${NETWORK:-}" ]; then
  NETWORK_ARG=(--network "$NETWORK")
fi

if [ -f "$ROOT/.env" ]; then
  set -a; source "$ROOT/.env"; set +a
fi
GRAFANA_ADMIN_PASSWORD="${GRAFANA_ADMIN_PASSWORD:-admin}"
GRAFANA_URL="${GRAFANA_URL:-http://localhost:3000}"
PROM_RW_URL="${K6_PROMETHEUS_RW_SERVER_URL:-http://localhost:9090/api/v1/write}"

annotate() {
  local text="$1"
  curl -sf -u "admin:${GRAFANA_ADMIN_PASSWORD}" -X POST "${GRAFANA_URL}/api/annotations" \
    -H "Content-Type: application/json" \
    -d "{\"text\":\"${text}\",\"tags\":[\"demo\",\"k6\"]}" >/dev/null 2>&1 || \
    echo "Grafana annotation skipped (unreachable)."
}

annotate "k6 ${TEST} started"

docker run --rm "${NETWORK_ARG[@]}" \
  -e K6_PROMETHEUS_RW_SERVER_URL="$PROM_RW_URL" \
  -e K6_PROMETHEUS_RW_TREND_STATS="p(50),p(95),p(99),avg,max" \
  -v "$ROOT/loadtest/k6:/scripts" \
  -v "$RESULTS_DIR:/results" \
  grafana/k6 run \
  --summary-export "/results/$(basename "$SUMMARY_JSON")" \
  -o experimental-prometheus-rw \
  "/scripts/${TEST}.js" || true

annotate "k6 ${TEST} ended"

if command -v python3 >/dev/null 2>&1; then
  python3 "$ROOT/loadtest/report.py" "$TEST" "$SUMMARY_JSON"
else
  python "$ROOT/loadtest/report.py" "$TEST" "$SUMMARY_JSON"
fi
