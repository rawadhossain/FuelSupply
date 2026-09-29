"""Typed snapshot of the simulator world at one tick (built from /v1/* JSON).
Shared by Core (fallback) and the Intelligence service. Pure Python; no hard-coded topology."""
from __future__ import annotations

from dataclasses import dataclass, field

FUELS = ("DIESEL", "PETROL", "OCTANE")


@dataclass
class Station:
    id: str
    region_id: str
    status: str
    demand_multiplier: float
    capacity: dict[str, float]
    inventory: dict[str, float]


@dataclass
class Depot:
    id: str
    region_id: str
    status: str
    dispatch_capacity_per_tick: float
    capacity: dict[str, float]
    inventory: dict[str, float]


@dataclass
class Route:
    id: str
    source_depot_id: str
    destination_station_id: str
    transit_ticks: int
    max_shipment: float
    status: str


@dataclass
class Arrival:
    id: str
    depot_id: str
    fuel_type: str
    quantity: float
    planned_tick: int
    status: str


@dataclass
class Allocation:
    id: int
    route_id: str
    source_depot_id: str
    destination_station_id: str
    fuel_type: str
    quantity: float
    created_tick: int
    expected_arrival_tick: int | None
    status: str


@dataclass
class Event:
    id: int
    type: str
    start_tick: int
    end_tick: int
    status: str
    parameters: dict


@dataclass
class Snapshot:
    tick: int
    stations: dict[str, Station]
    depots: dict[str, Depot]
    routes: dict[str, Route]
    arrivals: list[Arrival] = field(default_factory=list)
    allocations: list[Allocation] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)
    region_factor: dict[str, float] = field(default_factory=dict)
    stale: bool = False

    @property
    def fuels(self) -> tuple[str, ...]:
        seen = {f for s in self.stations.values() for f in s.capacity}
        return tuple(f for f in FUELS if f in seen) + tuple(sorted(seen - set(FUELS)))

    @classmethod
    def from_api(cls, data: dict, stale: bool = False) -> "Snapshot":
        """data: {'instance': {...}, 'stations': [...], 'depots': [...], 'routes': [...],
        'supply-arrivals': [...], 'allocations': [...], 'events': [...], 'regions': [...]}"""
        f = lambda d: {k: float(v) for k, v in d.items()}
        return cls(
            tick=int(data["instance"]["tick"]),
            stations={s["id"]: Station(s["id"], s["region_id"], s["status"], float(s.get("demand_multiplier", 1.0)),
                                       f(s["capacity"]), f(s["inventory"])) for s in data["stations"]},
            depots={d["id"]: Depot(d["id"], d["region_id"], d["status"], float(d["dispatch_capacity_per_tick"]),
                                   f(d["capacity"]), f(d["inventory"])) for d in data["depots"]},
            routes={r["id"]: Route(r["id"], r["source_depot_id"], r["destination_station_id"], int(r["transit_ticks"]),
                                   float(r["max_shipment"]), r["status"]) for r in data["routes"]},
            arrivals=[Arrival(a["id"], a["depot_id"], a["fuel_type"], float(a["quantity"]),
                              int(a["actual_tick"] if a.get("actual_tick") is not None else a["planned_tick"]),
                              a["status"]) for a in data.get("supply-arrivals", [])],
            allocations=[Allocation(a["id"], a["route_id"], a["source_depot_id"], a["destination_station_id"],
                                    a["fuel_type"], float(a["quantity"]), int(a["created_tick"]),
                                    a.get("expected_arrival_tick"), a["status"]) for a in data.get("allocations", [])],
            events=[Event(e["id"], e["type"], int(e["start_tick"]), int(e["end_tick"]), e["status"],
                          e.get("parameters") or {}) for e in data.get("events", [])],
            region_factor={r["id"]: float(r["demand_factor"]) for r in data.get("regions", [])},
            stale=stale,
        )
