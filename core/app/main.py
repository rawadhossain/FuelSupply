import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fuelsupply_shared.observability import setup_observability
from sqlalchemy import func, select

from app import config
from app.allocation_executor import AllocationExecutor
from app.ingestion import IngestionSupervisor
from app.simulator_client import SimulatorClient
from app.store import (
    RedisStateCache,
    init_models,
    make_allocation_write_hook,
    make_engine,
    make_poll_success_hook,
    make_session_factory,
)
from app.store.models import AllocationRecord, AuditLogEntry

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    engine = make_engine(config.DATABASE_URL)
    await init_models(engine)
    session_factory = make_session_factory(engine)
    redis_cache = RedisStateCache(config.REDIS_URL)

    client = SimulatorClient(base_url=config.SIMULATOR_BASE_URL)
    supervisor = IngestionSupervisor(
        client=client,
        sse_base_url=config.SIMULATOR_BASE_URL,
        poll_interval_seconds=config.POLL_INTERVAL_SECONDS,
        gap_threshold_ticks=config.SSE_GAP_THRESHOLD_TICKS,
        gap_check_interval_seconds=config.SSE_GAP_CHECK_INTERVAL_SECONDS,
        on_poll_success=make_poll_success_hook(session_factory, redis_cache),
    )
    allocation_executor = AllocationExecutor(
        client, on_write=make_allocation_write_hook(session_factory)
    )

    app.state.supervisor = supervisor
    app.state.allocation_executor = allocation_executor
    app.state.session_factory = session_factory
    app.state.redis_cache = redis_cache

    run_task = None
    try:
        run_task = asyncio.create_task(supervisor.run())
        yield
    finally:
        await supervisor.stop()
        if run_task is not None:
            run_task.cancel()
        await client.aclose()
        await redis_cache.aclose()
        await engine.dispose()


app = FastAPI(title="Fuel Supply Core Service", lifespan=lifespan)

setup_observability(app, service="core")


@app.get("/health")
async def health() -> dict:
    return {"status": "healthy", "service": "core"}


@app.get("/internal/ingestion-state")
async def ingestion_state() -> dict:
    """Debug/verification view of the ingestion layer — not the public dashboard
    contract (that's CONTRACT-CORE-API, TASK-003/020). Exists so TASK-011's
    poller+SSE behavior is inspectable without waiting on the store (TASK-013)
    or the frontend.
    """
    supervisor: IngestionSupervisor = app.state.supervisor
    state = supervisor.state
    return {
        "ready": state.is_ready,
        "last_polled_tick": state.last_polled_tick,
        "last_poll_at": state.last_poll_at.isoformat() if state.last_poll_at else None,
        "last_poll_error": state.last_poll_error,
        "sse_connected": state.sse_connected,
        "last_sse_event_tick": state.last_sse_event_tick,
        "counts": {
            "regions": len(state.regions.data) if state.regions else None,
            "depots": len(state.depots.data) if state.depots else None,
            "stations": len(state.stations.data) if state.stations else None,
            "routes": len(state.routes.data) if state.routes else None,
            "supply_arrivals": len(state.supply_arrivals.data) if state.supply_arrivals else None,
            "events": len(state.events.data) if state.events else None,
            "allocations": len(state.allocations.data) if state.allocations else None,
        },
        "any_stale": any(
            resource is not None and resource.stale
            for resource in (
                state.instance,
                state.regions,
                state.depots,
                state.stations,
                state.routes,
                state.supply_arrivals,
                state.events,
                state.allocations,
                state.metrics,
            )
        ),
    }


@app.get("/internal/store-state")
async def store_state() -> dict:
    """Debug/verification view of the Postgres + Redis store (TASK-013) — not
    the public contract. Lets VER-* evidence be pulled over HTTP instead of
    exec-ing into the container to run SQL by hand.
    """
    session_factory = app.state.session_factory
    redis_cache: RedisStateCache = app.state.redis_cache

    async with session_factory() as session:
        allocation_count = (
            await session.execute(select(func.count()).select_from(AllocationRecord))
        ).scalar_one()
        audit_count = (
            await session.execute(select(func.count()).select_from(AuditLogEntry))
        ).scalar_one()

    return {
        "postgres": {"allocations": allocation_count, "audit_log": audit_count},
        "redis_snapshot": await redis_cache.read_snapshot(),
    }
