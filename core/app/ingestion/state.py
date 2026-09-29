"""In-memory snapshot of the simulator's world, kept fresh by Poller + SSEListener.

REST is the only source of truth (hard rule) — this snapshot is always built
from `/v1/*` GETs. SSE only tells the poller *when* to re-fetch sooner; no
field here is ever set directly from an SSE payload.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from fuelsupply_shared.models import (
    Allocation,
    DemandHistoryEntry,
    Depot,
    DomainEvent,
    InstanceState,
    Region,
    Route,
    SimulatorMetrics,
    Station,
    SupplyArrival,
)

from app.simulator_client import SimulatorResponse


@dataclass
class NetworkState:
    instance: SimulatorResponse[InstanceState] | None = None
    regions: SimulatorResponse[list[Region]] | None = None
    depots: SimulatorResponse[list[Depot]] | None = None
    stations: SimulatorResponse[list[Station]] | None = None
    routes: SimulatorResponse[list[Route]] | None = None
    supply_arrivals: SimulatorResponse[list[SupplyArrival]] | None = None
    events: SimulatorResponse[list[DomainEvent]] | None = None
    allocations: SimulatorResponse[list[Allocation]] | None = None
    metrics: SimulatorResponse[SimulatorMetrics] | None = None
    demand_history_by_station: dict[str, SimulatorResponse[list[DemandHistoryEntry]]] = field(
        default_factory=dict
    )

    last_poll_at: datetime | None = None
    last_poll_error: str | None = None
    last_sse_connected_at: datetime | None = None
    last_sse_event_tick: int | None = None
    sse_connected: bool = False

    @property
    def last_polled_tick(self) -> int | None:
        return self.instance.data.tick if self.instance else None

    @property
    def is_ready(self) -> bool:
        """True once at least one full poll cycle has populated every resource."""
        return self.instance is not None and self.depots is not None and self.stations is not None

    def mark_poll_success(self, now: datetime | None = None) -> None:
        self.last_poll_at = now or datetime.now(timezone.utc)
        self.last_poll_error = None

    def mark_poll_error(self, message: str, now: datetime | None = None) -> None:
        self.last_poll_at = now or datetime.now(timezone.utc)
        self.last_poll_error = message
