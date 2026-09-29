# Monitoring

## Ports

| Service | Container port | Host port (env var) | Notes |
|---|---|---|---|
| prometheus | 9090 | `PROMETHEUS_PORT` (default 9090) | bound to 127.0.0.1 |
| alertmanager | 9093 | `ALERTMANAGER_PORT` (default 9093) | bound to 127.0.0.1, single no-op receiver |
| loki | 3100 | `LOKI_PORT` (default 3100) | bound to 127.0.0.1, filesystem storage |
| alloy | 12345 | `ALLOY_PORT` (default 12345) | bound to 127.0.0.1, UI/debug endpoint only |
| cadvisor | 8080 | `CADVISOR_PORT` (default 8081) | bound to 127.0.0.1 |
| postgres-exporter | 9187 | `POSTGRES_EXPORTER_PORT` (default 9187) | bound to 127.0.0.1 |
| redis-exporter | 9121 | `REDIS_EXPORTER_PORT` (default 9121) | bound to 127.0.0.1 |
| sim-exporter | 9101 | `SIM_EXPORTER_PORT` (default 9101) | bound to 127.0.0.1, `/metrics` + `/healthz` |
| grafana | 3000 | `GRAFANA_PORT` (default 3000) | Prometheus/Loki/Alertmanager datasources provisioned |

## sim-exporter metrics

Custom collector, one live scrape of the simulator per Prometheus scrape, 2s timeouts, never crashes
(`sim_up`/`sim_api_up` drop to 0 and the scrape continues on any failure). Verified live against
`asifmahmoud414/bup-fuel-supply-simulator:1.0.0` — field names in `ops/sim-exporter/app.py` match the
simulator's real JSON exactly (`/v1/instance`, `/v1/depots`, `/v1/stations`, `/v1/metrics`,
`/admin/faults`, `/admin/events`), no mismatches found.

| Metric | Labels | Source |
|---|---|---|
| `sim_up` | — | `/v1/health` (bypasses faults) |
| `sim_tick` | — | `/v1/health` → `simulation.tick` |
| `sim_running` | — | `/v1/health` → `simulation.status == RUNNING` |
| `sim_api_up` | — | `/v1/metrics` (faultable) |
| `sim_api_latency_seconds` | — | last `/v1/metrics` call duration |
| `sim_stale` | — | `X-Simulator-Stale` header on the last call |
| `sim_served_demand_liters`, `sim_unmet_demand_liters`, `sim_service_level`, `sim_allocation_liters`, `sim_allocation_failures` | — | `/v1/metrics` |
| `sim_depot_inventory_liters` | `depot, fuel` | `/v1/depots` |
| `sim_station_stock_ratio` | `station, region, fuel` | `/v1/stations` (inventory/capacity); if series count would exceed 200 (not the case with the baseline scenario's 2 regions × 4 stations × 3 fuels = 24 series), aggregates to `region, fuel` and drops `station` |
| `sim_active_faults` | `type` | `/admin/faults`, counts `active == true` |
| `sim_active_events` | `type` | `/admin/events`, counts `status == "ACTIVE"` |

## Alert rules (`ops/prometheus/alerts.yml`)

`ServiceDown`, `SimulatorDown`, `SimulatorAPIUnavailable`, `SimulatorStaleData`, `SimulatorSlow`,
`HighErrorRate`, `HighP95Latency`, `DegradedModeActive`, `FallbackActivated`,
`LowPredictionConfidence`, `CircuitBreakerOpen`, `UnmetDemandRising`, `StockoutRisk`,
`ContainerHighMemory`, `ContainerRestarted`. Demo-tuned short `for:` values (0s–30s).

Fired and confirmed live in both Prometheus (`/api/v1/rules`, `ALERTS`) and Alertmanager
(`/api/v2/alerts`): `ServiceDown` (stopped `intelligence`), `SimulatorAPIUnavailable` (injected an
`unavailable` fault; `SimulatorDown` correctly stayed silent since `/v1/health` bypasses faults).
`StockoutRisk`/`UnmetDemandRising` also fire on their own once the simulator is `RUNNING` for a
while, since the baseline scenario's stations genuinely draw down below the 15% threshold.

## Known limitations

- **cAdvisor loses container name/compose labels on this Docker Desktop/WSL2 host.** Only raw
  cgroup `id` paths are exposed (`container_label_com_docker_compose_service` etc. are absent).
  `ContainerHighMemory`/`ContainerRestarted` were rewritten to match
  `id=~"/docker/[0-9a-f]{64}"` instead of a `name` label so they still fire — summaries show the
  raw cgroup id rather than a friendly container name.
- **Grafana's Alertmanager datasource `/health` check returns `"Plugin unavailable"` (500)** even
  though the datasource works — a direct proxy query
  (`/api/datasources/proxy/uid/alertmanager/api/v2/alerts`) returns real alert data. This is a
  known Grafana OSS quirk with the external-Alertmanager health-check code path, not a
  provisioning error; not worth chasing further for a demo.
- Alloy's `discovery.docker` target list can go briefly stale (~30–60s) right after
  `docker compose up` recreates containers, logging `No such container` for the old IDs before it
  self-heals and picks up the new ones. Log ingestion (confirmed via a live Loki
  `{service="core"}` query) works fine once it settles; this only matters if you're grepping logs
  in the first minute after `up`.
