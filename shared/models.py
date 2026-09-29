"""Pydantic models of the simulator's /v1/* JSON (guide §4), used at the service boundary to reject
malformed input (REQ-009b). Unknown extra fields are allowed so a newer simulator does not break us
(REQ-041); fields we rely on are typed and required."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from .snapshot import Snapshot


class _M(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)


class Instance(_M):
    tick: int = Field(ge=0)
    status: Optional[str] = None
    seed: Optional[int] = None


class Region(_M):
    id: str
    demand_factor: float = Field(gt=0)


class Station(_M):
    id: str
    region_id: str
    status: str
    demand_multiplier: float = Field(default=1.0, gt=0)
    capacity: dict[str, float]
    inventory: dict[str, float]


class Depot(_M):
    id: str
    region_id: str
    status: str
    dispatch_capacity_per_tick: float = Field(ge=0)
    capacity: dict[str, float]
    inventory: dict[str, float]


class Route(_M):
    id: str
    source_depot_id: str
    destination_station_id: str
    transit_ticks: int = Field(ge=0)
    max_shipment: float = Field(gt=0)
    status: str


class SupplyArrival(_M):
    id: str
    depot_id: str
    fuel_type: str
    quantity: float = Field(ge=0)
    planned_tick: int
    actual_tick: Optional[int] = None
    status: str


class Allocation(_M):
    id: int
    route_id: str
    source_depot_id: str
    destination_station_id: str
    fuel_type: str
    quantity: float = Field(gt=0)
    created_tick: int
    expected_arrival_tick: Optional[int] = None
    status: str


class Event(_M):
    id: int
    type: str
    start_tick: int
    end_tick: int
    status: str
    parameters: dict = Field(default_factory=dict)


class DemandRow(_M):
    station_id: str
    fuel_type: str
    tick: int
    demand_liters: float = Field(ge=0)
    served_liters: Optional[float] = Field(default=None, ge=0)
    unmet_liters: Optional[float] = None


class SimSnapshot(_M):
    """The /v1/* responses of one tick, keyed like the endpoints."""
    instance: Instance
    stations: list[Station]
    depots: list[Depot]
    routes: list[Route]
    supply_arrivals: list[SupplyArrival] = Field(default_factory=list, alias="supply-arrivals")
    allocations: list[Allocation] = Field(default_factory=list)
    events: list[Event] = Field(default_factory=list)
    regions: list[Region] = Field(default_factory=list)

    def to_snapshot(self, stale: bool = False) -> Snapshot:
        return Snapshot.from_api(self.model_dump(by_alias=True), stale=stale)
