# Integration map

This map distinguishes the checked-out foundation branch from the integrated `origin/master` reference. Details of refs and merge ancestry are in [branch-inventory.md](branch-inventory.md). “Integrated-ref evidence” means files and tests exist in that ref; use [verification.md](verification.md) or the branch's own artifacts for run-specific proof.

| Integration | Producer | Consumer | Current `jessan_appli` | Later integrated `origin/master` | Main verification needed |
|---|---|---|---|---|---|
| Simulator REST reads | Simulator `/v1/*` | Core simulator client | Contracts/models documented; no running Core client | Client and ingestion implementation present | Live contract test; validate every response/error envelope |
| Simulator SSE | Simulator `/v1/stream` | Core listener | Contract rules documented; no listener | Listener/reconnect implementation present | Inject stream disconnect; prove REST resync and polling continuity |
| Intelligence request | Core state and demand history | Intelligence service | No service contract or assessment route in checked-out code | `POST /intel/assess` and Pydantic request/response models present | One-tick live call and correct stale/invalid input handling |
| Intelligence output | Forecasts, signals, risks, allocation candidates | Core and operator UI | Planned only | Assessment implementation and sample payloads present | Check explanation, constraints, alternatives, confidence, review fields |
| Fallback allocator | Snapshot + forecast | Core if Intelligence fails | Planned only | Shared heuristic module present | Stop Intelligence and show fallback marked degraded/HUMAN_REVIEW |
| Simulator allocation | Operator-approved recommendation | Simulator `/v1/allocations` | API behavior verified manually; no Core executor in checkout | Core executor and idempotency handling present | Retry/replay, all validation codes, cancel, post-write confirmation |
| Persistence and cache | Core state changes | Postgres / Redis | Compose services configured; no app stores in checkout | Store implementations present | Restart/reconcile and failure-path checks |
| Frontend API | Core REST/WebSocket | React dashboard | Placeholder UI | Later UI branch exists separately; not in `origin/master` at audit | Review `origin/Anindo-UI`; contract and integrate before claiming dashboard done |
| Metrics and monitoring | Core/Intelligence/exporter | Prometheus/Grafana/alerting | Scrape config exists, app metrics absent | Exporters and observability configuration present | Confirm targets up, panels populated, alert rule behavior |
| Demo and load evidence | Rehearsal/load scripts | Judges/team | Proposed demo only | Intelligence replay/load/demo artifacts present | Confirm timestamps, workload, environment, and live-vs-offline labeling |

## Ownership boundaries

- Core owns all simulator network access and all simulator writes.
- Intelligence accepts Core-owned snapshots; it must not poll or mutate simulator state.
- The frontend calls Core, never the simulator or Intelligence directly.
- Admin endpoints are test/rehearsal controls only. They bypass simulator fault injection and are not production data paths.
