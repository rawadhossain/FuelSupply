#!/usr/bin/env bash
# One-command load-test demo for Section 17 of ProblemStatement.md: `make demo-load`.
# Exercises the real end-to-end decision path POST /internal/recommendations
# (core -> intelligence), body built once in k6 setup() from a live
# GET /internal/state. Never calls /internal/allocations/execute or any
# /admin route other than the simulator's /admin/run preflight kick.
#
# Flags:
#   --scenario assess|state|recommend  (default recommend)
#   --chaos                            stop intelligence ~60s in for 15s
#   --llm-off                          recreate intelligence with an empty
#                                       OpenAI key for the run, restore after
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

SCENARIO="${SCENARIO:-recommend}"
CHAOS=0
LLM_OFF=0
while [ $# -gt 0 ]; do
  case "$1" in
    --scenario) SCENARIO="$2"; shift 2 ;;
    --scenario=*) SCENARIO="${1#*=}"; shift ;;
    --chaos) CHAOS=1; shift ;;
    --llm-off) LLM_OFF=1; shift ;;
    *) echo "unknown flag: $1" >&2; exit 1 ;;
  esac
done
case "$SCENARIO" in
  assess|state|recommend) ;;
  *) echo "usage: $0 [--scenario assess|state|recommend] [--chaos] [--llm-off]"; exit 1 ;;
esac

if [ -f "$ROOT/.env" ]; then
  set -a; source "$ROOT/.env"; set +a
fi
GRAFANA_ADMIN_PASSWORD="${GRAFANA_ADMIN_PASSWORD:-admin}"
GRAFANA_URL="${GRAFANA_URL:-http://localhost:${GRAFANA_PORT:-3000}}"
PROM_RW_URL="${K6_PROMETHEUS_RW_SERVER_URL:-http://prometheus:9090/api/v1/write}"
CORE_PORT="${CORE_PORT:-8100}"
INTELLIGENCE_PORT="${INTELLIGENCE_PORT:-8200}"
SIMULATOR_PORT="${SIMULATOR_PORT:-8000}"

echo "Scenario: $SCENARIO. narrate=false is hardcoded in the k6 request body, so"
echo "intelligence does NOT call the OpenAI API on this path by default; --llm-off"
echo "still forces an empty key on intelligence for the run as extra insurance."

# --- Preflight -------------------------------------------------------------
if ! curl -sf "http://localhost:${CORE_PORT}/health" >/dev/null 2>&1 || \
   ! curl -sf "http://localhost:${INTELLIGENCE_PORT}/health" >/dev/null 2>&1; then
  echo "core and/or intelligence are not answering /health. run make up first" >&2
  exit 1
fi
curl -sf -X POST "http://localhost:${SIMULATOR_PORT}/admin/run" >/dev/null 2>&1 || \
  echo "warning: simulator /admin/run did not respond (may already be running)"

# --- Network detection (never --network host; no-op on Docker Desktop) -----
DETECTED_NETWORK="$(docker compose config 2>/dev/null | awk '/^networks:/{f=1;next} f && /name:/{print $2; exit}')"
if [ -z "$DETECTED_NETWORK" ]; then
  DETECTED_NETWORK="$(docker network ls --format '{{.Name}}' | grep -m1 -i bup || true)"
fi
if [ -z "$DETECTED_NETWORK" ] || [ "$DETECTED_NETWORK" = "host" ]; then
  echo "Could not auto-detect a usable compose network." >&2
  exit 1
fi
echo "Using docker network: $DETECTED_NETWORK"

annotate() {
  curl -sf -u "admin:${GRAFANA_ADMIN_PASSWORD}" -X POST "${GRAFANA_URL}/api/annotations" \
    -H "Content-Type: application/json" \
    -d "{\"text\":\"$1\",\"tags\":[\"demo\"]}" >/dev/null 2>&1 || \
    echo "Grafana annotation skipped (unreachable)."
}

if [ "$LLM_OFF" = 1 ]; then
  echo "Recreating intelligence with an empty OpenAI key for this run..."
  OPENAI_API_KEY= docker compose up -d --no-deps intelligence >/dev/null
  sleep 3
fi
restore_llm() {
  if [ "$LLM_OFF" = 1 ]; then
    echo "Restoring intelligence's normal OpenAI key..."
    docker compose up -d --no-deps intelligence >/dev/null
  fi
}
trap restore_llm EXIT

RESULTS_DIR="$ROOT/loadtest/results"
mkdir -p "$RESULTS_DIR"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
SUMMARY_JSON="$RESULTS_DIR/k6-demo-${TS}.json"

annotate "Load test started (demo, ${SCENARIO})"

K6_ARGS=(run --summary-export "/results/$(basename "$SUMMARY_JSON")" -o experimental-prometheus-rw "/scripts/demo.js")

if [ "$CHAOS" = 1 ]; then
  CID="bup-demo-k6-${TS}"
  MSYS_NO_PATHCONV=1 docker run -d --name "$CID" --network "$DETECTED_NETWORK" \
    -e SCENARIO="$SCENARIO" -e CORE_URL="http://core:8100" -e INTEL_URL="http://intelligence:8200" \
    -e K6_PROMETHEUS_RW_SERVER_URL="$PROM_RW_URL" \
    -e K6_PROMETHEUS_RW_TREND_STATS="p(50),p(95),p(99),avg,max" \
    -v "$ROOT/loadtest/k6:/scripts" -v "$RESULTS_DIR:/results" \
    grafana/k6 "${K6_ARGS[@]}" >/dev/null

  sleep 60
  annotate "chaos: stopping intelligence"
  echo "Chaos: stopping intelligence for 15s..."
  docker compose stop intelligence >/dev/null

  OUTAGE_OK=0; OUTAGE_FAIL=0
  END=$((SECONDS + 15))
  while [ $SECONDS -lt $END ]; do
    if curl -sf -X POST "http://localhost:${CORE_PORT}/internal/recommendations" \
        -H 'Content-Type: application/json' -d '{"policy":"heuristic","narrate":false}' >/dev/null 2>&1; then
      OUTAGE_OK=$((OUTAGE_OK + 1))
    else
      OUTAGE_FAIL=$((OUTAGE_FAIL + 1))
    fi
    sleep 1
  done

  docker compose start intelligence >/dev/null
  annotate "chaos: intelligence restored"
  echo "Chaos: intelligence restored."

  docker wait "$CID" >/dev/null 2>&1 || true
  docker logs "$CID" 2>&1 | tail -40 || true
  docker rm "$CID" >/dev/null 2>&1 || true

  TOTAL_OUTAGE=$((OUTAGE_OK + OUTAGE_FAIL))
  echo ""
  echo "== Chaos outage window (core /internal/recommendations, intelligence down 15s) =="
  echo "  probes: $TOTAL_OUTAGE, ok: $OUTAGE_OK, failed: $OUTAGE_FAIL"
  if [ "$OUTAGE_OK" -gt 0 ]; then
    echo "  core kept answering during the outage (fallback engaged)."
  else
    echo "  core did NOT answer during the outage (no fallback observed)."
  fi
else
  MSYS_NO_PATHCONV=1 docker run --rm --network "$DETECTED_NETWORK" \
    -e SCENARIO="$SCENARIO" -e CORE_URL="http://core:8100" -e INTEL_URL="http://intelligence:8200" \
    -e K6_PROMETHEUS_RW_SERVER_URL="$PROM_RW_URL" \
    -e K6_PROMETHEUS_RW_TREND_STATS="p(50),p(95),p(99),avg,max" \
    -v "$ROOT/loadtest/k6:/scripts" -v "$RESULTS_DIR:/results" \
    grafana/k6 "${K6_ARGS[@]}" || true
fi

annotate "Load test ended (demo, ${SCENARIO})"

if [ ! -f "$SUMMARY_JSON" ]; then
  echo "k6 did not produce a summary export at $SUMMARY_JSON" >&2
  exit 0
fi

if command -v python3 >/dev/null 2>&1; then PY=python3; else PY=python; fi
REPORT_LINE="$("$PY" "$ROOT/loadtest/report.py" demo "$SUMMARY_JSON" "$SCENARIO")"
EVIDENCE_PATH="${REPORT_LINE#Wrote }"

echo ""
echo "== Section 17 demo load test ($SCENARIO) =="
sed -n '/^## Results/,/^## Target resource usage/p' "$EVIDENCE_PATH" | sed '$d'
echo ""
echo "Full report: $EVIDENCE_PATH"

exit 0
