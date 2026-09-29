"""Risk signals (REQ-018): statistical demand anomalies + state changes for every event type."""
from __future__ import annotations

from dataclasses import dataclass, field

from .detect import CusumDetector
from .snapshot import Snapshot


@dataclass
class SignalTracker:
    detector: CusumDetector
    first_seen_arrival: dict = field(default_factory=dict)   # id -> (planned_tick, quantity)
    last_tick_fed: int = -1

    def feed_demand(self, rows: list[dict], expected_fn) -> list[dict]:
        """rows: demand-history rows (any order). expected_fn(station, fuel, tick) -> expected litres
        including the multiplier that was in force. Only ticks not yet fed are processed."""
        out = []
        for r in sorted(rows, key=lambda r: (r["tick"], r["station_id"], r["fuel_type"])):
            if r["tick"] <= self.last_tick_fed:
                continue
            sig = self.detector.update(r["station_id"], r["fuel_type"], r["tick"], r["demand_liters"],
                                       expected_fn(r["station_id"], r["fuel_type"], r["tick"]))
            if sig:
                out.append(sig)
        if rows:
            self.last_tick_fed = max(self.last_tick_fed, max(r["tick"] for r in rows))
        return out

    def state_signals(self, snap: Snapshot) -> list[dict]:
        sig = []
        for s in snap.stations.values():
            if s.status != "OPEN":
                sig.append({"type": "station_outage", "entity_id": s.id, "severity": "high"})
        for r in snap.routes.values():
            if r.status != "AVAILABLE":
                sig.append({"type": "route_disrupted", "entity_id": r.id, "severity": "high"})
        for d in snap.depots.values():
            if d.status == "CONSTRAINED":
                sig.append({"type": "depot_constrained", "entity_id": d.id, "severity": "medium"})
            elif d.status != "OPEN":
                sig.append({"type": "depot_closed", "entity_id": d.id, "severity": "high"})
        for a in snap.arrivals:
            first = self.first_seen_arrival.setdefault(a.id, (a.planned_tick, a.quantity))
            if a.status == "ARRIVED":
                continue
            if a.status == "DELAYED" or a.planned_tick > first[0]:
                sig.append({"type": "supply_delayed", "entity_id": a.id, "severity": "medium",
                            "evidence": {"depot_id": a.depot_id, "fuel_type": a.fuel_type,
                                         "planned_tick": a.planned_tick, "originally": first[0]}})
            if a.quantity < first[1] - 1e-6:
                sig.append({"type": "supply_shortfall", "entity_id": a.id, "severity": "medium",
                            "evidence": {"depot_id": a.depot_id, "fuel_type": a.fuel_type,
                                         "quantity": a.quantity, "originally": first[1]}})
        for e in snap.events:
            if e.status in ("ACTIVE", "SCHEDULED"):
                sig.append({"type": f"event_{e.status.lower()}", "entity_id": str(e.id), "severity":
                            "high" if e.status == "ACTIVE" else "low",
                            "evidence": {"event_type": e.type, "start_tick": e.start_tick,
                                         "end_tick": e.end_tick, "parameters": e.parameters}})
        if snap.stale:
            sig.append({"type": "data_stale", "entity_id": "simulator", "severity": "medium"})
        return sig


# --------------------------------------------------------------------------- inventory reconciliation
INV_TOLERANCE_L = 25.0


def reconcile_inventory(prev: Snapshot, now: Snapshot, served: dict) -> list[dict]:
    """Abnormal inventory changes: compare each station/depot stock change between two consecutive
    snapshots with what is explained by sales, truck arrivals, supply arrivals and dispatches.
    served: (station_id, fuel) -> litres served at tick prev.tick (from /v1/demand-history).
    Depot dispatch is checked under both plausible accounting rules (deducted at creation or at
    departure) and the smaller residual is used, so a bookkeeping convention never raises an alarm."""
    if now.tick != prev.tick + 1:
        return []
    T, out = prev.tick, []
    for sid, st in now.stations.items():
        if sid not in prev.stations:
            continue
        p = prev.stations[sid]
        for f, inv in st.inventory.items():
            if (sid, f) not in served:
                continue
            arrived = sum(a.quantity for a in prev.allocations if a.destination_station_id == sid and a.fuel_type == f
                          and a.status in ("PENDING", "IN_TRANSIT") and a.expected_arrival_tick == T)
            expected = min(p.capacity.get(f, float("inf")), p.inventory.get(f, 0) + arrived) - served[(sid, f)]
            resid = inv - expected
            if abs(resid) > max(INV_TOLERANCE_L, 0.002 * p.capacity.get(f, 0)):
                out.append({"type": "inventory_anomaly", "entity_id": f"{sid}|{f}", "severity":
                            "high" if abs(resid) > 500 else "medium",
                            "evidence": {"tick": T, "actual_l": round(inv, 1), "expected_l": round(expected, 1),
                                         "unexplained_l": round(resid, 1)}})
    for did, dp in now.depots.items():
        if did not in prev.depots:
            continue
        p = prev.depots[did]
        for f, inv in dp.inventory.items():
            supply = sum(a.quantity for a in prev.arrivals if a.depot_id == did and a.fuel_type == f
                         and a.status != "ARRIVED" and a.planned_tick == T)
            base = min(p.capacity.get(f, float("inf")), p.inventory.get(f, 0) + supply)
            created = sum(a.quantity for a in now.allocations if a.source_depot_id == did and a.fuel_type == f
                          and a.created_tick == T and a.status != "CANCELLED")
            departed = sum(a.quantity for a in now.allocations if a.source_depot_id == did and a.fuel_type == f
                           and a.created_tick == T - 1 and a.status not in ("CANCELLED",))
            resid = min((inv - (base - created), inv - (base - departed)), key=abs)
            if abs(resid) > max(INV_TOLERANCE_L, 0.002 * p.capacity.get(f, 0)):
                out.append({"type": "inventory_anomaly", "entity_id": f"{did}|{f}", "severity":
                            "high" if abs(resid) > 500 else "medium",
                            "evidence": {"tick": T, "actual_l": round(inv, 1), "unexplained_l": round(resid, 1)}})
    return out


# ------------------------------------------------------------------------ transport / supply outlook
def allocations_at_risk(snap: Snapshot) -> list[dict]:
    """Transport-failure prediction: a PENDING allocation departs at created_tick + 1; if its route is
    (or will be) DISRUPTED then, the simulator marks it FAILED. Warn while it can still be cancelled."""
    from . import schedule
    out = []
    for a in snap.allocations:
        if a.status != "PENDING" or a.route_id not in snap.routes:
            continue
        k = a.created_tick + 1 - snap.tick
        ok = schedule.route_open(snap, a.route_id, max(k + 1, 1))
        if not ok[max(k, 0)] or snap.stations[a.destination_station_id].status != "OPEN":
            out.append({"type": "allocation_at_risk", "entity_id": str(a.id), "severity": "high",
                        "evidence": {"route_id": a.route_id, "departure_tick": a.created_tick + 1,
                                     "quantity": a.quantity, "fuel_type": a.fuel_type},
                        "advice": "route/station unavailable at departure: cancel (PENDING) and re-route"})
    return out


@dataclass
class SupplyHistory:
    """Learns how late supply arrivals are per depot, from arrivals seen through to ARRIVED."""
    first_planned: dict = field(default_factory=dict)   # arrival id -> first planned tick seen
    delays: dict = field(default_factory=dict)          # depot id -> list of delays (ticks)
    done: set = field(default_factory=set)

    def update(self, snap: Snapshot) -> None:
        for a in snap.arrivals:
            self.first_planned.setdefault(a.id, a.planned_tick)
            if a.status == "ARRIVED" and a.id not in self.done:
                self.done.add(a.id)
                self.delays.setdefault(a.depot_id, []).append(max(0, a.planned_tick - self.first_planned[a.id]))

    def outlook(self, snap: Snapshot, tick_minutes: int = 15) -> list[dict]:
        rows = []
        for a in sorted(snap.arrivals, key=lambda x: x.planned_tick):
            if a.status == "ARRIVED":
                continue
            hist = self.delays.get(a.depot_id, [])
            mean_delay = sum(hist) / len(hist) if hist else 0.0
            eta_tick = a.planned_tick + mean_delay
            rows.append({"id": a.id, "depot_id": a.depot_id, "fuel_type": a.fuel_type, "quantity": a.quantity,
                         "status": a.status, "planned_tick": a.planned_tick,
                         "delayed_by_ticks": a.planned_tick - self.first_planned.get(a.id, a.planned_tick),
                         "eta_tick": round(eta_tick, 1),
                         "eta_hours": round((eta_tick - snap.tick) * tick_minutes / 60, 2),
                         "basis": f"planned tick + mean observed delay at {a.depot_id} "
                                  f"({mean_delay:.1f} ticks over {len(hist)} arrivals)" if hist
                                  else "planned tick (no delivery history yet)",
                         "on_time_rate": round(sum(1 for d in hist if d == 0) / len(hist), 3) if hist else None})
        return rows
