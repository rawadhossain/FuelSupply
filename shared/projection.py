"""Deterministic two-echelon inventory roll-forward (validated: reproduces the simulator's
first-unmet tick for all 12 station x fuel series in the baseline export).

Semantics (from guide §5 + observed data):
  * offset k <-> tick snapshot.tick + k; demand at a tick is served as min(inventory, demand)
  * a new allocation created at offset k deducts depot stock at k, arrives at k + 1 + transit
  * supply arrivals land at their planned/actual tick, capped at depot capacity (excess = overflow)
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from . import schedule
from .snapshot import Snapshot


@dataclass(frozen=True)
class Move:
    route_id: str
    fuel_type: str
    quantity: float
    offset: int = 0            # created at snapshot.tick + offset


@dataclass
class Projection:
    H: int
    station_inv: dict = field(default_factory=dict)   # (sid,f) -> (H+1,) inventory before each tick, last = end
    served: dict = field(default_factory=dict)        # (sid,f) -> (H,)
    unmet: dict = field(default_factory=dict)
    depot_inv: dict = field(default_factory=dict)     # (did,f) -> (H+1,)
    overflow: dict = field(default_factory=dict)      # (did,f) -> (H,)

    def stockout_offset(self, key) -> int | None:
        idx = np.nonzero(self.unmet[key] > 1e-6)[0]
        return int(idx[0]) if len(idx) else None

    def total_unmet(self) -> float:
        return float(sum(v.sum() for v in self.unmet.values()))

    def total_overflow(self) -> float:
        return float(sum(v.sum() for v in self.overflow.values()))


def inbound_schedule(snap: Snapshot, H: int, moves=()) -> dict:
    """(sid,f) -> (H,) litres arriving at stations from existing + planned allocations."""
    inb: dict = {}
    for a in snap.allocations:
        if a.status not in ("PENDING", "IN_TRANSIT") or a.expected_arrival_tick is None:
            continue
        k = a.expected_arrival_tick - snap.tick
        if 0 <= k < H:
            inb.setdefault((a.destination_station_id, a.fuel_type), np.zeros(H))[k] += a.quantity
    for m in moves:
        r = snap.routes[m.route_id]
        k = m.offset + 1 + r.transit_ticks
        if k < H:
            inb.setdefault((r.destination_station_id, m.fuel_type), np.zeros(H))[k] += m.quantity
    return inb


def supply_schedule(snap: Snapshot, H: int) -> dict:
    sup: dict = {}
    for a in snap.arrivals:
        if a.status == "ARRIVED":
            continue
        k = a.planned_tick - snap.tick
        if 0 <= k < H:
            sup.setdefault((a.depot_id, a.fuel_type), np.zeros(H))[k] += a.quantity
    return sup


def project(snap: Snapshot, demand: dict, H: int, moves=()) -> Projection:
    """demand: (sid,f) -> (H,) expected demand (already includes multipliers)."""
    P = Projection(H)
    inb = inbound_schedule(snap, H, moves)
    for sid, st in snap.stations.items():
        open_ = schedule.station_open(snap, sid, H)
        for f in st.capacity:
            key = (sid, f)
            d = demand.get(key, np.zeros(H))
            inv = np.empty(H + 1); srv = np.empty(H); un = np.empty(H)
            x = st.inventory.get(f, 0.0)
            arr = inb.get(key, np.zeros(H))
            for k in range(H):
                inv[k] = x
                x = min(st.capacity[f], x + arr[k])
                s = min(x, d[k]) if open_[k] else 0.0
                srv[k], un[k] = s, d[k] - s
                x -= s
            inv[H] = x
            P.station_inv[key], P.served[key], P.unmet[key] = inv, srv, un
    sup = supply_schedule(snap, H)
    out: dict = {}
    for m in moves:
        r = snap.routes[m.route_id]
        if m.offset < H:
            out.setdefault((r.source_depot_id, m.fuel_type), np.zeros(H))[m.offset] += m.quantity
    for did, dp in snap.depots.items():
        for f in dp.capacity:
            key = (did, f)
            inv = np.empty(H + 1); ov = np.zeros(H)
            x = dp.inventory.get(f, 0.0)
            s_in, s_out = sup.get(key, np.zeros(H)), out.get(key, np.zeros(H))
            for k in range(H):
                inv[k] = x
                x += s_in[k]
                if x > dp.capacity[f]:
                    ov[k], x = x - dp.capacity[f], dp.capacity[f]
                x = max(0.0, x - s_out[k])
            inv[H] = x
            P.depot_inv[key], P.overflow[key] = inv, ov
    return P


def network_cover(snap: Snapshot, demand: dict, H: int) -> dict:
    """Per fuel: ticks of demand covered by all stock + in-transit + scheduled arrivals within H."""
    out = {}
    for f in snap.fuels:
        stock = sum(s.inventory.get(f, 0) for s in snap.stations.values()) + \
                sum(d.inventory.get(f, 0) for d in snap.depots.values()) + \
                sum(a.quantity for a in snap.allocations if a.fuel_type == f and a.status in ("PENDING", "IN_TRANSIT")) + \
                sum(a.quantity for a in snap.arrivals if a.fuel_type == f and a.status != "ARRIVED"
                    and a.planned_tick - snap.tick < H)
        cum = np.cumsum(sum(v for (s, ff), v in demand.items() if ff == f))
        covered = int(np.searchsorted(cum, stock, side="right"))
        out[f] = {"cover_ticks": covered if covered < H else None, "stock_l": round(stock, 1),
                  "demand_h_l": round(float(cum[-1]), 1), "systemic_shortage": covered < H}
    return out
