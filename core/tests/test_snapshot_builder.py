import pytest
from app.ingestion.state import NetworkState
from app.intelligence_client import build_sim_snapshot
from app.intelligence_client.snapshot import SnapshotNotReadyError
from app.simulator_client import SimulatorResponse
from fuelsupply_shared.models import (
    Depot,
    DomainEvent,
    InstanceState,
    Region,
    Route,
    Station,
)


def make_ready_state() -> NetworkState:
    state = NetworkState()
    state.instance = SimulatorResponse(
        data=InstanceState(
            id=1, scenario_id="baseline", scenario_version="1.0", seed=1,
            sim_time="2026-01-01T00:00:00", tick=42, tick_minutes=15, status="PAUSED",
        ),
        stale=False,
    )
    state.regions = SimulatorResponse(data=[Region(id="region-dhaka", name="Dhaka", demand_factor=1.0)], stale=False)
    state.depots = SimulatorResponse(
        data=[
            Depot(
                id="depot-gazipur", name="Gazipur", region_id="region-dhaka", status="OPEN",
                dispatch_capacity_per_tick=12000.0,
                capacity={"DIESEL": 90000, "PETROL": 70000, "OCTANE": 45000},
                inventory={"DIESEL": 60000, "PETROL": 45000, "OCTANE": 26000},
            )
        ],
        stale=False,
    )
    state.stations = SimulatorResponse(
        data=[
            Station(
                id="station-mirpur", name="Mirpur", region_id="region-dhaka", status="OPEN",
                demand_profile="urban_high", demand_multiplier=1.0,
                capacity={"DIESEL": 15000, "PETROL": 14000, "OCTANE": 9000},
                inventory={"DIESEL": 9000, "PETROL": 9000, "OCTANE": 5000},
            )
        ],
        stale=False,
    )
    state.routes = SimulatorResponse(
        data=[
            Route(
                id="route-gazipur-mirpur", source_depot_id="depot-gazipur",
                destination_station_id="station-mirpur", transit_ticks=2, max_shipment=7000.0,
                status="AVAILABLE",
            )
        ],
        stale=False,
    )
    state.supply_arrivals = SimulatorResponse(data=[], stale=False)
    state.allocations = SimulatorResponse(data=[], stale=False)
    state.events = SimulatorResponse(
        data=[
            DomainEvent(
                id=1, type="demand_spike", status="SCHEDULED", start_tick=20, end_tick=30,
                parameters={"station_ids": ["station-mirpur"], "multiplier": 1.5},
            )
        ],
        stale=False,
    )
    return state


def test_build_sim_snapshot_matches_intelligence_shape() -> None:
    snapshot = build_sim_snapshot(make_ready_state())

    assert snapshot["instance"] == {"tick": 42, "status": "PAUSED", "seed": 1}
    assert snapshot["depots"][0]["id"] == "depot-gazipur"
    assert snapshot["depots"][0]["inventory"] == {"DIESEL": 60000, "PETROL": 45000, "OCTANE": 26000}
    assert snapshot["stations"][0]["id"] == "station-mirpur"
    assert snapshot["routes"][0]["id"] == "route-gazipur-mirpur"
    # The real /v1/events shape is {id: int, ..., end_tick: int} — no duration_ticks.
    assert snapshot["events"][0] == {
        "id": 1,
        "type": "demand_spike",
        "start_tick": 20,
        "end_tick": 30,
        "status": "SCHEDULED",
        "parameters": {"station_ids": ["station-mirpur"], "multiplier": 1.5},
    }


def test_build_sim_snapshot_raises_when_not_ready() -> None:
    with pytest.raises(SnapshotNotReadyError):
        build_sim_snapshot(NetworkState())
