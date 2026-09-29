"""Naive fallback allocation policy — used ONLY when the Intelligence service
itself is unreachable (REQ-009a: "ML model unavailable -> fallback allocation
policy"). Not tuned, not benchmarked: nearest eligible depot with enough
inventory, first route that fits. Every recommendation it produces is marked
HUMAN_REVIEW — this is a safety net, not a policy anyone should trust blindly.

Output shape matches Intelligence's own recommendation objects exactly, so
the existing dashboard UI (built against that shape) renders these with no
frontend changes.
"""

from __future__ import annotations

from app.ingestion.state import NetworkState

FUELS = ("DIESEL", "PETROL", "OCTANE")
URGENT_RATIO = 0.2  # station inventory/capacity below this triggers a pick


def compute_fallback_recommendations(state: NetworkState) -> list[dict]:
    if not state.depots or not state.stations or not state.routes:
        return []

    routes_by_dest: dict[str, list] = {}
    for r in state.routes.data:
        if r.status.value != "AVAILABLE":
            continue
        routes_by_dest.setdefault(r.destination_station_id, []).append(r)

    depots_by_id = {d.id: d for d in state.depots.data if d.status.value in ("OPEN", "CONSTRAINED")}

    recommendations: list[dict] = []
    for station in state.stations.data:
        if station.status.value != "OPEN":
            continue
        for fuel in FUELS:
            capacity = getattr(station.capacity, fuel)
            inventory = getattr(station.inventory, fuel)
            if capacity <= 0 or inventory / capacity >= URGENT_RATIO:
                continue

            best = None  # (depot, route, quantity), fastest transit wins
            for route in routes_by_dest.get(station.id, []):
                depot = depots_by_id.get(route.source_depot_id)
                if depot is None:
                    continue
                depot_inventory = getattr(depot.inventory, fuel)
                if depot_inventory <= 0:
                    continue
                headroom = capacity - inventory
                quantity = min(route.max_shipment, depot_inventory, headroom, depot.dispatch_capacity_per_tick)
                if quantity <= 0:
                    continue
                if best is None or route.transit_ticks < best[1].transit_ticks:
                    best = (depot, route, quantity)

            if best is None:
                continue
            depot, route, quantity = best
            ratio_pct = inventory / capacity * 100
            recommendations.append(
                {
                    "id": f"fallback-{station.id}-{fuel}-{route.id}",
                    "station_id": station.id,
                    "fuel_type": fuel,
                    "action": {
                        "source_depot_id": depot.id,
                        "route_id": route.id,
                        "quantity": round(quantity, 1),
                    },
                    "alternatives": [],
                    "constraints": ["core fallback heuristic — Intelligence service unavailable"],
                    "binding_constraints": [],
                    "signals": ["intelligence_unavailable"],
                    "impact": {
                        "stockout_before_h": 0,
                        "stockout_after_h": 0,
                        "unmet_before_l": 0,
                        "unmet_after_l": 0,
                        "risk_before": 1.0,
                        "risk_after": 0.5,
                    },
                    "confidence": 0.3,
                    "review": "HUMAN_REVIEW",
                    "policy": "core_fallback_heuristic",
                    "explanation": (
                        f"[FALLBACK POLICY — Intelligence unavailable] {station.name or station.id} "
                        f"{fuel.lower()} at {ratio_pct:.0f}% capacity. Suggest {quantity:.0f} L from "
                        f"{depot.name or depot.id} via {route.id}. Unreviewed heuristic pick — verify "
                        f"before approving. [Simulated environment]"
                    ),
                }
            )
    return recommendations
