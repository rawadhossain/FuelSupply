import logging
import os
import time
from wsgiref.simple_server import make_server

import requests
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, generate_latest
from prometheus_client.core import GaugeMetricFamily

SIMULATOR_BASE_URL = os.environ.get("SIMULATOR_BASE_URL", "http://simulator-api:8000")
PORT = int(os.environ.get("PORT", "9101"))
TIMEOUT = 2.0
MAX_STATION_SERIES = 200

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s sim-exporter %(message)s")
log = logging.getLogger("sim-exporter")


def _get(path):
    return requests.get(f"{SIMULATOR_BASE_URL}{path}", timeout=TIMEOUT)


class SimulatorCollector:
    def collect(self):
        yield from self._collect_health()
        yield from self._collect_metrics()
        yield from self._collect_depots()
        yield from self._collect_stations()
        yield from self._collect_faults()
        yield from self._collect_events()

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
        try:
            resp = _get("/v1/depots")
            if resp.status_code == 200:
                for depot in resp.json():
                    depot_id = depot.get("id", "")
                    for fuel, qty in (depot.get("inventory") or {}).items():
                        inventory.add_metric([depot_id, fuel], float(qty))
        except Exception as exc:
            log.warning("depot scrape failed: %s", exc)
        if inventory.samples:
            yield inventory

    def _collect_stations(self):
        try:
            resp = _get("/v1/stations")
            if resp.status_code != 200:
                return
            stations = resp.json()
        except Exception as exc:
            log.warning("station scrape failed: %s", exc)
            return

        rows = []
        for station in stations:
            station_id = station.get("id", "")
            region_id = station.get("region_id", "")
            capacity = station.get("capacity") or {}
            stock = station.get("inventory") or {}
            for fuel, cap in capacity.items():
                if cap:
                    rows.append((station_id, region_id, fuel, stock.get(fuel, 0) / cap))

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

    def _collect_faults(self):
        active = GaugeMetricFamily("sim_active_faults", "Currently active injected faults", labels=["type"])
        try:
            resp = _get("/admin/faults")
            if resp.status_code == 200:
                counts = {}
                for fault in resp.json():
                    if fault.get("active"):
                        fault_type = fault.get("type", "unknown")
                        counts[fault_type] = counts.get(fault_type, 0) + 1
                for fault_type, count in counts.items():
                    active.add_metric([fault_type], count)
        except Exception as exc:
            log.warning("fault scrape failed: %s", exc)
        if active.samples:
            yield active

    def _collect_events(self):
        active = GaugeMetricFamily("sim_active_events", "Currently active crisis events", labels=["type"])
        try:
            resp = _get("/admin/events")
            if resp.status_code == 200:
                counts = {}
                for event in resp.json():
                    if event.get("status") == "ACTIVE":
                        event_type = event.get("type", "unknown")
                        counts[event_type] = counts.get(event_type, 0) + 1
                for event_type, count in counts.items():
                    active.add_metric([event_type], count)
        except Exception as exc:
            log.warning("event scrape failed: %s", exc)
        if active.samples:
            yield active


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
