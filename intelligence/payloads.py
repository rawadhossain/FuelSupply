"""Build /intel/assess request bodies from the replay simulator (same JSON shape as the real /v1/*).

    python -m intelligence.payloads            # writes loadtest/sample_assess_request.json (crisis, tick 121)
Used by service tests, the Locust load test, and as a mock for the Core / frontend teams.
"""
from __future__ import annotations

import copy
import json
import os

import pandas as pd

from .projection import Move
from .replay import ROOT, ReplaySim


def sim_json(sim: ReplaySim) -> dict:
    s = sim.snap
    return {
        "instance": {"id": 1, "tick": s.tick, "status": "PAUSED"},
        "regions": [{"id": k, "name": k, "demand_factor": v} for k, v in s.region_factor.items()],
        "stations": [{"id": x.id, "name": x.id, "region_id": x.region_id, "status": x.status,
                      "demand_multiplier": x.demand_multiplier, "capacity": dict(x.capacity),
                      "inventory": {k: round(v, 3) for k, v in x.inventory.items()}} for x in s.stations.values()],
        "depots": [{"id": x.id, "name": x.id, "region_id": x.region_id, "status": x.status,
                    "dispatch_capacity_per_tick": x.dispatch_capacity_per_tick, "capacity": dict(x.capacity),
                    "inventory": {k: round(v, 3) for k, v in x.inventory.items()}} for x in s.depots.values()],
        "routes": [{"id": r.id, "source_depot_id": r.source_depot_id, "destination_station_id": r.destination_station_id,
                    "transit_ticks": r.transit_ticks, "max_shipment": r.max_shipment, "status": r.status}
                   for r in s.routes.values()],
        "supply-arrivals": [{"id": a.id, "depot_id": a.depot_id, "fuel_type": a.fuel_type, "quantity": a.quantity,
                             "planned_tick": a.planned_tick, "actual_tick": None, "status": a.status} for a in s.arrivals],
        "allocations": [{"id": a.id, "route_id": a.route_id, "source_depot_id": a.source_depot_id,
                         "destination_station_id": a.destination_station_id, "fuel_type": a.fuel_type,
                         "quantity": a.quantity, "created_tick": a.created_tick,
                         "expected_arrival_tick": a.expected_arrival_tick, "status": a.status} for a in s.allocations],
        "events": [{"id": e.id, "type": e.type, "start_tick": e.start_tick, "end_tick": e.end_tick,
                    "status": e.status, "parameters": e.parameters} for e in s.events],
    }


def crisis_request(target_tick: int = 121) -> dict:
    """Run the validated crisis replay with the heuristic up to `target_tick`; return a request body."""
    from .assess import Assessor
    dem = pd.read_csv(os.path.join(ROOT, "dataset", "ml", "demand_full_features.csv"))
    sim, ass, rows = ReplaySim(dem, crisis=True), Assessor(), []
    while sim.snap.tick < target_tick:
        out = ass.assess(copy.deepcopy(sim.snap), rows, policy="heuristic")
        for r in out["recommendations"]:
            a = r["action"]; sim.submit(Move(a["route_id"], r["fuel_type"], a["quantity"], 0))
        rows = sim.step()
    return {"snapshot": sim_json(sim), "demand_rows": [{k: (round(v, 3) if isinstance(v, float) else v)
                                                        for k, v in r.items()} for r in rows],
            "stale": False, "policy": "heuristic", "narrate": False}


if __name__ == "__main__":
    body = crisis_request()
    path = os.path.join(ROOT, "loadtest", "sample_assess_request.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    json.dump(body, open(path, "w"), indent=1)
    print("written", path, "tick", body["snapshot"]["instance"]["tick"])
