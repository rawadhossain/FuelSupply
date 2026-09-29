# Evidence index

- [`drills.md`](drills.md) — resilience drill timing (detection/recovery seconds), one row per
  `kill`/`start`/fault-drill scenario run. Appended automatically by
  `ops/scenario-runner/scenario.py`.
- [`../../loadtest/results/summary.md`](../../loadtest/results/summary.md) and
  `loadtest/results/*.csv` — k6/locust load-test reports (pre-existing, see `loadtest/README.md`).
- `screenshots/` — dashboard/UI screenshots captured during verification or rehearsal.

Run `make evidence` to list every file currently under this directory.
