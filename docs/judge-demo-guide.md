# Judge demo guide

Quick-reference for presenting the live system. For the DevOps-specific act-by-act script
(exact scenario-runner commands, talk track per act), see `docs/devops/demo-runbook.md` —
this file is the condensed version: what to open, what order, what to say, what to avoid.

Everything below was run live and verified working on `abrar/grafana-demo-data`
(stacked on `abrar/devops-integration`) — not narrated from a mock. See `docs/evidence/drills.md`
for recorded detection/recovery timings.

## URLs

| What | URL | Notes |
|---|---|---|
| **Operator dashboard** (main screen) | `http://localhost:8080` | sim controls, recommendations, alerts, health — no admin panel needed |
| Grafana — Command Center | `http://localhost:3000/d/fuel-command` | inventory/demand/allocations/decisions — open this first |
| Grafana — Resilience Drill | `http://localhost:3000/d/fuel-resilience` | alerts, fault annotations, fallback/degraded state |
| Grafana — Fuel Network | `http://localhost:3000/d/fuel-network` | depot/station/route detail, backup panel |
| Grafana — Platform Health | `http://localhost:3000/d/fuel-platform` | request rates, latency, intelligence internals, logs |
| Prometheus targets/alerts | `http://localhost:9090/targets`, `/alerts` | proof panel if a dashboard misbehaves |
| Alertmanager | `http://localhost:9093` | show alert firing/clearing in real time |
| Core direct (fallback only) | `http://localhost:8100/internal/health-summary` | raw JSON if the UI dies |

Grafana: anonymous Viewer works, or `admin` / `.env`'s `GRAFANA_ADMIN_PASSWORD`.

## Flow (~12 min)

1. **Baseline.** Dashboard open, sim RUNNING via the in-dashboard Run button (never the admin
   panel). Point at tick counter, inventory, Command Center dashboard's "Decisions per minute"
   and "Service level" panels moving.
2. **Demand pressure.** Inject `demand_spike` from the dashboard's event form (or
   `python ops/scenario-runner/scenario.py demand-spike`). Watch demand/risk shift on both the
   dashboard and Command Center.
3. **Recommend.** Fetch recommendations, show confidence/constraints/impact/alternatives on a
   card, approve one (HUMAN_REVIEW confirm gate — a human stays in the loop).
4. **Watch it execute.** Allocation status PENDING → IN_TRANSIT → ARRIVED as ticks advance;
   station inventory recovers.
5. **Break something.** Inject `route_disruption` on the route just used. Show the system
   reflecting the disruption; a subsequent recommendation reroutes around it.
6. **Kill Intelligence.** `python ops/scenario-runner/scenario.py kill intelligence` (prints
   detection time live — measured 22.3s in rehearsal). Dashboard flips to **"FALLBACK POLICY
   ACTIVE"**, keeps serving real `core_fallback_heuristic` recommendations, nothing crashes.
   This is the strongest beat — a real code path, not narrated.
7. **Corrupt the simulator response.** `python ops/scenario-runner/scenario.py bad-payload
   corrupt_json`. Health card goes to `degraded`, Resilience dashboard's fallback/error panels
   move, nothing 500s. `bad-payload off` to clear.
8. **Recover.** `python ops/scenario-runner/scenario.py start intelligence` (recovery measured
   18.2s in rehearsal). Alert clears, banner disappears, health returns to `healthy` automatically
   — no manual restart.
9. **Prove it's not fake.** Prometheus `/alerts`, Alertmanager firing history,
   `docs/evidence/drills.md` for the exact detection/recovery numbers just produced, Grafana
   annotations marking every injected event on the timeline.

## What's real behind each panel

Worth knowing so a follow-up question doesn't catch you flat-footed:

- **Command Center** "Decisions per minute" / "Fallbacks, last 5 min" — driven by Core's own
  `decisions_total` / `fallback_activations_total` counters, incremented once per recommendation
  actually returned to a caller (not simulated).
- **Resilience** "Simulator calls by status" / "Simulator API latency" — every single Core→simulator
  call is timed and counted, success or failure, via `record_simulator_call()` wrapping the client.
- **Platform Health** "Prediction confidence" / "Model inference p95" / "Shortage alerts" — sourced
  from Intelligence's own `intel_*` metrics (it instruments itself separately from Core's shared
  catalogue — the panels were retargeted to match, see commit on `abrar/grafana-demo-data`).
  Shortage alerts show an aggregate rate only — the counter isn't labeled by fuel/severity.
- **"Live decisions & incidents"** (Loki panel) — real structured logs, one line per recommendation,
  queryable by `decision_id`/`policy`/`outcome`.

## Known gaps — don't get caught off guard

- **Simulator circuit breaker panel will show "No data."** No circuit breaker is built yet
  (TASK-030) — Core retries via last-known-good state, not a breaker. If asked, say so directly;
  don't imply the panel is broken instrumentation, it's an unbuilt feature.
- **k6 load-test panels are empty** unless you've run the load test suite this session — optional,
  skip unless asked, or run it ahead of time and reference `loadtest/results/summary.md`.
- `python` is the working interpreter in this shell; `python3` may hit the Windows Store shim
  depending on machine — check with `python --version` before the demo, use whichever works.
- WebSocket push isn't built (TASK-021) — dashboard polls every 5s. Don't claim real-time push.
- k8s scripts exist but are unverified/unused — SPEC marks this optional, don't demo it.
- `docs/api-contracts.md`'s formal CONTRACT-CORE-API/CONTRACT-INTEL-OUTPUT are still a partial
  draft (TASK-003) — the code is real and live-verified, the contract doc just lags it.
- `SIMULATION_SPEED` defaults to `1` tick/sec in `.env.example` (good for live narration); check
  your actual `.env` isn't still set to a faster dev value before judges arrive.
