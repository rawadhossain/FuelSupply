"""Builds the JSON body Intelligence's `SimSnapshot` expects (`intelligence/models.py`)
from Core's own `NetworkState` (`app.ingestion.state`) — never from the simulator
directly. Intelligence never calls the simulator (SPEC §5); Core is the only bridge.
"""

from __future__ import annotations

from app.ingestion.state import NetworkState


class SnapshotNotReadyError(Exception):
    """Raised when the poller hasn't completed a first full cycle yet — there's
    nothing meaningful to send Intelligence."""


def _fuel_amounts(amounts) -> dict[str, float]:
    return {"DIESEL": amounts.DIESEL, "PETROL": amounts.PETROL, "OCTANE": amounts.OCTANE}


def build_sim_snapshot(state: NetworkState) -> dict:
    if not state.is_ready or state.instance is None:
        raise SnapshotNotReadyError("ingestion has not completed a first poll cycle yet")

    instance = state.instance.data
    return {
        "instance": {
            "tick": instance.tick,
            "status": instance.status.value,
            "seed": instance.seed,
        },
        "regions": [
            {"id": r.id, "demand_factor": r.demand_factor} for r in (state.regions.data if state.regions else [])
        ],
        "stations": [
            {
                "id": s.id,
                "region_id": s.region_id,
                "status": s.status.value,
                "demand_multiplier": s.demand_multiplier,
                "capacity": _fuel_amounts(s.capacity),
                "inventory": _fuel_amounts(s.inventory),
            }
            for s in (state.stations.data if state.stations else [])
        ],
        "depots": [
            {
                "id": d.id,
                "region_id": d.region_id,
                "status": d.status.value,
                "dispatch_capacity_per_tick": d.dispatch_capacity_per_tick,
                "capacity": _fuel_amounts(d.capacity),
                "inventory": _fuel_amounts(d.inventory),
            }
            for d in (state.depots.data if state.depots else [])
        ],
        "routes": [
            {
                "id": r.id,
                "source_depot_id": r.source_depot_id,
                "destination_station_id": r.destination_station_id,
                "transit_ticks": r.transit_ticks,
                "max_shipment": r.max_shipment,
                "status": r.status.value,
            }
            for r in (state.routes.data if state.routes else [])
        ],
        "supply_arrivals": [
            {
                "id": a.id,
                "depot_id": a.depot_id,
                "fuel_type": a.fuel_type.value,
                "quantity": a.quantity,
                "planned_tick": a.planned_tick,
                "actual_tick": a.actual_tick,
                "status": a.status.value,
            }
            for a in (state.supply_arrivals.data if state.supply_arrivals else [])
        ],
        "allocations": [
            {
                "id": a.id,
                "route_id": a.route_id,
                "source_depot_id": a.source_depot_id,
                "destination_station_id": a.destination_station_id,
                "fuel_type": a.fuel_type.value,
                "quantity": a.quantity,
                "created_tick": a.created_tick,
                "expected_arrival_tick": a.expected_arrival_tick,
                "status": a.status.value,
            }
            for a in (state.allocations.data if state.allocations else [])
        ],
        "events": [
            {
                "id": e.id,
                "type": e.type.value,
                "start_tick": e.start_tick,
                "end_tick": e.end_tick,
                "status": e.status.value,
                "parameters": e.parameters,
            }
            for e in (state.events.data if state.events else [])
        ],
    }
