#!/usr/bin/env bash
# Smoke test for the running compose stack. Exits non-zero if any check fails.
# Host ports come from the environment or .env, with the same defaults as compose.
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ -f .env ]]; then
  # Avoid `source <(...)` process substitution: it hangs under some bash
  # builds (observed with Git Bash/MSYS on Windows). A plain temp file works
  # everywhere and also strips stray CRLFs from a Windows-edited .env.
  clean_env="$(mktemp)"
  trap 'rm -f "$clean_env"' EXIT
  tr -d '\r' < .env > "$clean_env"
  set -a
  # shellcheck disable=SC1090
  source "$clean_env"
  set +a
fi

TIMEOUT="${SMOKE_TIMEOUT:-120}"
CORE_URL="http://localhost:${CORE_PORT:-8100}/health"
INTEL_URL="http://localhost:${INTELLIGENCE_PORT:-8200}/health"
NGINX_URL="http://localhost:${FRONTEND_PORT:-8080}/nginx-health"
PROXY_URL="http://localhost:${FRONTEND_PORT:-8080}/api/health"

NAMES=("core /health" "intelligence /health" "frontend /nginx-health" "frontend /api/health (proxy)")
URLS=("$CORE_URL" "$INTEL_URL" "$NGINX_URL" "$PROXY_URL")
RESULTS=()
failed=0

wait_for() {
  local url="$1" deadline=$((SECONDS + TIMEOUT))
  until curl -fsS -o /dev/null --max-time 3 "$url" 2>/dev/null; do
    if ((SECONDS >= deadline)); then return 1; fi
    sleep 2
  done
}

for i in "${!URLS[@]}"; do
  if wait_for "${URLS[$i]}"; then
    RESULTS+=("PASS")
  else
    RESULTS+=("FAIL")
    failed=1
  fi
done

echo
printf '%-32s %-6s %s\n' "CHECK" "RESULT" "URL"
printf '%-32s %-6s %s\n' "--------------------------------" "------" "---"
for i in "${!URLS[@]}"; do
  printf '%-32s %-6s %s\n' "${NAMES[$i]}" "${RESULTS[$i]}" "${URLS[$i]}"
done
echo

if ((failed)); then
  echo "SMOKE: FAIL (timeout per check: ${TIMEOUT}s)" >&2
  exit 1
fi
echo "SMOKE: PASS"
