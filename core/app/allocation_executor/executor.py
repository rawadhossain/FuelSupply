"""The only component allowed to write to the simulator (REQ-005, REQ-006).

Every submission gets a deterministic idempotency key (ADR-004) and is
confirmed with a follow-up REST read of `/v1/allocations` after the write —
the POST response is trusted for the immediate result, but the list read is
what a future persistence layer (TASK-013) would reconcile from, and REST
being the only source of truth is a hard rule, not a suggestion.

Retrying after a FAILED allocation, or any other deliberate re-decision for
the same tick, is the caller's responsibility: bump `attempt` and call
`submit()` again. This executor does not auto-retry — that would blur the
line between "network retry, same key" and "new decision, new key" that
ADR-004 depends on.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from fuelsupply_shared.models import Allocation, AllocationRequest, FuelType

from app.simulator_client import SimulatorClient

from .idempotency import make_idempotency_key

logger = logging.getLogger(__name__)

OnWrite = Callable[[Allocation], Awaitable[None]]


class AllocationExecutor:
    def __init__(self, client: SimulatorClient, on_write: OnWrite | None = None) -> None:
        self._client = client
        self._on_write = on_write

    async def submit(
        self,
        *,
        source_depot_id: str,
        destination_station_id: str,
        route_id: str,
        fuel_type: FuelType,
        quantity: float,
        tick: int,
        intent: str,
        attempt: int = 0,
    ) -> Allocation:
        fuel_value = fuel_type.value if isinstance(fuel_type, FuelType) else fuel_type
        key = make_idempotency_key(
            source_depot_id=source_depot_id,
            destination_station_id=destination_station_id,
            route_id=route_id,
            fuel_type=fuel_value,
            quantity=quantity,
            tick=tick,
            intent=intent,
            attempt=attempt,
        )
        request = AllocationRequest(
            idempotency_key=key,
            source_depot_id=source_depot_id,
            destination_station_id=destination_station_id,
            route_id=route_id,
            fuel_type=fuel_type,
            quantity=quantity,
        )
        result = await self._client.post_allocation(request)
        allocation = result.data
        logger.info(
            "allocation submitted: id=%s key=%s status=%s tick=%s intent=%s attempt=%s",
            allocation.id,
            key,
            allocation.status,
            tick,
            intent,
            attempt,
        )
        confirmed = await self._confirm(allocation.id)
        await self._notify(confirmed or allocation)
        return allocation

    async def cancel(self, allocation_id: int) -> Allocation:
        result = await self._client.post_cancel_allocation(allocation_id)
        allocation = result.data
        logger.info("allocation cancelled: id=%s status=%s", allocation.id, allocation.status)
        confirmed = await self._confirm(allocation.id)
        await self._notify(confirmed or allocation)
        return allocation

    async def _notify(self, allocation: Allocation) -> None:
        if self._on_write is None:
            return
        try:
            await self._on_write(allocation)
        except Exception:
            logger.exception("on_write hook failed for allocation id=%s", allocation.id)

    async def _confirm(self, allocation_id: int) -> Allocation | None:
        """Re-fetch the ledger and confirm the write landed — never trust the
        POST/cancel response alone as final truth (REST is the only source
        of truth, hard rule)."""
        listing = await self._client.get_allocations()
        for allocation in listing.data:
            if allocation.id == allocation_id:
                return allocation
        logger.warning(
            "allocation id=%s not found in follow-up GET /v1/allocations", allocation_id
        )
        return None
