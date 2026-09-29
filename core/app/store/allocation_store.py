"""Mirrors the simulator's allocation ledger into Postgres.

Never authoritative — every write here is derived from a REST read; on
conflict the simulator's value always wins (ASM-007). This just makes
decision history queryable and durable across a Core restart.
"""

from __future__ import annotations

from fuelsupply_shared.models import Allocation
from sqlalchemy.ext.asyncio import AsyncSession

from .models import AllocationRecord


async def _apply(session: AsyncSession, allocation: Allocation) -> None:
    fuel_value = allocation.fuel_type.value if hasattr(allocation.fuel_type, "value") else allocation.fuel_type
    status_value = allocation.status.value if hasattr(allocation.status, "value") else allocation.status

    record = await session.get(AllocationRecord, allocation.id)
    if record is None:
        record = AllocationRecord(id=allocation.id)
        session.add(record)

    record.idempotency_key = allocation.idempotency_key
    record.source_depot_id = allocation.source_depot_id
    record.destination_station_id = allocation.destination_station_id
    record.route_id = allocation.route_id
    record.fuel_type = fuel_value
    record.quantity = allocation.quantity
    record.created_tick = allocation.created_tick
    record.departure_tick = allocation.departure_tick
    record.expected_arrival_tick = allocation.expected_arrival_tick
    record.actual_arrival_tick = allocation.actual_arrival_tick
    record.status = status_value
    record.failure_reason = allocation.failure_reason


async def upsert_allocation(session: AsyncSession, allocation: Allocation) -> None:
    await _apply(session, allocation)
    await session.commit()


async def reconcile_allocations(session: AsyncSession, allocations: list[Allocation]) -> None:
    """Upsert every allocation from a fresh REST read in one transaction."""
    for allocation in allocations:
        await _apply(session, allocation)
    await session.commit()
