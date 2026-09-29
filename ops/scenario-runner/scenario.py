#!/usr/bin/env python3
"""Demo scenario runner. stdlib only. Run from host:
  python ops/scenario-runner/scenario.py <name> [args]
  python ops/scenario-runner/scenario.py list
"""
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

SIM = os.environ.get("SIMULATOR_BASE_URL_HOST", f"http://localhost:{os.environ.get('SIMULATOR_PORT', '8000')}")
PROM = f"http://localhost:{os.environ.get('PROMETHEUS_PORT', '9090')}"
GRAFANA = f"http://localhost:{os.environ.get('GRAFANA_PORT', '3000')}"
GRAFANA_USER = "admin"
GRAFANA_PASS = os.environ.get("GRAFANA_ADMIN_PASSWORD", "admin")
EVIDENCE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "docs", "evidence")
DRILLS_FILE = os.path.join(EVIDENCE_DIR, "drills.md")


def _load_dotenv():
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


_load_dotenv()
GRAFANA_PASS = os.environ.get("GRAFANA_ADMIN_PASSWORD", GRAFANA_PASS)


def http(method, url, body=None, headers=None, timeout=10):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, raw.decode(errors="replace")


def sim_get(path):
    status, body = http("GET", f"{SIM}{path}")
    if status >= 400:
        raise RuntimeError(f"GET {path} -> {status}: {body}")
    return body


def sim_post(path, body=None):
    status, resp = http("POST", f"{SIM}{path}", body or {})
    if status >= 400:
        raise RuntimeError(f"POST {path} -> {status}: {resp}")
    return resp


def annotate(text, tags=None):
    tags = tags or []
    if "demo" not in tags:
        tags = ["demo"] + tags
    import base64
    auth = base64.b64encode(f"{GRAFANA_USER}:{GRAFANA_PASS}".encode()).decode()
    status, resp = http(
        "POST", f"{GRAFANA}/api/annotations",
        {"text": text, "tags": tags},
        headers={"Authorization": f"Basic {auth}"},
    )
    if status >= 300:
        print(f"  [warn] annotation failed ({status}): {resp}")
    else:
        print(f"  [annotation] {text}")
    return status < 300


def current_tick():
    return sim_get("/v1/instance")["tick"]


def tick_seconds():
    """Approx wall-clock seconds per tick, from SIMULATION_SPEED (ticks/sec) or default 1."""
    try:
        speed = float(os.environ.get("SIMULATION_SPEED", "1"))
        return 1.0 / speed if speed > 0 else 1.0
    except ValueError:
        return 1.0


def ticks_for_minutes(minutes):
    secs = minutes * 60.0
    tps = tick_seconds()
    return max(1, int(secs / tps))


def region_ids():
    return [r["id"] for r in sim_get("/v1/regions")]


def station_ids(region=None):
    stations = sim_get("/v1/stations")
    if region:
        stations = [s for s in stations if s["region_id"] == region]
    return [s["id"] for s in stations]


def route_ids():
    return [r["id"] for r in sim_get("/v1/routes")]


def depot_ids():
    return [d["id"] for d in sim_get("/v1/depots")]


def poll_alert(alert_name, want_firing, timeout=90, interval=2):
    """Poll Prometheus for alert_name to reach the desired firing state. Returns elapsed seconds or None on timeout."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            status, body = http("GET", f"{PROM}/api/v1/alerts")
            if status == 200:
                alerts = body.get("data", {}).get("alerts", [])
                firing = any(a["labels"].get("alertname") == alert_name and a["state"] == "firing" for a in alerts)
                if firing == want_firing:
                    return round(time.time() - start, 1)
        except Exception as exc:
            print(f"  [warn] prometheus poll failed: {exc}")
        time.sleep(interval)
    return None


def record_drill(scenario, alert, detection_s, recovery_s):
    os.makedirs(EVIDENCE_DIR, exist_ok=True)
    header = "| timestamp | scenario | alert | detection_s | recovery_s |\n|---|---|---|---|---|\n"
    if not os.path.exists(DRILLS_FILE):
        with open(DRILLS_FILE, "w", encoding="utf-8") as f:
            f.write("# Resilience drills\n\n" + header)
    ts = time.strftime("%Y-%m-%dT%H:%M:%S%z") or time.strftime("%Y-%m-%dT%H:%M:%S")
    row = f"| {ts} | {scenario} | {alert} | {detection_s if detection_s is not None else 'TIMEOUT'} | {recovery_s if recovery_s is not None else 'TIMEOUT'} |\n"
    with open(DRILLS_FILE, "a", encoding="utf-8") as f:
        f.write(row)
    print(f"  [drill] {row.strip()}")


def compose(*args):
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    cmd = ["docker", "compose"] + list(args)
    print(f"  $ {' '.join(cmd)}")
    subprocess.run(cmd, cwd=root, check=False)


# ---------------- scenarios ----------------

def cmd_run(args):
    sim_post("/admin/run")
    print("Simulator running.")


def cmd_pause(args):
    sim_post("/admin/pause")
    print("Simulator paused.")


def cmd_reset(args):
    sim_post("/admin/reset")
    print("Simulator reset. WARNING: teammate's DB mirror (core/postgres) must resync from this reset.")


def cmd_demo_reset(args):
    cmd_reset(args)
    sim_post("/admin/faults/clear")
    sim_post("/admin/run")
    print("demo-reset complete: reset, faults cleared, running.")


def _inject_event(event_type, parameters, minutes, label):
    tick = current_tick()
    duration = ticks_for_minutes(minutes)
    annotate(label, tags=[event_type])
    resp = sim_post("/admin/events", {
        "type": event_type,
        "start_tick": tick,
        "duration_ticks": duration,
        "parameters": parameters,
    })
    print(f"  event id={resp.get('id')} start_tick={tick} end_tick={tick + duration} (~{minutes:.1f} min)")
    return resp


def cmd_demand_spike(args):
    regs = region_ids()
    targets = regs[:2] if len(regs) >= 2 else regs[:1]
    _inject_event("demand_spike", {"multiplier": 2.0, "region_ids": targets}, 2.5,
                  f"Demand spike injected: {'+'.join(targets)} x2.0")


def cmd_route_down(args):
    routes = route_ids()
    targets = routes[:1]
    _inject_event("route_disruption", {"route_ids": targets}, 2.5, f"Route disruption injected: {'+'.join(targets)}")


def cmd_station_outage(args):
    stations = station_ids()
    targets = stations[:1]
    _inject_event("station_outage", {"station_ids": targets}, 2.5, f"Station outage injected: {'+'.join(targets)}")


def cmd_depot_constraint(args):
    depots = depot_ids()
    targets = depots[:1]
    _inject_event("depot_constraint", {"depot_ids": targets}, 2.5, f"Depot constraint injected: {'+'.join(targets)}")


def cmd_shipment_delay(args):
    depots = depot_ids()
    targets = depots[:1]
    _inject_event("shipment_delay", {"delay_ticks": 4, "depot_ids": targets}, 2.5,
                  f"Shipment delay injected: {'+'.join(targets)} +4 ticks")


def cmd_supply_shortfall(args):
    depots = depot_ids()
    targets = depots[:1]
    _inject_event("supply_shortfall", {"factor": 0.5, "depot_ids": targets}, 2.5,
                  f"Supply shortfall injected: {'+'.join(targets)} x0.5")


def cmd_combined(args):
    regs = region_ids()
    routes = route_ids()
    depots = depot_ids()
    annotate("Combined crisis injected: demand spike + route down + depot constraint", tags=["combined"])
    tick = current_tick()
    duration = ticks_for_minutes(2.5)
    for etype, params in [
        ("demand_spike", {"multiplier": 2.0, "region_ids": regs[:1]}),
        ("route_disruption", {"route_ids": routes[:1]}),
        ("depot_constraint", {"depot_ids": depots[:1]}),
    ]:
        resp = sim_post("/admin/events", {"type": etype, "start_tick": tick, "duration_ticks": duration, "parameters": params})
        print(f"  {etype} id={resp.get('id')}")
    print(f"  combined events end at tick {tick + duration}")


def _wait_for_resolve(minutes, label):
    time.sleep(minutes * 60)
    annotate(f"{label} resolved", tags=["resolved"])


def _fault(fault_type, seconds, label):
    annotate(f"{label} injected ({seconds}s)", tags=[fault_type])
    resp = sim_post("/admin/faults", {"type": fault_type, "duration_seconds": seconds, "parameters": {}})
    print(f"  fault id={resp.get('id')} duration={seconds}s")
    return resp


def cmd_sim_latency(args):
    seconds = _seconds_arg(args, 60)
    _fault("latency", seconds, "Simulator latency fault")


def cmd_sim_unavailable(args):
    seconds = _seconds_arg(args, 60)
    _fault("unavailable", seconds, "Simulator unavailable fault")


def cmd_sim_error_rate(args):
    seconds = _seconds_arg(args, 60)
    _fault("error_rate", seconds, "Simulator error_rate fault")


def cmd_sim_stale(args):
    seconds = _seconds_arg(args, 60)
    _fault("stale_data", seconds, "Simulator stale_data fault")


def cmd_stream_disconnect(args):
    seconds = _seconds_arg(args, 60)
    _fault("stream_disconnect", seconds, "Simulator stream_disconnect fault")


def cmd_clear(args):
    sim_post("/admin/faults/clear")
    annotate("Faults cleared", tags=["clear"])
    print("Faults cleared.")


def _seconds_arg(args, default):
    if "--seconds" in args:
        i = args.index("--seconds")
        if i + 1 < len(args):
            return int(args[i + 1])
    return default


ALERT_FOR_FAULT = {
    "unavailable": "SimulatorAPIUnavailable",
    "latency": "SimulatorSlow",
    "error_rate": "HighErrorRate",
    "stale_data": "SimulatorStaleData",
}
ALERT_FOR_KILL = {
    "core": "ServiceDown",
    "intelligence": "ServiceDown",
}


def cmd_kill(args):
    if not args:
        print("usage: kill <core|intelligence|redis|postgres|frontend>")
        return
    service = args[0]
    annotate(f"Killing {service}", tags=["kill", service])
    compose("stop", service)
    alert = ALERT_FOR_KILL.get(service, "ServiceDown")
    print(f"  waiting for {alert} to fire (timeout 90s)...")
    detection = poll_alert(alert, True)
    print(f"  detection: {detection if detection is not None else 'TIMEOUT'}s")
    print(f"  now run: python ops/scenario-runner/scenario.py start {service}")
    record_drill(f"kill {service}", alert, detection, None)


def cmd_start(args):
    if not args:
        print("usage: start <core|intelligence|redis|postgres|frontend>")
        return
    service = args[0]
    compose("start", service)
    annotate(f"Started {service}", tags=["start", service])
    alert = ALERT_FOR_KILL.get(service, "ServiceDown")
    print(f"  waiting for {alert} to clear (timeout 90s)...")
    recovery = poll_alert(alert, False)
    print(f"  recovery: {recovery if recovery is not None else 'TIMEOUT'}s")
    record_drill(f"start {service}", alert, None, recovery)


def _sim_fault_drill(fault_type, args, label):
    alert = ALERT_FOR_FAULT.get(fault_type)
    seconds = _seconds_arg(args, 90)
    _fault(fault_type, seconds, label)
    detection = poll_alert(alert, True) if alert else None
    print(f"  detection: {detection if detection is not None else 'TIMEOUT'}s")
    sim_post("/admin/faults/clear")
    annotate(f"{label} cleared", tags=["clear", fault_type])
    recovery = poll_alert(alert, False) if alert else None
    print(f"  recovery: {recovery if recovery is not None else 'TIMEOUT'}s")
    record_drill(fault_type, alert or "n/a", detection, recovery)


def cmd_sim_unavailable_drill(args):
    _sim_fault_drill("unavailable", args, "Simulator unavailable fault")


CHAOS_CONTROL = f"http://localhost:{os.environ.get('CHAOS_PROXY_CONTROL_PORT', '8001')}"


def _chaos_set(mode):
    status, resp = http("POST", f"{CHAOS_CONTROL}/__chaos", {"mode": mode})
    return status, resp


def _chaos_in_path():
    status, _ = http("GET", "http://localhost:8000/__chaos_probe", timeout=2) if False else (None, None)
    # Determine by checking core's configured SIMULATOR_BASE_URL via docker inspect.
    try:
        out = subprocess.run(
            ["docker", "compose", "exec", "-T", "core", "printenv", "SIMULATOR_BASE_URL"],
            cwd=os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            capture_output=True, text=True, timeout=10,
        )
        return "chaos-proxy" in out.stdout
    except Exception:
        return False


def _set_core_simulator_url(url):
    env = dict(os.environ)
    env["SIMULATOR_BASE_URL"] = url
    compose_up_env(env)


def compose_up_env(env):
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    cmd = ["docker", "compose", "up", "-d", "--no-deps", "core"]
    print(f"  $ SIMULATOR_BASE_URL={env.get('SIMULATOR_BASE_URL')} {' '.join(cmd)}")
    subprocess.run(cmd, cwd=root, env=env, check=False)


def cmd_bad_payload(args):
    if not args:
        print("usage: bad-payload <corrupt_json|wrong_schema|slow|error500|off>")
        return
    mode = args[0]
    mode_map = {"off": "passthrough"}
    chaos_mode = mode_map.get(mode, mode)
    if chaos_mode != "passthrough" and not _chaos_in_path():
        print("  chaos-proxy not in path — routing core through chaos-proxy")
        _set_core_simulator_url("http://chaos-proxy:8000")
    status, resp = _chaos_set(chaos_mode)
    annotate(f"Chaos proxy mode: {mode}", tags=["chaos", mode])
    print(f"  chaos-proxy mode -> {mode} ({status}: {resp})")
    if mode == "off":
        _set_core_simulator_url(os.environ.get("SIMULATOR_BASE_URL", "http://simulator-api:8000"))


def cmd_dispatch(args):
    from urllib.request import Request
    minutes = 5
    if "--minutes" in args:
        i = args.index("--minutes")
        if i + 1 < len(args):
            minutes = float(args[i + 1])
    deadline = time.time() + minutes * 60
    print(f"baseline-dispatch running for {minutes} min (Ctrl+C to stop)...")
    try:
        while time.time() < deadline:
            _dispatch_once()
            time.sleep(15)
    except KeyboardInterrupt:
        print("stopped.")


def _dispatch_once():
    stations = sim_get("/v1/stations")
    depots = sim_get("/v1/depots")
    routes = [r for r in sim_get("/v1/routes") if r["status"] == "AVAILABLE"]
    candidates = []
    for s in stations:
        if s["status"] != "OPEN":
            continue
        for fuel, cap in s["capacity"].items():
            if cap <= 0:
                continue
            ratio = s["inventory"].get(fuel, 0) / cap
            candidates.append((ratio, s["id"], fuel))
    if not candidates:
        print("  no candidate stations")
        return
    candidates.sort(key=lambda c: c[0])
    ratio, station_id, fuel = candidates[0]
    route = next((r for r in routes if r["destination_station_id"] == station_id), None)
    if not route:
        print(f"  no available route to {station_id}, skipping")
        return
    depot = next((d for d in depots if d["id"] == route["source_depot_id"] and d["inventory"].get(fuel, 0) > 0), None)
    if not depot:
        print(f"  no depot with {fuel} for route {route['id']}, skipping")
        return
    quantity = min(route["max_shipment"], depot["inventory"][fuel] * 0.2)
    if quantity <= 0:
        print(f"  zero quantity available, skipping")
        return
    tick = current_tick()
    key = f"baseline-{station_id}-{fuel}-{tick}"
    body = {
        "idempotency_key": key,
        "source_depot_id": depot["id"],
        "destination_station_id": station_id,
        "route_id": route["id"],
        "fuel_type": fuel,
        "quantity": round(quantity, 1),
    }
    status, resp = http("POST", f"{SIM}/v1/allocations", body)
    print(f"  dispatch {station_id} {fuel} ratio={ratio:.2f} qty={body['quantity']} -> {status}")


SCENARIOS = {
    "run": (cmd_run, "start the simulator"),
    "pause": (cmd_pause, "pause the simulator"),
    "reset": (cmd_reset, "reset the simulator (warns about DB mirror resync)"),
    "demo-reset": (cmd_demo_reset, "reset + clear faults + run"),
    "demand-spike": (cmd_demand_spike, "inject a demand_spike x2.0 on 1-2 regions (~2.5min)"),
    "route-down": (cmd_route_down, "inject a route_disruption (~2.5min)"),
    "station-outage": (cmd_station_outage, "inject a station_outage (~2.5min)"),
    "depot-constraint": (cmd_depot_constraint, "inject a depot_constraint (~2.5min)"),
    "shipment-delay": (cmd_shipment_delay, "inject a shipment_delay +4 ticks"),
    "supply-shortfall": (cmd_supply_shortfall, "inject a supply_shortfall x0.5"),
    "combined": (cmd_combined, "demand-spike + route-down + depot-constraint together"),
    "sim-latency": (cmd_sim_latency, "inject latency fault [--seconds N, default 60]"),
    "sim-unavailable": (cmd_sim_unavailable, "inject unavailable fault [--seconds N]; use drill 'kill' timing style via sim-unavailable-drill"),
    "sim-unavailable-drill": (cmd_sim_unavailable_drill, "unavailable fault with detection/recovery timing"),
    "sim-error-rate": (cmd_sim_error_rate, "inject error_rate fault [--seconds N]"),
    "sim-stale": (cmd_sim_stale, "inject stale_data fault [--seconds N]"),
    "stream-disconnect": (cmd_stream_disconnect, "inject stream_disconnect fault [--seconds N, default 60]"),
    "clear": (cmd_clear, "clear all active faults"),
    "kill": (cmd_kill, "kill <core|intelligence|redis|postgres|frontend>, times detection"),
    "start": (cmd_start, "start <service>, times recovery"),
    "bad-payload": (cmd_bad_payload, "bad-payload <corrupt_json|wrong_schema|slow|error500|off>"),
    "dispatch": (cmd_dispatch, "alias for baseline-dispatch"),
    "baseline-dispatch": (cmd_dispatch, "TEMP stand-in dispatcher [--minutes N, default 5]"),
}


def cmd_list(args):
    print("Available scenarios:")
    for name, (_, desc) in sorted(SCENARIOS.items()):
        print(f"  {name:<22} {desc}")


def main():
    if len(sys.argv) < 2 or sys.argv[1] == "list":
        cmd_list(sys.argv[2:])
        return
    name = sys.argv[1]
    args = sys.argv[2:]
    if name not in SCENARIOS:
        print(f"unknown scenario: {name}")
        cmd_list([])
        sys.exit(1)
    fn, _ = SCENARIOS[name]
    fn(args)


if __name__ == "__main__":
    main()
