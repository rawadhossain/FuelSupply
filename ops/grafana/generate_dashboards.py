#!/usr/bin/env python3
"""Generate provisioned Grafana dashboard JSON for the Fuel Ops demo stack.

Stdlib only. Run: python ops/grafana/generate_dashboards.py
Writes JSON into ops/grafana/provisioning/dashboards/, which the file-based
dashboard provider (dashboards.yml) already watches.

Datasource uids match ops/grafana/provisioning/datasources/*.yml:
  prometheus -> "prometheus", loki -> "loki"
"""
import json
import os

PROM = "prometheus"
LOKI = "loki"

GREEN = "green"
AMBER = "orange"
RED = "red"
BLUE = "blue"

OUT_DIR = os.path.join(os.path.dirname(__file__), "provisioning", "dashboards")

_id_counter = {"n": 0}


def _next_id():
    _id_counter["n"] += 1
    return _id_counter["n"]


def ds(uid):
    return {"type": "prometheus" if uid == PROM else "loki", "uid": uid}


def target(uid, expr, legend=None, ref_id="A", instant=False, fmt="time_series"):
    t = {"datasource": ds(uid), "expr": expr, "refId": ref_id, "format": fmt}
    if legend:
        t["legendFormat"] = legend
    if instant:
        t["instant"] = True
    return t


class Layout:
    """Top-to-bottom grid packer for a 24-column dashboard."""

    def __init__(self):
        self.y = 0
        self.panels = []

    def row(self, title):
        panel = {
            "id": _next_id(), "type": "row", "title": title, "collapsed": False,
            "gridPos": {"h": 1, "w": 24, "x": 0, "y": self.y}, "panels": [],
        }
        self.panels.append(panel)
        self.y += 1
        return panel

    def line(self, panels_widths):
        x, max_h = 0, 0
        for panel, w in panels_widths:
            h = panel.pop("_h", 8)
            panel["gridPos"] = {"h": h, "w": w, "x": x, "y": self.y}
            self.panels.append(panel)
            x += w
            max_h = max(max_h, h)
            if x >= 24:
                x = 0
        self.y += max_h


def _panel_datasource(targets):
    uids = {t.get("datasource", {}).get("uid") for t in targets}
    if len(uids) == 1:
        return ds(next(iter(uids)))
    return {"type": "datasource", "uid": "-- Mixed --"}


def _base(panel_type, title, description, targets, height, extra_field_defaults=None, overrides=None, options=None):
    field_config = {"defaults": dict(extra_field_defaults or {}), "overrides": list(overrides or [])}
    panel = {
        "id": _next_id(),
        "type": panel_type,
        "title": title,
        "description": description,
        "datasource": _panel_datasource(targets) if targets else ds(PROM),
        "targets": targets,
        "fieldConfig": field_config,
        "_h": height,
    }
    if options is not None:
        panel["options"] = options
    return panel


def by_name_override(name, props):
    return {"matcher": {"id": "byName", "options": name}, "properties": [
        {"id": k, "value": v} for k, v in props.items()
    ]}


def steps(*pairs):
    return [{"color": c, "value": v} for c, v in pairs]


def stat_panel(title, description, targets, unit="short", thresholds=None, mappings=None,
               no_value_text=None, height=4, color_mode="value", graph_mode="area",
               text_size=None, orientation="auto", overrides=None, decimals=None):
    defaults = {"unit": unit}
    if decimals is not None:
        defaults["decimals"] = decimals
    if thresholds:
        defaults["thresholds"] = {"mode": "absolute", "steps": thresholds}
    if mappings:
        defaults["mappings"] = mappings
    if no_value_text is not None:
        defaults["noValue"] = no_value_text
    options = {
        "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
        "orientation": orientation, "textMode": "auto", "colorMode": color_mode,
        "graphMode": graph_mode, "justifyMode": "auto",
    }
    if text_size:
        options["text"] = text_size
    return _base("stat", title, description, targets, height, defaults, overrides, options)


def updown_stat(title, description, expr, height=4):
    return stat_panel(
        title, description, [target(PROM, expr)], unit="short",
        mappings=[{"type": "value", "options": {
            "0": {"text": "DOWN", "color": RED}, "1": {"text": "UP", "color": GREEN},
        }}],
        thresholds=steps((RED, None), (GREEN, 1)),
        no_value_text="NO DATA", height=height, color_mode="background",
    )


def timeseries_panel(title, description, targets, unit="short", height=8, stacking=None, draw_style=None,
                      overrides=None, thresholds=None, no_value_text=None, min_=None, max_=None):
    custom = {"drawStyle": draw_style or "line", "lineWidth": 1, "fillOpacity": 10, "pointSize": 5, "showPoints": "never"}
    if stacking:
        custom["stacking"] = {"mode": stacking, "group": "A"}
    defaults = {"unit": unit, "custom": custom}
    if min_ is not None:
        defaults["min"] = min_
    if max_ is not None:
        defaults["max"] = max_
    if thresholds:
        defaults["thresholds"] = {"mode": "absolute", "steps": thresholds}
        defaults["custom"]["thresholdsStyle"] = {"mode": "line"}
    if no_value_text is not None:
        defaults["noValue"] = no_value_text
    options = {"tooltip": {"mode": "multi"}, "legend": {"displayMode": "list", "placement": "bottom", "calcs": []}}
    return _base("timeseries", title, description, targets, height, defaults, overrides, options)


def gauge_panel(title, description, targets, unit="percentunit", min_=0, max_=1, height=8, thresholds=None):
    defaults = {"unit": unit, "min": min_, "max": max_, "thresholds": {"mode": "absolute", "steps": thresholds or steps((RED, None), (AMBER, 0.9), (GREEN, 0.97))}}
    return _base("gauge", title, description, targets, height, defaults,
                  options={"showThresholdLabels": False, "showThresholdMarkers": True})


def bargauge_panel(title, description, targets, unit="percentunit", height=8, thresholds=None, min_=0, max_=1):
    defaults = {"unit": unit, "min": min_, "max": max_, "thresholds": {"mode": "absolute", "steps": thresholds or steps((RED, None), (AMBER, 0.15), (GREEN, 0.25))}}
    return _base("bargauge", title, description, targets, height, defaults,
                  options={"orientation": "horizontal", "displayMode": "gradient", "reduceOptions": {"calcs": ["lastNotNull"], "values": False}})


def table_panel(title, description, targets, height=8, overrides=None, transformations=None):
    panel = _base("table", title, description, targets, height, {}, overrides, {"showHeader": True})
    if transformations:
        panel["transformations"] = transformations
    return panel


def logs_panel(title, description, targets, height=8):
    return _base("logs", title, description, targets, height, {}, options={
        "showTime": True, "showLabels": True, "wrapLogMessage": True,
        "sortOrder": "Descending", "enableLogDetails": True, "dedupStrategy": "none",
    })


def text_panel(title, content, height=6):
    panel = _base("text", title, "", [], height, {}, options={"mode": "markdown", "content": content})
    panel["datasource"] = ds(PROM)
    return panel


AWAIT_DECISION = "Awaiting decision engine"
AWAIT_METRIC = "No data yet"

HIDE_INSTANCE_JOB = [
    by_name_override("instance", {"custom.hidden": True}),
    by_name_override("job", {"custom.hidden": True}),
    by_name_override("__name__", {"custom.hidden": True}),
]


def _demo_annotation():
    return {
        "name": "Demo", "datasource": {"type": "grafana", "uid": "-- Grafana --"},
        "enable": True, "iconColor": "purple", "type": "tags", "tags": ["demo"],
    }


def _links(exclude_uid):
    dashboards = [
        ("fuel-command", "Command Center"),
        ("fuel-resilience", "Resilience Drill"),
        ("fuel-platform", "Platform Health"),
        ("fuel-network", "Fuel Network"),
    ]
    return [
        {"title": title, "url": f"/d/{uid}", "type": "link", "icon": "dashboard", "targetBlank": False}
        for uid, title in dashboards if uid != exclude_uid
    ]


def build_command_center():
    lay = Layout()
    big_status_mappings = [{"type": "value", "options": {
        "0": {"text": "OPERATIONAL", "color": GREEN},
        "1": {"text": "DEGRADED", "color": AMBER},
        "2": {"text": "INCIDENT", "color": RED},
    }}]

    # --- Top status section (no row header) ---
    lay.line([
        (stat_panel(
            "System state", "Worst of: firing critical/warning alerts and degraded mode. See the Why? panel for detail.",
            [target(PROM, "fuel:system_state")], mappings=big_status_mappings,
            thresholds=steps((GREEN, None), (AMBER, 1), (RED, 2)),
            no_value_text="OPERATIONAL", height=5, color_mode="background",
        ), 4),
        (table_panel(
            "Why?", "Every alert currently firing in Prometheus. Empty means nothing is firing.",
            [target(PROM, 'ALERTS{alertstate="firing"}', instant=True, fmt="table")],
            height=5,
            overrides=[
                by_name_override("Time", {"custom.hidden": True}),
                by_name_override("Value", {"custom.hidden": True}),
                by_name_override("alertstate", {"custom.hidden": True}),
                by_name_override("alertname", {"displayName": "Alert"}),
                by_name_override("severity", {"displayName": "Severity"}),
            ] + HIDE_INSTANCE_JOB,
        ), 8),
        (stat_panel(
            "Component status", "Is each core component reachable right now?",
            [
                target(PROM, 'up{job="core"}', "Backend API", ref_id="A", instant=True),
                target(PROM, 'up{job="intelligence"}', "Decision engine", ref_id="B", instant=True),
                target(PROM, "sim_up", "Fuel simulator", ref_id="C", instant=True),
                target(PROM, "sim_api_up", "Simulator API", ref_id="D", instant=True),
                target(PROM, "pg_up", "Database", ref_id="E", instant=True),
                target(PROM, "redis_up", "Cache", ref_id="F", instant=True),
            ],
            mappings=[{"type": "value", "options": {
                "0": {"text": "DOWN", "color": RED}, "1": {"text": "UP", "color": GREEN},
            }}],
            thresholds=steps((RED, None), (GREEN, 1)),
            no_value_text="NO DATA", height=5, color_mode="background", orientation="horizontal",
        ), 8),
        (stat_panel(
            "Simulation", "Is the simulator clock running, and at what tick?",
            [
                target(PROM, "sim_running", "State", ref_id="A", instant=True),
                target(PROM, "sim_tick", "Tick", ref_id="B", instant=True),
            ],
            mappings=[{"type": "value", "options": {
                "0": {"text": "PAUSED", "color": AMBER}, "1": {"text": "RUNNING", "color": GREEN},
            }}],
            no_value_text="NO DATA", height=5, color_mode="background", orientation="horizontal",
            overrides=[by_name_override("Tick", {
                "unit": "locale", "decimals": 0, "mappings": [],
                "color": {"mode": "fixed", "fixedColor": BLUE},
            })],
        ), 4),
    ])
    lay.line([
        (stat_panel(
            "p95 latency", "95th percentile HTTP request duration across all services, last 5 minutes.",
            [target(PROM, "fuel:http_p95_seconds * 1000")], unit="ms",
            thresholds=steps((GREEN, None), (AMBER, 300), (RED, 1000)),
            no_value_text=AWAIT_METRIC, height=3,
        ), 4),
        (stat_panel(
            "Error rate", "Share of HTTP requests returning a 5xx, last 5 minutes.",
            [target(PROM, "fuel:http_error_ratio * 100")], unit="percent",
            thresholds=steps((GREEN, None), (AMBER, 1), (RED, 5)),
            no_value_text="0", height=3,
        ), 4),
        (stat_panel(
            "Active disruptions", "Injected crisis events plus injected faults currently active.",
            [target(PROM, "(sum(sim_active_events) or vector(0)) + (sum(sim_active_faults) or vector(0))")],
            thresholds=steps((GREEN, None), (AMBER, 1)),
            no_value_text="0", height=3,
        ), 4),
        (stat_panel(
            "Out of fuel", "Stations completely out of at least one fuel type right now.",
            [target(PROM, "count(sim_station_stock_ratio == 0) or vector(0)")],
            thresholds=steps((GREEN, None), (RED, 1)),
            no_value_text="0", height=3,
        ), 4),
        (stat_panel(
            "Low stock", "Stations below 25% stock (but not yet empty) on at least one fuel type.",
            [target(PROM, "count(sim_station_stock_ratio > 0 and sim_station_stock_ratio < 0.25) or vector(0)")],
            thresholds=steps((GREEN, None), (AMBER, 1)),
            no_value_text="0", height=3,
        ), 4),
        (stat_panel(
            "Service level", "Share of demand served, trailing 5 minutes (held over brief pauses). "
                              "The cumulative figure since simulator start is tracked as fuel:service_level.",
            [target(PROM, "fuel:service_level_5m")], unit="percentunit",
            thresholds=steps((RED, None), (AMBER, 0.9), (GREEN, 0.97)),
            no_value_text=AWAIT_METRIC, height=3,
        ), 4),
    ])
    lay.line([
        (stat_panel(
            "Unmet demand, last 10 min", "Liters of demand not served in the last 10 minutes.",
            [target(PROM, "fuel:unmet_liters_10m")], unit="litre",
            thresholds=steps((GREEN, None), (AMBER, 1)),
            no_value_text="0", height=3,
        ), 6),
        (stat_panel(
            "Projected stockouts", "Stations trending toward empty within 6 simulated hours (baseline trend estimate).",
            [target(PROM, "count(fuel:station_hours_to_stockout < 6) or vector(0)")], unit="short",
            thresholds=steps((GREEN, None), (AMBER, 1)),
            no_value_text="0", height=3,
        ), 6),
        (stat_panel(
            "Decisions per minute", "Allocation decisions made by the decision engine.",
            [target(PROM, "fuel:decisions_per_min")], unit="short",
            no_value_text=AWAIT_DECISION, height=3,
        ), 6),
        (stat_panel(
            "Fallbacks, last 5 min", "Times the platform fell back to a safe default instead of a model decision.",
            [target(PROM, "fuel:fallbacks_5m")], unit="short",
            thresholds=steps((GREEN, None), (AMBER, 1)),
            no_value_text="0", height=3,
        ), 6),
    ])

    # --- Are we serving demand? / Where is fuel running out? ---
    lay.line([
        (timeseries_panel(
            "Service level (5-min)", "Trailing 5-minute service level, fixed 0-100% axis so dips are always visible.",
            [target(PROM, "fuel:service_level_5m", "Service level")], unit="percentunit",
            min_=0, max_=1, no_value_text=AWAIT_METRIC,
        ), 8),
        (timeseries_panel(
            "Unmet demand (rolling 10 min)", "Liters of demand not served, trailing 10-minute window.",
            [target(PROM, "fuel:unmet_liters_10m", "Unmet demand")], unit="litre", draw_style="bars",
            no_value_text="0",
        ), 8),
        (timeseries_panel(
            "Where is fuel running out?", "Lowest stock ratio per region, across all fuels. Amber below 25%, red below 15%.",
            [target(PROM, "min by (region) (sim_station_stock_ratio)", "{{region}}")], unit="percentunit",
            thresholds=steps((GREEN, None), (AMBER, 0.25), (RED, 0.15)), min_=0, max_=1,
        ), 8),
    ])

    # --- Table / responsiveness / logs ---
    lay.line([
        (table_panel(
            "Lowest fuel stock", "The 10 station/fuel combinations with the least stock right now, ascending.",
            [target(PROM, "bottomk(10, sim_station_stock_ratio)", instant=True, fmt="table")],
            height=7,
            overrides=[
                by_name_override("Time", {"custom.hidden": True}),
                by_name_override("station", {"displayName": "Station"}),
                by_name_override("region", {"displayName": "Region"}),
                by_name_override("fuel", {"displayName": "Fuel"}),
                by_name_override("Value", {"displayName": "Stock %", "unit": "percentunit"}),
            ] + HIDE_INSTANCE_JOB,
            transformations=[{"id": "sortBy", "options": {"fields": {}, "sort": [{"field": "Value"}]}}],
        ), 8),
        (timeseries_panel(
            "How is the platform responding?", "Request latency (left) and error rate (right) over time.",
            [
                target(PROM, "fuel:http_p95_seconds * 1000", "p95 latency (ms)", ref_id="A"),
                target(PROM, "fuel:http_error_ratio * 100", "Error rate %", ref_id="B"),
            ], unit="ms", height=7, no_value_text=AWAIT_METRIC,
            overrides=[by_name_override("Error rate %", {"unit": "percent", "custom.axisPlacement": "right"})],
        ), 8),
        (logs_panel(
            "Live decisions & incidents", "Decision events and error/warning logs from the backend and decision engine, newest first.",
            [
                target(LOKI, '{service=~"core|intelligence"} |= "\\"event\\":\\"decision\\"" | json request_id="request_id", decision_id="decision_id", policy="policy", outcome="outcome"', ref_id="A", fmt="logs"),
                target(LOKI, '{service=~"core|intelligence", level=~"error|warning"}', ref_id="B", fmt="logs"),
            ], height=7,
        ), 8),
    ])

    return {
        "uid": "fuel-command", "title": "Fuel Ops Command Center", "tags": ["fuel", "demo"],
        "timezone": "browser", "editable": True, "style": "dark", "refresh": "5s",
        "schemaVersion": 39, "version": 1, "time": {"from": "now-15m", "to": "now"},
        "panels": lay.panels, "links": _links("fuel-command"),
        "annotations": {"list": [_demo_annotation()]},
    }


def build_resilience_drill():
    lay = Layout()
    lay.row("")
    lay.line([
        (timeseries_panel(
            "Component health", "Up/down state of every core component over time.",
            [
                target(PROM, 'up{job="core"}', "Backend API", ref_id="A"),
                target(PROM, 'up{job="intelligence"}', "Decision engine", ref_id="B"),
                target(PROM, "sim_up", "Fuel simulator", ref_id="C"),
                target(PROM, "sim_api_up", "Simulator API", ref_id="D"),
                target(PROM, "pg_up", "Database", ref_id="E"),
                target(PROM, "redis_up", "Cache", ref_id="F"),
            ],
        ), 12),
        (timeseries_panel(
            "Firing alerts", "Which alerts are firing, and for how long.",
            [target(PROM, 'ALERTS{alertstate="firing"}', "{{alertname}}")],
        ), 12),
    ])
    lay.line([
        (timeseries_panel(
            "Degraded mode", "1 while a service is serving degraded/cached data instead of live data.",
            [target(PROM, "degraded_mode", "{{service}}")], no_value_text=AWAIT_METRIC,
        ), 8),
        (timeseries_panel(
            "Simulator circuit breaker", "Closed (healthy) / half-open (probing) / open (tripped).",
            [target(PROM, "simulator_circuit_state")], min_=0, max_=2,
        ), 8),
        (timeseries_panel(
            "Fallbacks per minute by reason", "Why the platform chose a fallback instead of a model decision.",
            [target(PROM, "sum by (reason) (rate(fallback_activations_total[1m])) * 60", "{{reason}}")],
            no_value_text=AWAIT_METRIC,
        ), 8),
    ])
    lay.line([
        (timeseries_panel(
            "Simulator API latency", "p95 duration of calls the backend makes to the simulator.",
            [target(PROM, "histogram_quantile(0.95, sum by (le) (rate(simulator_request_duration_seconds_bucket[1m])))")],
            unit="s",
        ), 8),
        (timeseries_panel(
            "Simulator calls by status", "Outcome of calls to the simulator API.",
            [target(PROM, "sum by (status) (rate(simulator_requests_total[1m]))", "{{status}}")],
            unit="reqps",
        ), 8),
        (timeseries_panel(
            "Request error ratio", "Share of HTTP requests returning a 5xx.",
            [target(PROM, "fuel:http_error_ratio")], unit="percentunit",
            thresholds=steps((GREEN, None), (AMBER, 0.01), (RED, 0.05)),
        ), 8),
    ])
    lay.line([
        (timeseries_panel(
            "Validation rejections", "Simulator responses rejected as invalid, by reason.",
            [target(PROM, "sum by (reason) (rate(validation_rejections_total[1m])) * 60", "{{reason}}")],
            no_value_text=AWAIT_METRIC,
        ), 12),
        (text_panel(
            "Resilience rules",
            "1. **ML unavailable** -> fall back to the rule-based policy.\n"
            "2. **Invalid simulator response** -> reject the payload and raise an alert.\n"
            "3. **Low prediction confidence** -> route the decision to human review.\n"
            "4. **Dependency down** -> serve cached state in degraded mode.",
            height=12,
        ), 12),
    ])
    return {
        "uid": "fuel-resilience", "title": "Resilience Drill", "tags": ["fuel", "demo"],
        "timezone": "browser", "editable": True, "style": "dark", "refresh": "5s",
        "schemaVersion": 39, "version": 1, "time": {"from": "now-30m", "to": "now"},
        "panels": lay.panels, "links": _links("fuel-resilience"),
        "annotations": {"list": [_demo_annotation()]},
    }


def build_platform_health():
    lay = Layout()

    lay.row("APPLICATION")
    lay.line([
        (timeseries_panel(
            "Request rate by service", "HTTP requests per second, per service.",
            [target(PROM, "sum by (service) (rate(http_requests_total[1m]))", "{{service}}")], unit="reqps",
        ), 8),
        (timeseries_panel(
            "p50 / p95 / p99 latency", "Request duration percentiles across all services.",
            [
                target(PROM, "histogram_quantile(0.50, sum by (le) (rate(http_request_duration_seconds_bucket[1m])))", "p50", ref_id="A"),
                target(PROM, "histogram_quantile(0.95, sum by (le) (rate(http_request_duration_seconds_bucket[1m])))", "p95", ref_id="B"),
                target(PROM, "histogram_quantile(0.99, sum by (le) (rate(http_request_duration_seconds_bucket[1m])))", "p99", ref_id="C"),
            ], unit="s",
        ), 8),
        (timeseries_panel(
            "5xx rate by service", "Server-error responses per second, per service.",
            [target(PROM, 'sum by (service) (rate(http_requests_total{status=~"5.."}[1m]))', "{{service}}")],
            unit="reqps", no_value_text="0",
        ), 8),
    ])

    lay.row("SYSTEM")
    lay.line([
        (timeseries_panel(
            "CPU by service", "Process CPU time, per scraped job.",
            [target(PROM, "rate(process_cpu_seconds_total[1m])", "{{job}}")], unit="percentunit",
        ), 6),
        (timeseries_panel(
            "Memory by service", "Resident memory, per scraped job.",
            [target(PROM, "process_resident_memory_bytes", "{{job}}")], unit="bytes",
        ), 6),
        (timeseries_panel(
            "Postgres connections", "Active backend connections reported by postgres_exporter.",
            [target(PROM, "sum(pg_stat_database_numbackends)")],
        ), 6),
        (timeseries_panel(
            "Redis memory", "Memory used, reported by redis_exporter.",
            [target(PROM, "redis_memory_used_bytes")], unit="bytes",
        ), 6),
    ])

    lay.row("INTELLIGENCE")
    lay.line([
        (timeseries_panel(
            "Decisions by policy/outcome", "Decision throughput, split by which policy ran and what it decided.",
            [target(PROM, "sum by (policy, outcome) (rate(decisions_total[1m])) * 60", "{{policy}} / {{outcome}}")],
            unit="short", no_value_text=AWAIT_DECISION,
        ), 8),
        (timeseries_panel(
            "Prediction confidence", "Model confidence: average and p95.",
            [
                target(PROM, "avg(rate(prediction_confidence_sum[5m])) / avg(rate(prediction_confidence_count[5m]))", "avg", ref_id="A"),
                target(PROM, "histogram_quantile(0.95, sum by (le) (rate(prediction_confidence_bucket[5m])))", "p95", ref_id="B"),
            ], unit="percentunit", no_value_text=AWAIT_DECISION,
        ), 8),
        (timeseries_panel(
            "Model inference p95", "p95 model inference duration.",
            [target(PROM, "histogram_quantile(0.95, sum by (le) (rate(model_inference_seconds_bucket[1m])))")],
            unit="s", no_value_text=AWAIT_DECISION,
        ), 8),
    ])
    lay.line([
        (timeseries_panel(
            "Shortage alerts by fuel/severity", "Shortage alerts raised by the decision engine.",
            [target(PROM, "sum by (fuel, severity) (rate(shortage_alerts_total[1m])) * 60", "{{fuel}} / {{severity}}")],
            unit="short", no_value_text=AWAIT_DECISION,
        ), 24),
    ])

    lay.row("LOAD TEST")
    lay.line([
        (timeseries_panel(
            "k6 request rate", "Load-test request throughput, when a k6 run is pushing metrics.",
            [target(PROM, "sum(rate(k6_http_reqs_total[1m]))")], unit="reqps",
            no_value_text="Run: make loadtest T=load",
        ), 8),
        (timeseries_panel(
            "k6 virtual users", "Concurrent virtual users during a load-test run.",
            [target(PROM, "sum(k6_vus)")], unit="short",
            no_value_text="Run: make loadtest T=load",
        ), 8),
        (timeseries_panel(
            "k6 request duration p95", "p95 request duration as seen by the load-test client.",
            [target(PROM, "histogram_quantile(0.95, sum by (le) (rate(k6_http_req_duration_bucket[1m])))")],
            unit="s", no_value_text="Run: make loadtest T=load",
        ), 8),
    ])

    lay.row("LOGS")
    lay.line([
        (timeseries_panel(
            "Log volume by level", "Log line rate, split by level label.",
            [target(LOKI, 'sum by (level) (count_over_time({service=~"core|intelligence"}[1m]))', "{{level}}")],
        ), 24),
    ])

    return {
        "uid": "fuel-platform", "title": "Platform Health", "tags": ["fuel", "demo"],
        "timezone": "browser", "editable": True, "style": "dark", "refresh": "5s",
        "schemaVersion": 39, "version": 1, "time": {"from": "now-15m", "to": "now"},
        "panels": lay.panels, "links": _links("fuel-platform"),
        "annotations": {"list": [_demo_annotation()]},
    }


def build_fuel_network():
    lay = Layout()
    lay.line([
        (timeseries_panel(
            "Depot inventory by fuel", "Liters held at each depot, stacked by fuel type.",
            [target(PROM, "sim_depot_inventory_liters", "{{depot}} / {{fuel}}")],
            unit="litre", stacking="normal", height=8,
        ), 12),
        (table_panel(
            "Incoming supply", "Supply scheduled or delayed, not yet arrived at a depot.",
            [target(PROM, "sim_incoming_supply_liters", instant=True, fmt="table")],
            height=8,
            overrides=[
                by_name_override("Time", {"custom.hidden": True}),
                by_name_override("depot", {"displayName": "Depot"}),
                by_name_override("fuel", {"displayName": "Fuel"}),
                by_name_override("status", {"displayName": "Status"}),
                by_name_override("Value", {"displayName": "Liters", "unit": "litre"}),
            ] + HIDE_INSTANCE_JOB,
        ), 12),
    ])
    lay.line([
        (stat_panel(
            "Regional demand multiplier", "Baseline demand factor times any active demand-spike multiplier, per region.",
            [target(PROM, "sim_region_demand_multiplier", "{{region}}", instant=True)],
            unit="short", height=6, color_mode="background", orientation="horizontal",
            thresholds=steps((GREEN, None), (AMBER, 1.3), (RED, 1.8)),
            no_value_text=AWAIT_METRIC,
        ), 8),
        (stat_panel(
            "Station status", "Stations by operational status.",
            [target(PROM, "sim_station_status", "{{status}}", instant=True)],
            unit="short", height=6, color_mode="background", orientation="horizontal",
            no_value_text=AWAIT_METRIC,
        ), 8),
        (stat_panel(
            "Depot / route status", "Depots and routes by operational status.",
            [
                target(PROM, "sim_depot_status", "Depot: {{status}}", ref_id="A", instant=True),
                target(PROM, "sim_route_status", "Route: {{status}}", ref_id="B", instant=True),
            ],
            unit="short", height=6, color_mode="background", orientation="horizontal",
            no_value_text=AWAIT_METRIC,
        ), 8),
    ])
    lay.line([
        (table_panel(
            "Active events", "Crisis events currently active, with ticks remaining.",
            [target(PROM, "sim_event_remaining_ticks", instant=True, fmt="table")],
            height=8,
            overrides=[
                by_name_override("Time", {"custom.hidden": True}),
                by_name_override("type", {"displayName": "Event"}),
                by_name_override("event_id", {"displayName": "ID"}),
                by_name_override("targets", {"displayName": "Targets"}),
                by_name_override("Value", {"displayName": "Ticks left"}),
            ] + HIDE_INSTANCE_JOB,
        ), 12),
        (table_panel(
            "Active faults", "Injected faults currently active, with seconds remaining.",
            [target(PROM, "sim_fault_remaining_seconds", instant=True, fmt="table")],
            height=8,
            overrides=[
                by_name_override("Time", {"custom.hidden": True}),
                by_name_override("type", {"displayName": "Fault"}),
                by_name_override("Value", {"displayName": "Seconds left", "unit": "s"}),
            ] + HIDE_INSTANCE_JOB,
        ), 12),
    ])

    return {
        "uid": "fuel-network", "title": "Fuel Network", "tags": ["fuel", "demo"],
        "timezone": "browser", "editable": True, "style": "dark", "refresh": "5s",
        "schemaVersion": 39, "version": 1, "time": {"from": "now-15m", "to": "now"},
        "panels": lay.panels, "links": _links("fuel-network"),
        "annotations": {"list": [_demo_annotation()]},
    }


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    for name, builder in (
        ("fuel-command.json", build_command_center),
        ("fuel-resilience.json", build_resilience_drill),
        ("fuel-platform.json", build_platform_health),
        ("fuel-network.json", build_fuel_network),
    ):
        _id_counter["n"] = 0
        dashboard = builder()
        path = os.path.join(OUT_DIR, name)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(dashboard, f, indent=2, sort_keys=False)
            f.write("\n")
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
