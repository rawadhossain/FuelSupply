"""Composition helpers wiring the store into the ingestion/allocation layers
without those layers depending on storage directly (Poller and
AllocationExecutor only know about an optional async callback)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fuelsupply_shared.models import Allocation
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ingestion.state import NetworkState

from .allocation_store import reconcile_allocations, upsert_allocation
from .audit_log import record_event
from .redis_cache import RedisStateCache


def make_poll_success_hook(
    session_factory: async_sessionmaker[AsyncSession], redis_cache: RedisStateCache
) -> Callable[[NetworkState], Awaitable[None]]:
    async def hook(state: NetworkState) -> None:
        if state.allocations is not None:
            async with session_factory() as session:
                await reconcile_allocations(session, state.allocations.data)
        await redis_cache.write_snapshot(state)

    return hook


def make_allocation_write_hook(
    session_factory: async_sessionmaker[AsyncSession],
) -> Callable[[Allocation], Awaitable[None]]:
    async def hook(allocation: Allocation) -> None:
        status_value = (
            allocation.status.value if hasattr(allocation.status, "value") else allocation.status
        )
        async with session_factory() as session:
            await upsert_allocation(session, allocation)
            await record_event(
                session,
                event_type="allocation_write",
                details={
                    "id": allocation.id,
                    "status": status_value,
                    "idempotency_key": allocation.idempotency_key,
                },
            )

    return hook
