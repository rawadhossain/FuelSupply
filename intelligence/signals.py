"""Risk signals (REQ-018): statistical demand anomalies + state changes for every event type."""
from __future__ import annotations

from dataclasses import dataclass, field

from shared.snapshot import Snapshot

from .detect import CusumDetector


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
