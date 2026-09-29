"""Offline closed-loop replay: runs a policy tick-by-tick against the *recorded* simulator demand
(dataset/ml/demand_full_features.csv) using the projection semantics that reproduce the simulator's
stockout ticks exactly. Demand does not depend on our actions, so replaying real demand is faithful
for the baseline scenario (no events). The live benchmark (benchmark.py) is the authoritative check.

    python -m intelligence.replay --ticks 672
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import time

import numpy as np
import pandas as pd

from shared.projection import Move
from shared.snapshot import Allocation, Snapshot

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Scenario initial state (SPEC.md §4.1 / guide §8) — the export only has end-of-run inventories.
INIT_STATION = {"station-mirpur": (9000, 9000, 5000), "station-tongi": (11000, 6000, 3500),
                "station-karnaphuli": (8500, 9500, 5200), "station-coxsbazar": (7500, 7500, 4200)}
INIT_DEPOT = {"depot-gazipur": (60000, 45000, 26000), "depot-patiya": (55000, 42000, 24000)}
FUELS = ("DIESEL", "PETROL", "OCTANE")


def initial_snapshot() -> Snapshot:
    raw = lambda n: json.load(open(os.path.join(ROOT, "dataset", "data", n)))
    stations, depots = raw("stations.json"), raw("depots.json")
    for s in stations:
        s["inventory"] = dict(zip(FUELS, INIT_STATION[s["id"]])); s["status"] = "OPEN"; s["demand_multiplier"] = 1.0
    for d in depots:
        d["inventory"] = dict(zip(FUELS, INIT_DEPOT[d["id"]])); d["status"] = "OPEN"
    arr = raw("supply_arrivals.json")
    for a in arr:
        a["status"], a["actual_tick"] = "SCHEDULED", None
    return Snapshot.from_api({"instance": {"tick": 0}, "stations": stations, "depots": depots,
                              "routes": raw("routes.json"), "supply-arrivals": arr, "allocations": [],
                              "events": [], "regions": raw("regions.json")})


# Same scripted crisis as dataset/export_crisis_dataset.py (+ a supply-side shock).
# Effects follow guide §7.8: spike multiplies demand_multiplier; outage -> station OUTAGE (served 0);
# route_disruption -> DISRUPTED; shipment_delay shifts planned_tick. OFFLINE ESTIMATE ONLY.
CRISIS = [
    ("demand_spike", 100, 48, {"region_ids": ["region-dhaka"], "multiplier": 1.8}),
    ("route_disruption", 60, 60, {"route_ids": ["route-gazipur-tongi"]}),
    ("demand_spike", 250, 24, {"station_ids": ["station-karnaphuli"], "multiplier": 2.5}),
    ("station_outage", 350, 32, {"station_ids": ["station-coxsbazar"]}),
    ("demand_spike", 480, 48, {"multiplier": 1.4}),
    ("route_disruption", 480, 48, {"route_ids": ["route-gazipur-mirpur", "route-patiya-karnaphuli"]}),
    ("shipment_delay", 10, 1, {"depot_ids": ["depot-gazipur"], "delay_ticks": 40}),
]


class ReplaySim:
    def __init__(self, demand: pd.DataFrame, crisis: bool = False):
        self.snap = initial_snapshot()
        from shared.snapshot import Event
        if crisis:
            self.snap.events = [Event(i + 1, t, st, st + dur, "SCHEDULED", p) for i, (t, st, dur, p) in enumerate(CRISIS)]
        self.dem = {(r.station_id, r.fuel_type, r.tick): r.demand_liters for r in demand.itertuples()}
        self.served = self.unmet = self.overflow = 0.0
        self.stockout_ticks = 0          # station x fuel x ticks with unmet demand
        self.rejected: dict[str, int] = {}
        self.next_id = 1

    def submit(self, m: Move) -> bool:
        """Mirror the simulator's validation order for the checks we can evaluate."""
        s = self.snap; r = s.routes[m.route_id]; st = s.stations[r.destination_station_id]; dp = s.depots[r.source_depot_id]
        used = sum(a.quantity for a in s.allocations if a.source_depot_id == dp.id and a.created_tick == s.tick)
        checks = [("STATION_CLOSED", st.status != "OPEN"), ("ROUTE_DISRUPTED", r.status != "AVAILABLE"),
                  ("ROUTE_CAPACITY_EXCEEDED", m.quantity > r.max_shipment),
                  ("INSUFFICIENT_INVENTORY", m.quantity > dp.inventory[m.fuel_type] + 1e-6),
                  ("DISPATCH_CAPACITY_EXCEEDED", used + m.quantity > dp.dispatch_capacity_per_tick + 1e-6),
                  ("DESTINATION_CAPACITY_EXCEEDED", st.inventory[m.fuel_type] + m.quantity > st.capacity[m.fuel_type] + 1e-6)]
        for code, bad in checks:
            if bad:
                self.rejected[code] = self.rejected.get(code, 0) + 1
                return False
        dp.inventory[m.fuel_type] -= m.quantity
        s.allocations.append(Allocation(self.next_id, r.id, dp.id, st.id, m.fuel_type, m.quantity, s.tick,
                                        s.tick + 1 + r.transit_ticks, "PENDING"))
        self.next_id += 1
        return True

    def _events(self, t: int, phase: str):
        """Simulator behaviour verified on real crisis data: an event is active for ticks
        start..end inclusive -> activate before tick `start` is processed, resolve after tick `end`."""
        s = self.snap
        def stations_of(p):
            sids, rids = p.get("station_ids") or [], p.get("region_ids") or []
            return [x for x in s.stations if (not sids and not rids) or x in sids or s.stations[x].region_id in rids]
        for e in s.events:
            p = e.parameters
            if phase == "start" and e.status == "SCHEDULED" and e.start_tick == t:
                e.status = "ACTIVE"
                if e.type == "demand_spike":
                    for x in stations_of(p): s.stations[x].demand_multiplier *= p.get("multiplier", 1.5)
                elif e.type == "station_outage":
                    for x in p.get("station_ids") or list(s.stations): s.stations[x].status = "OUTAGE"
                elif e.type == "route_disruption":
                    for x in p.get("route_ids") or list(s.routes): s.routes[x].status = "DISRUPTED"
                elif e.type == "shipment_delay":
                    for a in s.arrivals:
                        if a.status != "ARRIVED" and (not p.get("depot_ids") or a.depot_id in p["depot_ids"]):
                            a.planned_tick += p.get("delay_ticks", 2); a.status = "DELAYED"
                    e.status = "RESOLVED"
            elif phase == "end" and e.status == "ACTIVE" and e.end_tick == t:
                e.status = "RESOLVED"
                if e.type == "demand_spike":
                    for x in stations_of(p): s.stations[x].demand_multiplier /= max(p.get("multiplier", 1.5), 0.01)
                elif e.type == "station_outage":
                    for x in p.get("station_ids") or list(s.stations): s.stations[x].status = "OPEN"
                elif e.type == "route_disruption":
                    for x in p.get("route_ids") or list(s.routes): s.routes[x].status = "AVAILABLE"

    def step(self) -> list[dict]:
        s = self.snap; t = s.tick
        self._events(t, "start")
        for a in s.arrivals:                                  # depot supply, capped
            if a.status in ("SCHEDULED", "DELAYED") and a.planned_tick == t:
                dp = s.depots[a.depot_id]; x = dp.inventory[a.fuel_type] + a.quantity
                self.overflow += max(0.0, x - dp.capacity[a.fuel_type])
                dp.inventory[a.fuel_type] = min(x, dp.capacity[a.fuel_type]); a.status = "ARRIVED"
        for a in s.allocations:
            if a.status == "PENDING" and a.created_tick < t:
                a.status = "IN_TRANSIT"
            if a.status == "IN_TRANSIT" and a.expected_arrival_tick == t:
                st = s.stations[a.destination_station_id]
                st.inventory[a.fuel_type] = min(st.capacity[a.fuel_type], st.inventory[a.fuel_type] + a.quantity)
                a.status = "ARRIVED"
        rows = []
        for sid, st in s.stations.items():
            for f in FUELS:
                d = self.dem[(sid, f, t)] * st.demand_multiplier
                sv = min(st.inventory[f], d) if st.status == "OPEN" else 0.0
                st.inventory[f] -= sv; self.served += sv; self.unmet += d - sv
                self.stockout_ticks += (d - sv) > 1e-6
                rows.append({"station_id": sid, "fuel_type": f, "tick": t, "demand_liters": d,
                             "served_liters": sv, "unmet_liters": d - sv})
        s.allocations = [a for a in s.allocations if a.status != "ARRIVED"]
        self._events(t, "end")
        s.tick += 1
        return rows


def run(policy: str, ticks: int, demand: pd.DataFrame, crisis: bool = False) -> dict:
    from .assess import Assessor
    sim, ass = ReplaySim(demand, crisis), Assessor()
    rows, n_alloc, lat = [], 0, []
    for _ in range(ticks):
        if policy != "none":
            out = ass.assess(copy.deepcopy(sim.snap), rows, policy=policy)
            lat.append(out["latency_ms"])
            for rec in out["recommendations"]:
                a = rec["action"]
                n_alloc += sim.submit(Move(a["route_id"], rec["fuel_type"], a["quantity"], 0))
        rows = sim.step()
    tot = sim.served + sim.unmet
    return {"policy": policy, "scenario": "crisis" if crisis else "baseline", "ticks": ticks, "service_level": round(sim.served / tot, 4),
            "served_l": round(sim.served), "unmet_l": round(sim.unmet), "overflow_l": round(sim.overflow),
            "allocations": n_alloc, "rejected": sim.rejected,
            "stockout_series_hours": round(sim.stockout_ticks * 0.25, 1),
            "total_supply_l": 216000 + sum(sum(v) for v in INIT_DEPOT.values()) + sum(sum(v) for v in INIT_STATION.values()),
            "assess_p50_ms": round(float(np.median(lat)), 1) if lat else None,
            "assess_p95_ms": round(float(np.percentile(lat, 95)), 1) if lat else None}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticks", type=int, default=672)
    ap.add_argument("--policies", default="none,heuristic,lp")
    ap.add_argument("--crisis", action="store_true", help="apply the scripted crisis events")
    a = ap.parse_args()
    demand = pd.read_csv(os.path.join(ROOT, "dataset", "ml", "demand_full_features.csv"))
    res = []
    for p in a.policies.split(","):
        t0 = time.time(); r = run(p, a.ticks, demand, a.crisis); r["wall_s"] = round(time.time() - t0, 1); res.append(r)
        print(json.dumps(r))
    out = os.path.join(ROOT, "intelligence", "artifacts",
                       f"replay_{'crisis' if a.crisis else 'baseline'}_{a.ticks}.json")
    json.dump(res, open(out, "w"), indent=2)
    print("written", out)


if __name__ == "__main__":
    main()
