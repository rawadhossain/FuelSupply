import logging
import os
import time
from datetime import datetime, timezone
from wsgiref.simple_server import make_server

import requests
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, generate_latest
from prometheus_client.core import GaugeMetricFamily

_TARGET_KEYS = ("region_id", "station_id", "depot_id", "route_id")
_MAX_TARGETS_LEN = 40


def _short_targets(parameters):
    if not isinstance(parameters, dict):
        return ""
    values = [str(parameters[k]) for k in _TARGET_KEYS if parameters.get(k)]
    joined = "+".join(values)
    return joined[:_MAX_TARGETS_LEN]


def _parse_iso(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None

SIMULATOR_BASE_URL = os.environ.get("SIMULATOR_BASE_URL", "http://simulator-api:8000")
PORT = int(os.environ.get("PORT", "9101"))
TIMEOUT = 2.0
MAX_STATION_SERIES = 200
MAX_SUPPLY_SERIES = 100

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s sim-exporter %(message)s")
log = logging.getLogger("sim-exporter")


def _get(path):
    return requests.get(f"{SIMULATOR_BASE_URL}{path}", timeout=TIMEOUT)


class SimulatorCollector:
    def __init__(self):
        self._current_tick = None
        self._events = None

    def collect(self):
        yield from self._collect_health()
        yield from self._collect_instance()
        yield from self._collect_metrics()
        yield from self._collect_depots()
        yield from self._collect_stations()
        yield from self._collect_routes()
        yield from self._collect_faults()
        yield from self._collect_events()
        yield from self._collect_supply()
        yield from self._collect_regions()

    def _fetch_events(self):
        if self._events is not None:
            return self._events
        try:
            resp = _get("/admin/events")
            self._events = resp.json() if resp.status_code == 200 else []
        except Exception as exc:
            log.warning("event scrape failed: %s", exc)
            self._events = []
        return self._events

    def _collect_instance(self):
        tick_minutes = GaugeMetricFamily("sim_tick_minutes", "Simulated minutes represented by one tick")
        try:
            resp = _get("/v1/instance")
            if resp.status_code == 200:
                data = resp.json()
                if data.get("tick") is not None:
                    self._current_tick = float(data["tick"])
                if data.get("tick_minutes") is not None:
                    tick_minutes.add_metric([], float(data["tick_minutes"]))
        except Exception as exc:
            log.warning("instance scrape failed: %s", exc)
        if tick_minutes.samples:
            yield tick_minutes

    def _collect_health(self):
        sim_up = GaugeMetricFamily("sim_up", "Simulator liveness via /v1/health (bypasses faults)")
        sim_tick = GaugeMetricFamily("sim_tick", "Current simulator tick")
        sim_running = GaugeMetricFamily("sim_running", "1 if simulation status is RUNNING")
        try:
            resp = _get("/v1/health")
            if resp.status_code == 200:
                sim_up.add_metric([], 1)
                sim_state = resp.json().get("simulation") or {}
                if sim_state.get("tick") is not None:
                    sim_tick.add_metric([], float(sim_state["tick"]))
                    self._current_tick = float(sim_state["tick"])
                if sim_state.get("status") is not None:
                    sim_running.add_metric([], 1.0 if sim_state["status"] == "RUNNING" else 0.0)
            else:
                sim_up.add_metric([], 0)
        except Exception as exc:
            log.warning("health check failed: %s", exc)
            sim_up.add_metric([], 0)
        yield sim_up
        if sim_tick.samples:
            yield sim_tick
        if sim_running.samples:
            yield sim_running

    def _collect_metrics(self):
        sim_api_up = GaugeMetricFamily("sim_api_up", "1 if /v1/metrics answered (faultable)")
        sim_latency = GaugeMetricFamily("sim_api_latency_seconds", "Latency of the last /v1/metrics call")
        sim_stale = GaugeMetricFamily("sim_stale", "1 if X-Simulator-Stale header seen on the last call")
        served = GaugeMetricFamily("sim_served_demand_liters", "Cumulative served demand")
        unmet = GaugeMetricFamily("sim_unmet_demand_liters", "Cumulative unmet demand")
        service_level = GaugeMetricFamily("sim_service_level", "Fraction of demand served")
        alloc_liters = GaugeMetricFamily("sim_allocation_liters", "Cumulative allocated liters")
        alloc_failures = GaugeMetricFamily("sim_allocation_failures", "Cumulative allocation failures")

        start = time.monotonic()
        try:
            resp = _get("/v1/metrics")
            sim_latency.add_metric([], time.monotonic() - start)
            sim_stale.add_metric([], 1.0 if resp.headers.get("X-Simulator-Stale") == "true" else 0.0)
            if resp.status_code == 200:
                sim_api_up.add_metric([], 1)
                data = resp.json()
                for family, key in (
                    (served, "served_demand_liters"),
                    (unmet, "unmet_demand_liters"),
                    (service_level, "service_level"),
                    (alloc_liters, "allocation_liters"),
                    (alloc_failures, "allocation_failures"),
                ):
                    if key in data:
                        family.add_metric([], float(data[key]))
            else:
                sim_api_up.add_metric([], 0)
        except Exception as exc:
            log.warning("metrics check failed: %s", exc)
            sim_api_up.add_metric([], 0)
            sim_latency.add_metric([], time.monotonic() - start)
            sim_stale.add_metric([], 0)

        yield sim_api_up
        yield sim_latency
        yield sim_stale
        for family in (served, unmet, service_level, alloc_liters, alloc_failures):
            if family.samples:
                yield family

    def _collect_depots(self):
        inventory = GaugeMetricFamily(
            "sim_depot_inventory_liters", "Depot inventory by fuel type", labels=["depot", "fuel"]
        )
        status_counts = GaugeMetricFamily("sim_depot_status", "Depots by status", labels=["status"])
        try:
            resp = _get("/v1/depots")
            if resp.status_code == 200:
                counts = {}
                for depot in resp.json():
                    depot_id = depot.get("id", "")
                    for fuel, qty in (depot.get("inventory") or {}).items():
                        inventory.add_metric([depot_id, fuel], float(qty))
                    status = depot.get("status", "unknown")
                    counts[status] = counts.get(status, 0) + 1
                for status, count in counts.items():
                    status_counts.add_metric([status], count)
        except Exception as exc:
            log.warning("depot scrape failed: %s", exc)
        if inventory.samples:
            yield inventory
        if status_counts.samples:
            yield status_counts

    def _collect_stations(self):
        status_counts = GaugeMetricFamily("sim_station_status", "Stations by status", labels=["status"])
        try:
            resp = _get("/v1/stations")
            if resp.status_code != 200:
                return
            stations = resp.json()
        except Exception as exc:
            log.warning("station scrape failed: %s", exc)
            return

        rows = []
        counts = {}
        for station in stations:
            station_id = station.get("id", "")
            region_id = station.get("region_id", "")
            capacity = station.get("capacity") or {}
            stock = station.get("inventory") or {}
            for fuel, cap in capacity.items():
                if cap:
                    rows.append((station_id, region_id, fuel, stock.get(fuel, 0) / cap))
            status = station.get("status", "unknown")
            counts[status] = counts.get(status, 0) + 1

        for status, count in counts.items():
            status_counts.add_metric([status], count)
        if status_counts.samples:
            yield status_counts

        if not rows:
            return

        ratio = GaugeMetricFamily(
            "sim_station_stock_ratio", "Station inventory/capacity ratio", labels=["station", "region", "fuel"]
        )
        if len(rows) > MAX_STATION_SERIES:
            agg = {}
            for _station_id, region_id, fuel, value in rows:
                agg.setdefault((region_id, fuel), []).append(value)
            for (region_id, fuel), values in agg.items():
                ratio.add_metric(["", region_id, fuel], sum(values) / len(values))
        else:
            for station_id, region_id, fuel, value in rows:
                ratio.add_metric([station_id, region_id, fuel], value)
        yield ratio

    def _collect_routes(self):
        status_counts = GaugeMetricFamily("sim_route_status", "Routes by status", labels=["status"])
        try:
            resp = _get("/v1/routes")
            if resp.status_code == 200:
                counts = {}
                for route in resp.json():
                    status = route.get("status", "unknown")
                    counts[status] = counts.get(status, 0) + 1
                for status, count in counts.items():
                    status_counts.add_metric([status], count)
        except Exception as exc:
            log.warning("route scrape failed: %s", exc)
        if status_counts.samples:
            yield status_counts

    def _collect_faults(self):
        active = GaugeMetricFamily("sim_active_faults", "Currently active injected faults", labels=["type"])
        remaining = GaugeMetricFamily(
            "sim_fault_remaining_seconds", "Seconds left on an active injected fault", labels=["type"]
        )
        try:
            resp = _get("/admin/faults")
            if resp.status_code == 200:
                counts = {}
                now = datetime.now(timezone.utc)
                for fault in resp.json():
                    if not fault.get("active"):
                        continue
                    fault_type = fault.get("type", "unknown")
                    counts[fault_type] = counts.get(fault_type, 0) + 1
                    end = _parse_iso(fault.get("end_wall_time"))
                    if end is not None:
                        if end.tzinfo is None:
                            end = end.replace(tzinfo=timezone.utc)
                        remaining.add_metric([fault_type], max(0.0, (end - now).total_seconds()))
                for fault_type, count in counts.items():
                    active.add_metric([fault_type], count)
        except Exception as exc:
            log.warning("fault scrape failed: %s", exc)
        if active.samples:
            yield active
        if remaining.samples:
            yield remaining

    def _collect_events(self):
        active = GaugeMetricFamily("sim_active_events", "Currently active crisis events", labels=["type"])
        remaining = GaugeMetricFamily(
            "sim_event_remaining_ticks",
            "Ticks left on an active crisis event",
            labels=["type", "event_id", "targets"],
        )
        counts = {}
        for event in self._fetch_events():
            if event.get("status") != "ACTIVE":
                continue
            event_type = event.get("type", "unknown")
            counts[event_type] = counts.get(event_type, 0) + 1
            end_tick = event.get("end_tick")
            if end_tick is not None and self._current_tick is not None:
                remaining.add_metric(
                    [event_type, str(event.get("id", "")), _short_targets(event.get("parameters"))],
                    max(0.0, float(end_tick) - self._current_tick),
                )
        for event_type, count in counts.items():
            active.add_metric([event_type], count)
        if active.samples:
            yield active
        if remaining.samples:
            yield remaining

    def _collect_supply(self):
        incoming = GaugeMetricFamily(
            "sim_incoming_supply_liters", "Supply not yet arrived at a depot", labels=["depot", "fuel", "status"]
        )
        eta = GaugeMetricFamily(
            "sim_supply_eta_ticks", "Ticks until scheduled supply arrival", labels=["depot", "fuel"]
        )
        try:
            resp = _get("/v1/supply-arrivals")
            if resp.status_code == 200:
                pending = [a for a in resp.json() if a.get("status") in ("SCHEDULED", "DELAYED")]
                for arrival in pending[:MAX_SUPPLY_SERIES]:
                    depot_id = arrival.get("depot_id", "")
                    fuel = arrival.get("fuel_type", "")
                    status = arrival.get("status", "unknown")
                    quantity = arrival.get("quantity")
                    if quantity is not None:
                        incoming.add_metric([depot_id, fuel, status], float(quantity))
                    planned_tick = arrival.get("planned_tick")
                    if planned_tick is not None and self._current_tick is not None:
                        eta.add_metric([depot_id, fuel], max(0.0, float(planned_tick) - self._current_tick))
        except Exception as exc:
            log.warning("supply-arrivals scrape failed: %s", exc)
        if incoming.samples:
            yield incoming
        if eta.samples:
            yield eta

    def _collect_regions(self):
        multiplier = GaugeMetricFamily(
            "sim_region_demand_multiplier", "Effective demand multiplier (baseline x any active demand spike)",
            labels=["region"],
        )
        try:
            resp = _get("/v1/regions")
            if resp.status_code == 200:
                spikes = {}
                for event in self._fetch_events():
                    if event.get("status") != "ACTIVE" or event.get("type") != "demand_spike":
                        continue
                    params = event.get("parameters") or {}
                    region_id = params.get("region_id")
                    mult = params.get("multiplier")
                    if region_id and mult is not None:
                        spikes[region_id] = float(mult)
                for region in resp.json():
                    region_id = region.get("id", "")
                    baseline = float(region.get("demand_factor", 1.0))
                    multiplier.add_metric([region_id], baseline * spikes.get(region_id, 1.0))
        except Exception as exc:
            log.warning("region scrape failed: %s", exc)
        if multiplier.samples:
            yield multiplier


def app(environ, start_response):
    path = environ.get("PATH_INFO", "")
    if path == "/healthz":
        start_response("200 OK", [("Content-Type", "text/plain")])
        return [b"ok"]
    if path != "/metrics":
        start_response("404 Not Found", [("Content-Type", "text/plain")])
        return [b"not found"]
    registry = CollectorRegistry()
    registry.register(SimulatorCollector())
    output = generate_latest(registry)
    start_response("200 OK", [("Content-Type", CONTENT_TYPE_LATEST)])
    return [output]


if __name__ == "__main__":
    log.info("listening on :%d, target=%s", PORT, SIMULATOR_BASE_URL)
    make_server("0.0.0.0", PORT, app).serve_forever()
