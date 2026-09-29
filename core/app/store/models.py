"""Our own Postgres schema — never authoritative for simulator world state
(ASM-007: REST is the source of truth; on any conflict, the simulator wins).
This exists so decision history and audit trail survive a Core restart and
can be queried without re-hitting the simulator.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class AllocationRecord(Base):
    """Mirror of the simulator's own allocation ledger (`GET /v1/allocations`)."""

    __tablename__ = "allocations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)  # simulator's own allocation id
    idempotency_key: Mapped[str] = mapped_column(String(150))
    source_depot_id: Mapped[str] = mapped_column(String(64))
    destination_station_id: Mapped[str] = mapped_column(String(64))
    route_id: Mapped[str] = mapped_column(String(64))
    fuel_type: Mapped[str] = mapped_column(String(16))
    quantity: Mapped[float] = mapped_column(Float)
    created_tick: Mapped[int] = mapped_column(Integer)
    departure_tick: Mapped[int | None] = mapped_column(Integer, nullable=True)
    expected_arrival_tick: Mapped[int | None] = mapped_column(Integer, nullable=True)
    actual_arrival_tick: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(16))
    failure_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AuditLogEntry(Base):
    """Our own log of important actions (REQ-022) — distinct from the
    simulator's ledger. Allocation submissions/cancellations today; decision
    and recovery events once Phase 2/4 exist to produce them."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    event_type: Mapped[str] = mapped_column(String(64))
    details: Mapped[dict] = mapped_column(JSON)


class PredictionRecord(Base):
    """Forecast/risk output, persisted for later comparison against actuals.

    Schema only for now — nothing writes here until TASK-014 (Phase 2
    forecasting) exists to produce predictions.
    """

    __tablename__ = "predictions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    station_id: Mapped[str] = mapped_column(String(64))
    fuel_type: Mapped[str] = mapped_column(String(16))
    tick: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict] = mapped_column(JSON)


class DemandSnapshot(Base):
    """Bounded-window snapshots of `/v1/demand-history` for offline reuse.

    Schema only for now — the ingestion poller deliberately does not fetch
    demand-history on its own cycle (RISK-006: unbounded table, and it's a
    per-station/fuel query INTEL drives directly); wiring a writer here is
    TASK-014's job once forecasting needs a persisted window.
    """

    __tablename__ = "demand_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    station_id: Mapped[str] = mapped_column(String(64))
    fuel_type: Mapped[str] = mapped_column(String(16))
    tick: Mapped[int] = mapped_column(Integer)
    demand_liters: Mapped[float] = mapped_column(Float)
    served_liters: Mapped[float] = mapped_column(Float)
    unmet_liters: Mapped[float] = mapped_column(Float)
