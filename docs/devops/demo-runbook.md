# DevOps demo runbook

Maps the scenario runner / chaos proxy / dashboards to the demo story in `docs/demo-flow.md`
(steps 1-12) plus the resilience-drill acts that showcase observability. This is the DevOps
half only — application UI behavior (steps 3-6, 8) belongs to the frontend/intelligence owners
and is noted below as **[teammate]**.

## Pre-demo checklist

1. `.env` has `SIMULATION_SPEED=1` (repo default is 8 for faster local iteration — confirm before
   judges arrive, since 1 tick/sec is what makes live narration work: `grep SIMULATION_SPEED .env`).
2. `make up` — build and start the full stack.
3. `make demo-reset` (or `python ops/scenario-runner/scenario.py demo-reset`) — resets the
   simulator, clears faults, starts running.
4. Open Grafana (`http://localhost:3000`, anonymous Viewer or `admin`/`$GRAFANA_ADMIN_PASSWORD`):
   - **Fuel Command Center** dashboard (default home) — inventory, demand, allocations.
   - **Fuel Resilience** dashboard — alerts, fault/event annotations, circuit breaker state.
5. Put both dashboards in kiosk mode (`?kiosk` query param or the TV icon) on separate screens/tabs.
6. Confirm Alertmanager (`:9093`) shows no stale firing alerts from a previous rehearsal.

## Act-by-act

| Act | Command | Dashboard focus | Alert(s) expected | Talk track (~30s) |
|---|---|---|---|---|
| 1. Baseline | Demo-flow step 1. Simulator RUNNING, dashboards live. | Command Center: tick counter, inventory, demand. | none | "This is the live network state, straight from the simulator's API — nothing here is fabricated." |
| 2. Demand pressure | `python ops/scenario-runner/scenario.py demand-spike` | Command Center: demand line jumps ~2x on the affected region(s); Resilience: annotation marker. | `UnmetDemandRising`, `StockoutRisk` (organic, once stock draws down) | "We just doubled demand in Dhaka — watch the stock ratio panel start falling." |
| 3-6. Forecast → recommend → approve → dispatch | **[teammate]** operator UI flow. If the UI isn't ready, run `python ops/scenario-runner/scenario.py dispatch --minutes 2` as a stand-in to move allocations while narrating manually. | Command Center: allocation flow, inventory recovering. | none expected | "The recommendation becomes a real allocation the simulator executes — you can watch it move." |
| 7. Break something | `python ops/scenario-runner/scenario.py route-down` or `depot-constraint` | Resilience: route/depot status changes, annotation marker. | none directly (no alert rule on route/depot state alone) | "Now we break the route the operator was just relying on." |
| 8. Adapt | **[teammate]** decision engine reroutes or flags for review. | Command Center: new recommendation. | none | "The system adapts instead of failing silently." |
| 9. Dependency fault (scripted, timed) | `python ops/scenario-runner/scenario.py kill intelligence` — prints detection seconds live. | Resilience: `ServiceDown` fires, panel goes red; annotation marker at inject time. | `ServiceDown` | "The intelligence service just died — watch the alert fire in under 30 seconds, and the UI keep showing last-known-good state instead of crashing." |
| 10. Recovery | `python ops/scenario-runner/scenario.py start intelligence` — prints recovery seconds live. | Resilience: alert clears; annotation marker. | `ServiceDown` clears | "Recovery is automatic once the dependency comes back — no manual restart." Cite the actual detection/recovery numbers just printed (also logged to `docs/evidence/drills.md`). |
| 11. Status/metrics | Point at Resilience dashboard's alert history + `docs/evidence/drills.md` row just recorded. | Resilience: alert timeline. | n/a | "These aren't placeholder numbers — this is what just happened in the last two minutes." |
| 12. Load-test evidence (optional) | Reference `loadtest/results/summary.md`. | n/a (slide/screenshot) | n/a | "We also stress-tested the allocation path." |

### Bonus resilience beat (if time allows, between acts 10 and 11)

`python ops/scenario-runner/scenario.py bad-payload corrupt_json` then
`python ops/scenario-runner/scenario.py bad-payload off` — shows the chaos proxy corrupting
simulator responses in-flight and core's degraded-mode handling, then clean recovery. Both
calls post Grafana annotations automatically.

## If X goes wrong

- **Alert didn't fire in time**: check Prometheus `:9090/alerts` directly — rule may still be in
  pending (`for:` not yet elapsed); narrate from the raw `/api/v1/alerts` JSON instead of waiting.
- **Service didn't recover after `start`**: check `docker compose ps <service>` and
  `docker compose logs --tail=50 <service>`; if it's wedged, `docker compose restart <service>`.
- **Dashboards empty**: confirm Prometheus targets are up (`:9090/targets`); the sim-exporter and
  core/intelligence `/metrics` endpoints are the usual culprits — `docker compose ps sim-exporter`.
- **Simulator stuck paused**: `python ops/scenario-runner/scenario.py run`, or
  `curl -X POST localhost:8000/admin/run`.
- **Port already in use**: another `docker compose` project or a leftover container is holding it —
  `docker compose ps` in this repo first, then `docker ps` globally; change the `*_PORT` env var in
  `.env` as a last resort and re-run `make up`.

## Notes

- Every scenario-runner command posts a Grafana annotation at inject and at resolve/clear —
  visible as vertical markers on both dashboards, tagged `demo`.
- `demand-spike`, `route-down`, `station-outage`, `depot-constraint`, `shipment-delay`, and
  `supply-shortfall` auto-resolve after ~2.5 real minutes (scaled from `SIMULATION_SPEED`); no
  manual clear needed. Faults (`sim-*`, `stream-disconnect`) also auto-expire but can be force
  cleared with `python ops/scenario-runner/scenario.py clear`.
- `reset` and `demo-reset` wipe the simulator's state — the teammate's DB mirror (core/postgres)
  will be out of sync until it resyncs; the scenario prints this warning every time.
- `baseline-dispatch` is a **temporary** stand-in for the real decision engine so dashboards have
  something to show — drop it once the teammate's dispatcher ships.
