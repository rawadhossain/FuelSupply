import asyncio

from app.store import init_models, make_engine, make_session_factory
from app.store.allocation_store import reconcile_allocations, upsert_allocation
from app.store.audit_log import record_event
from app.store.models import AllocationRecord, AuditLogEntry
from fuelsupply_shared.models import Allocation
from sqlalchemy import select

ALLOCATION = Allocation.model_validate(
    {
        "id": 1,
        "idempotency_key": "alloc-abc",
        "source_depot_id": "depot-gazipur",
        "destination_station_id": "station-mirpur",
        "route_id": "route-gazipur-mirpur",
        "fuel_type": "DIESEL",
        "quantity": 1000.0,
        "created_tick": 15,
        "departure_tick": None,
        "expected_arrival_tick": None,
        "actual_arrival_tick": None,
        "status": "PENDING",
        "failure_reason": None,
    }
)


async def make_session_factory_in_memory():
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    await init_models(engine)
    return make_session_factory(engine)


def test_reconcile_allocations_persists_rows() -> None:
    async def scenario():
        session_factory = await make_session_factory_in_memory()
        async with session_factory() as session:
            await reconcile_allocations(session, [ALLOCATION])
            rows = (await session.execute(select(AllocationRecord))).scalars().all()
        return rows

    rows = asyncio.run(scenario())
    assert len(rows) == 1
    assert rows[0].id == 1
    assert rows[0].status == "PENDING"
    assert rows[0].idempotency_key == "alloc-abc"


def test_upsert_allocation_updates_existing_row_not_duplicate() -> None:
    async def scenario():
        session_factory = await make_session_factory_in_memory()
        async with session_factory() as session:
            await upsert_allocation(session, ALLOCATION)
            cancelled = ALLOCATION.model_copy(update={"status": "CANCELLED"})
            await upsert_allocation(session, cancelled)
            rows = (await session.execute(select(AllocationRecord))).scalars().all()
        return rows

    rows = asyncio.run(scenario())
    assert len(rows) == 1  # updated in place, not a second row
    assert rows[0].status == "CANCELLED"


def test_record_event_persists_json_details() -> None:
    async def scenario():
        session_factory = await make_session_factory_in_memory()
        async with session_factory() as session:
            await record_event(
                session, event_type="allocation_write", details={"id": 1, "status": "PENDING"}
            )
            rows = (await session.execute(select(AuditLogEntry))).scalars().all()
        return rows

    rows = asyncio.run(scenario())
    assert len(rows) == 1
    assert rows[0].event_type == "allocation_write"
    assert rows[0].details == {"id": 1, "status": "PENDING"}
