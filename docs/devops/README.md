# DevOps

## Run it

```bash
make up      # build images, start the stack (detached)
make smoke   # wait for health, print a PASS/FAIL table
make ps      # service status
make logs SERVICE=core   # follow one service's logs (omit SERVICE for all)
make down    # stop (keeps volumes)
make nuke    # stop and delete volumes (postgres, grafana data)
```

`make up` copies `.env.example` to `.env` on first run if `.env` doesn't exist yet.

## Ports

| Service | Container port | Host port (env var) | Notes |
|---|---|---|---|
| frontend (nginx) | 8080 | `FRONTEND_PORT` (default 8080) | proxies `/api/` → core, `/ws` → core |
| core | 8100 | `CORE_PORT` (default 8100) | |
| intelligence | 8200 | `INTELLIGENCE_PORT` (default 8200) | |
| simulator-api | 8000 | `SIMULATOR_PORT` (default 8000) | organizer image |
| postgres | 5432 | `POSTGRES_PORT` (default 5432) | bound to 127.0.0.1 only |
| redis | 6379 | `REDIS_PORT` (default 6379) | bound to 127.0.0.1 only |
| prometheus | 9090 | `PROMETHEUS_PORT` (default 9090) | bound to 127.0.0.1 only |
| grafana | 3000 | `GRAFANA_PORT` (default 3000) | anonymous access, Viewer role |

## CI (`.github/workflows/ci.yml`)

- **secrets** — gitleaks scan.
- **backend** — ruff + pytest for `core` and `intelligence` (matrix), blocking.
- **frontend** — `npm ci`, eslint, `vite build`, blocking.
- **images** — buildx build of all three images (GHA cache, `linux/amd64`, `GIT_SHA` build arg), Trivy scan (fails on CRITICAL with an available fix, reports HIGH without failing); on push to `master`, logs into GHCR and pushes `:sha` and `:latest` (`packages: write` scoped to that job only).
- **compose-smoke** — brings up the full stack from `.env.example` and runs `scripts/smoke.sh`; dumps `docker compose logs` on failure; always tears down.

Top-level permissions default to `contents: read`.

## Known limitations

- Ruff currently fails on `shared/fuelsupply_shared/models.py` (rule `UP045`, `Optional[X]` → `X | None`). Not fixed here — it's shared model code outside DevOps ownership. Needs `ruff check --fix shared` or a rule exclusion before backend CI will go green.
- No `actionlint`/`act` available in this environment; `ci.yml` was not validated locally, only by inspection.
- GHCR push requires the default `GITHUB_TOKEN` to have package write permission for the repo (org/repo setting), which isn't verifiable from here.
- Trivy step versions/DB are not pinned beyond the action tag; a CVE database refresh can change results between runs.
- No new services (message queue, object storage, etc.) are added — that's a later PR.
