#!/usr/bin/env bash
# Run a k6 test (smoke|load|stress|spike|resilience) via the grafana/k6 docker
# image against the compose network, optionally pushing live metrics to
# Prometheus and Grafana annotations.
#
# Env vars:
#   SCENARIO   assess|recommend|state (default recommend) - passed through to k6
#   INTEL_URL  default http://intelligence:8200 (compose service DNS name)
#   CORE_URL   default http://core:8100 (compose service DNS name)
#   NETWORK    override the docker network name (auto-detected from
#              `docker compose config` otherwise; must NOT be "host" - this
#              targets Docker Desktop on Windows where --network host is a no-op)
set -euo pipefail

TEST="${1:-smoke}"
case "$TEST" in
  smoke|load|stress|spike|resilience) ;;
  *) echo "usage: $0 <smoke|load|stress|spike|resilience>"; exit 1 ;;
esac

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RESULTS_DIR="$ROOT/loadtest/results"
mkdir -p "$RESULTS_DIR"

TS="$(date -u +%Y%m%dT%H%M%SZ)"
SUMMARY_JSON="$RESULTS_DIR/k6-${TEST}-${TS}.json"

if [ -f "$ROOT/.env" ]; then
  set -a; source "$ROOT/.env"; set +a
fi
GRAFANA_ADMIN_PASSWORD="${GRAFANA_ADMIN_PASSWORD:-admin}"
GRAFANA_URL="${GRAFANA_URL:-http://localhost:3000}"
PROM_RW_URL="${K6_PROMETHEUS_RW_SERVER_URL:-http://prometheus:9090/api/v1/write}"
SCENARIO="${SCENARIO:-recommend}"
INTEL_URL="${INTEL_URL:-http://intelligence:8200}"
CORE_URL="${CORE_URL:-http://core:8100}"

# Auto-detect the compose network unless explicitly overridden. Never use
# --network host: this targets Docker Desktop on Windows where host
# networking doesn't expose compose service DNS names.
if [ -n "${NETWORK:-}" ]; then
  DETECTED_NETWORK="$NETWORK"
else
  DETECTED_NETWORK="$(cd "$ROOT" && docker compose config 2>/dev/null | awk '/^networks:/{f=1;next} f && /name:/{print $2; exit}')"
  if [ -z "$DETECTED_NETWORK" ]; then
    DETECTED_NETWORK="$(docker network ls --format '{{.Name}}' | grep -m1 -i bup || true)"
  fi
fi
if [ -z "$DETECTED_NETWORK" ] || [ "$DETECTED_NETWORK" = "host" ]; then
  echo "Could not auto-detect a usable compose network (got '${DETECTED_NETWORK:-<empty>}'). Set NETWORK=<name> explicitly." >&2
  exit 1
fi
echo "Using docker network: $DETECTED_NETWORK"

annotate() {
  local text="$1"
  curl -sf -u "admin:${GRAFANA_ADMIN_PASSWORD}" -X POST "${GRAFANA_URL}/api/annotations" \
    -H "Content-Type: application/json" \
    -d "{\"text\":\"${text}\",\"tags\":[\"demo\",\"k6\"]}" >/dev/null 2>&1 || \
    echo "Grafana annotation skipped (unreachable)."
}

annotate "k6 ${TEST} (${SCENARIO}) started"

# MSYS_NO_PATHCONV avoids Git-Bash-on-Windows mangling the container-side
# /scripts and /results paths (and the leading-slash args) into Windows paths
# before they reach `docker run`.
MSYS_NO_PATHCONV=1 docker run --rm --network "$DETECTED_NETWORK" \
  -e SCENARIO="$SCENARIO" \
  -e INTEL_URL="$INTEL_URL" \
  -e CORE_URL="$CORE_URL" \
  -e K6_PROMETHEUS_RW_SERVER_URL="$PROM_RW_URL" \
  -e K6_PROMETHEUS_RW_TREND_STATS="p(50),p(95),p(99),avg,max" \
  -v "$ROOT/loadtest/k6:/scripts" \
  -v "$RESULTS_DIR:/results" \
  grafana/k6 run \
  --summary-export "/results/$(basename "$SUMMARY_JSON")" \
  -o experimental-prometheus-rw \
  "/scripts/${TEST}.js" || true

annotate "k6 ${TEST} (${SCENARIO}) ended"

if command -v python3 >/dev/null 2>&1; then
  python3 "$ROOT/loadtest/report.py" "$TEST" "$SUMMARY_JSON" "$SCENARIO"
else
  python "$ROOT/loadtest/report.py" "$TEST" "$SUMMARY_JSON" "$SCENARIO"
fi
