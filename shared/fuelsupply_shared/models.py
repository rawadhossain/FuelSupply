"""Shared Pydantic models matching the BUP Fuel Supply Simulator's real responses.

Every model below was validated against a live simulator instance
(asifmahmoud414/bup-fuel-supply-simulator:1.0.0), not guessed from the docs
alone. See docs/api-contracts.md CONTRACT-SIM-REST / CONTRACT-SIM-ALLOC for
the captured request/response examples this file is derived from.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class FuelType(str, Enum):
    DIESEL = "DIESEL"
    PETROL = "PETROL"
    OCTANE = "OCTANE"


class FuelAmounts(BaseModel):
    """Capacity/inventory maps are keyed by fuel type in every simulator payload."""

    DIESEL: float
    PETROL: float
    OCTANE: float


class SimulatorStatus(str, Enum):
    PAUSED = "PAUSED"
    RUNNING = "RUNNING"


class DepotStatus(str, Enum):
    OPEN = "OPEN"
    CONSTRAINED = "CONSTRAINED"
    CLOSED = "CLOSED"


class StationStatus(str, Enum):
    OPEN = "OPEN"
    OUTAGE = "OUTAGE"


class RouteStatus(str, Enum):
    AVAILABLE = "AVAILABLE"
    DISRUPTED = "DISRUPTED"


class SupplyArrivalStatus(str, Enum):
    SCHEDULED = "SCHEDULED"
    DELAYED = "DELAYED"
    ARRIVED = "ARRIVED"


class EventStatus(str, Enum):
    SCHEDULED = "SCHEDULED"
    ACTIVE = "ACTIVE"
    RESOLVED = "RESOLVED"


class EventType(str, Enum):
    DEMAND_SPIKE = "demand_spike"
    ROUTE_DISRUPTION = "route_disruption"
    STATION_OUTAGE = "station_outage"
    DEPOT_CONSTRAINT = "depot_constraint"
    SHIPMENT_DELAY = "shipment_delay"
    SUPPLY_SHORTFALL = "supply_shortfall"


class AllocationStatus(str, Enum):
    PENDING = "PENDING"
    IN_TRANSIT = "IN_TRANSIT"
    ARRIVED = "ARRIVED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class FaultType(str, Enum):
    LATENCY = "latency"
    UNAVAILABLE = "unavailable"
    ERROR_RATE = "error_rate"
    STALE_DATA = "stale_data"
    STREAM_DISCONNECT = "stream_disconnect"


# ---- GET /v1/instance ------------------------------------------------------


class InstanceState(BaseModel):
    id: int
    scenario_id: str
    scenario_version: str
    seed: int
    sim_time: datetime
    tick: int
    tick_minutes: int
    status: SimulatorStatus


# ---- GET /v1/regions --------------------------------------------------------


class Region(BaseModel):
    id: str
    name: str
    demand_factor: float


# ---- GET /v1/depots ----------------------------------------------------------


class Depot(BaseModel):
    id: str
    name: str
    region_id: str
    status: DepotStatus
    dispatch_capacity_per_tick: float
    capacity: FuelAmounts
    inventory: FuelAmounts


# ---- GET /v1/stations --------------------------------------------------------


class Station(BaseModel):
    id: str
    name: str
    region_id: str
    status: StationStatus
    demand_profile: str
    demand_multiplier: float
    capacity: FuelAmounts
    inventory: FuelAmounts


# ---- GET /v1/routes -----------------------------------------------------------


class Route(BaseModel):
    id: str
    source_depot_id: str
    destination_station_id: str
    transit_ticks: int
    max_shipment: float
    status: RouteStatus


# ---- GET /v1/supply-arrivals ---------------------------------------------------


class SupplyArrival(BaseModel):
    id: str
    depot_id: str
    fuel_type: FuelType
    quantity: float
    planned_tick: int
    actual_tick: Optional[int] = None
    status: SupplyArrivalStatus


# ---- GET /v1/events -------------------------------------------------------------


class DomainEvent(BaseModel):
    id: int
    type: EventType
    status: EventStatus
    start_tick: int
    end_tick: int
    parameters: dict = Field(default_factory=dict)


# ---- GET /v1/demand-history -------------------------------------------------------


class DemandHistoryEntry(BaseModel):
    id: int
    station_id: str
    fuel_type: FuelType
    tick: int
    sim_time: datetime
    demand_liters: float
    served_liters: float
    unmet_liters: float


# ---- GET /v1/metrics -----------------------------------------------------------------


class SimulatorMetrics(BaseModel):
    served_demand_liters: float
    unmet_demand_liters: float
    service_level: float
    allocation_liters: float
    allocation_failures: int


# ---- Allocation write path: POST /v1/allocations, POST /v1/allocations/{id}/cancel ------


class AllocationRequest(BaseModel):
    idempotency_key: str = Field(min_length=1, max_length=150)
    source_depot_id: str
    destination_station_id: str
    route_id: str
    fuel_type: FuelType
    quantity: float = Field(gt=0)


class Allocation(BaseModel):
    id: int
    idempotency_key: str
    source_depot_id: str
    destination_station_id: str
    route_id: str
    fuel_type: FuelType
    quantity: float
    created_tick: int
    departure_tick: Optional[int] = None
    expected_arrival_tick: Optional[int] = None
    actual_arrival_tick: Optional[int] = None
    status: AllocationStatus
    failure_reason: Optional[str] = None


class SimulatorErrorDetail(BaseModel):
    """Shape used by `/v1/allocations` 404/409/422 responses: {"detail": {...}}."""

    code: str
    message: str


class SimulatorFaultError(BaseModel):
    """Shape used by `unavailable`/`error_rate` 503 fault responses: {"error": {...}}."""

    code: str
    message: str


# ---- GET /v1/health ---------------------------------------------------------------------


class SimulationSummary(BaseModel):
    status: SimulatorStatus
    tick: int


class HealthResponse(BaseModel):
    status: str
    database: str
    simulation: SimulationSummary


# ---- SSE payloads (advisory only — never treated as authoritative state) ----------------


class TickEvent(BaseModel):
    tick: int
    sim_time: datetime


class InventoryUpdatedEvent(BaseModel):
    entity_type: str
    entity_id: str
    inventory: FuelAmounts


class SimulatorNotice(BaseModel):
    level: Optional[str] = None
    message: str
