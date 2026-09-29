"""Fallback allocation policy (REQ-009a). Pure Python + numpy, no solver, no model files needed.
Core imports this directly when the Intelligence service is down.

Rule: for each station x fuel ordered by projected stockout, if it will run out within
(lead time + SAFETY) ticks, ship enough for TARGET_TICKS of expected demand from the open
route with the shortest transit that has stock, respecting every simulator validation check."""
from __future__ import annotations

import numpy as np

from . import schedule
from .projection import Move, project
from .snapshot import Snapshot

SAFETY_TICKS = 8
TARGET_TICKS = 96
MIN_QTY = 200.0


def dispatch_used(snap: Snapshot, did: str) -> float:
    return sum(a.quantity for a in snap.allocations
               if a.source_depot_id == did and a.status == "PENDING" and a.created_tick == snap.tick)


def plan(snap: Snapshot, demand: dict, H: int = 96) -> list[Move]:
    base = project(snap, demand, H)
    dep_left = {(d, f): dp.inventory.get(f, 0.0) for d, dp in snap.depots.items() for f in dp.capacity}
    disp_left = {d: dp.dispatch_capacity_per_tick - dispatch_used(snap, d) for d, dp in snap.depots.items()}
    inbound = {}
    for a in snap.allocations:
        if a.status in ("PENDING", "IN_TRANSIT"):
            k = (a.destination_station_id, a.fuel_type)
            inbound[k] = inbound.get(k, 0.0) + a.quantity
    order = sorted(base.unmet, key=lambda k: (base.stockout_offset(k) is None, base.stockout_offset(k) or 0))
    moves: list[Move] = []
    for sid, f in order:
        so = base.stockout_offset((sid, f))
        st = snap.stations[sid]
        if so is None or st.status != "OPEN":
            continue
        routes = sorted((r for r in snap.routes.values() if r.destination_station_id == sid
                         and schedule.route_open(snap, r.id, 1)[0] and schedule.depot_open(snap, r.source_depot_id, 1)[0]),
                        key=lambda r: (r.transit_ticks, -dep_left[(r.source_depot_id, f)]))
        for r in routes:
            if so > 1 + r.transit_ticks + SAFETY_TICKS:
                break                                   # not urgent yet via the fastest route
            need = float(demand[(sid, f)][:TARGET_TICKS].sum()) - st.inventory.get(f, 0) - inbound.get((sid, f), 0)
            headroom = st.capacity[f] - st.inventory.get(f, 0)
            q = min(need, headroom, r.max_shipment, dep_left[(r.source_depot_id, f)], disp_left[r.source_depot_id])
            q = float(np.floor(q / 10) * 10)
            if q < MIN_QTY:
                continue
            moves.append(Move(r.id, f, q, 0))
            dep_left[(r.source_depot_id, f)] -= q
            disp_left[r.source_depot_id] -= q
            inbound[(sid, f)] = inbound.get((sid, f), 0) + q
            break
    return moves
