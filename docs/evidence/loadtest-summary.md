# Load-test summary

## Results

| Scenario | Profile | Path | Peak VUs | Total reqs | Req/s | p50 (ms) | p95 (ms) | p99 | Error % | PASS/FAIL (p95<500ms & err<1%) | Report |
|---|---|---|---|---|---|---|---|---|---|---|---|
| recommend | smoke | POST /internal/recommendations | 1 | 30 | 0.98 | 47.2 | 89.8 | n/a* | 0% | PASS | [loadtest-20260929T095010Z.md](loadtest-20260929T095010Z.md) |
| recommend | load | POST /internal/recommendations | 50 | 1,176 | 9.47 | 5,030 | 5,113 | n/a* | 0% | FAIL (latency) | [loadtest-20260929T095018Z.md](loadtest-20260929T095018Z.md) |
| assess | load | POST /intel/assess | 50 | 4,254 | 35.21 | 52.6 | 165 | n/a* | 0% | PASS | (report pruned; see stress row) |
| assess | stress | POST /intel/assess | 200 | 11,000 | 45.72 | 2,882 | 3,468 | n/a* | 0% | FAIL (latency) | [loadtest-20260929T095035Z.md](loadtest-20260929T095035Z.md) |
| state | load | GET /internal/state | 50 | 4,520 | 37.45 | 2.5 | 8.2 | n/a* | 0% | PASS | (report pruned; see recommend/load row) |
| recommend | resilience (2min, intelligence killed 20s mid-run) | POST /internal/recommendations | 50 | 853 | 6.77 | 5,110 | 20,009 | n/a* | 0% | FAIL (latency) | [loadtest-20260929T095052Z.md](loadtest-20260929T095052Z.md) |

\* p99 not emitted by this k6 build's summary export (only p50/p90/p95/avg/max trend stats configured); see per-run reports for p90.

## Identified breaking point

`assess` (`POST /intel/assess`, intelligence service, heuristic policy, LLM
disabled) stayed under the 500ms p95 threshold at 50 VUs (p95=165ms) but
crossed it well before 200 VUs (stress run: p95=3.47s at peak 200 VUs). Error
rate stayed at 0% throughout (0 of 11,000 requests failed) — the service
degrades by getting slower, not by rejecting requests, up to at least 200 VUs.
The exact crossover VU count between 50 and 200 was not bisected further
(hackathon-mode: one run per profile, no additional intermediate runs).

`recommend` (`POST /internal/recommendations`, the core->intelligence e2e
decision path) is far more latency-constrained even at only 50 VUs
(p95=5.1s at load profile) — it is already the slower path before any
stress ramp, consistent with it doing strictly more work per request
(build snapshot from live NetworkState, fetch bounded demand-history from
the simulator, call intelligence, log decisions/audit) than `/intel/assess`
alone.

## Resilience (intelligence outage during recommend load)

50 VUs against `/internal/recommendations` for 2 minutes; `intelligence`
container stopped at the 1-minute mark for 20s then restarted. HTTP error
rate stayed 0% for the entire run — core's `IntelligenceUnavailableError`
fallback path fired (confirmed in core logs: `"Intelligence unavailable,
using fallback allocator: TIMEOUT:"`, then `policy: "core_fallback_heuristic"`
decisions, all `outcome: "HUMAN_REVIEW"`) and every request still got a 200
with `degraded: true`, `policy: "fallback"`. The cost was latency, not
correctness: p95 rose to 20.0s (max 27.9s) during/around the outage window,
consistent with core waiting out its intelligence-client timeout before
falling back, then a recovery tail as the queue drained. See
[loadtest-20260929T095052Z.md](loadtest-20260929T095052Z.md).

## Conclusions

1. **The decision-support path degrades gracefully, not catastrophically.**
   Across every profile — including 200 VUs of sustained stress on `assess`
   and a live 20s intelligence outage — HTTP error rate never exceeded 0%.
   The system's failure mode under load is added latency, and during an
   actual dependency outage it is the documented fallback-allocator path
   (REQ-009a), not a crash or 5xx storm.
2. **`/internal/recommendations` (core's e2e bridge) is the real
   bottleneck, not `/intel/assess` (intelligence's own compute).** At the
   same 50-VU load profile, `assess` p95 is 165ms while `recommend` p95 is
   5.1s — over 30x slower — despite `recommend` calling `assess` internally.
   The gap points at core's own per-request work (live snapshot build,
   simulator demand-history fetch, DB/audit writes) as the limiting factor,
   not the intelligence service's heuristic computation itself.
3. **`assess`'s own saturation point is CPU/compute-bound heuristic
   assessment, not I/O** — request bodies were fixed-size real snapshots,
   latency scaled up smoothly with concurrency (50->200 VUs: p95 165ms->3.47s)
   with zero errors, matching a queueing/compute-bound service under
   concurrency pressure rather than a resource limit or connection-pool
   exhaustion (which would show as errors, not just slower successes).

## Environment caveats

- Docker Desktop on Windows; k6 run via `docker run --network bup_default`
  (not `--network host`, which is a no-op on Docker Desktop for Windows).
- Prometheus CPU/RSS figures in each per-run report are a `max_over_time`
  best-effort lookback at report-generation time, not a true instrumented
  peak-during-run capture.
- `docker compose ps` at the time of these runs showed containers already
  running; `core` and `intelligence` were rebuilt and force-recreated once
  at the start of this session to pick up already-committed source changes
  (`/internal/state`, `/internal/recommendations`, `/intel/assess` were not
  present in the previously-running images).