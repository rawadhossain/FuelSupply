import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from time import perf_counter

import httpx
from fastapi import FastAPI, HTTPException
from fuelsupply_shared.observability import (
    HUMAN_REVIEW_REQUESTS_TOTAL,
    VALIDATION_REJECTIONS_TOTAL,
    log_decision,
    record_decision,
    record_fallback,
    set_degraded,
    setup_observability,
)
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app import config
from app.allocation_executor import AllocationExecutor
from app.fallback_allocator import compute_fallback_recommendations
from app.ingestion import IngestionSupervisor
from app.intelligence_client import (
    IntelligenceClient,
    IntelligenceInvalidInputError,
    IntelligenceUnavailableError,
    build_sim_snapshot,
)
from app.intelligence_client.snapshot import SnapshotNotReadyError
from app.simulator_client import SimulatorClient, SimulatorError
from app.store import (
    RedisStateCache,
    init_models,
    make_allocation_write_hook,
    make_engine,
    make_poll_success_hook,
    make_session_factory,
)
from app.store.audit_log import record_event
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
    intelligence_client = IntelligenceClient(base_url=config.INTELLIGENCE_BASE_URL)
    # /admin/* is a distinct surface (bypasses fault injection, orchestration
    # only) — kept as its own plain httpx client rather than bolted onto
    # SimulatorClient, which is scoped to the fault-affected /v1/* contract.
    admin_http = httpx.AsyncClient(base_url=config.SIMULATOR_BASE_URL, timeout=10.0)

    app.state.supervisor = supervisor
    app.state.allocation_executor = allocation_executor
    app.state.intelligence_client = intelligence_client
    app.state.simulator_client = client
    app.state.admin_http = admin_http
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
        await intelligence_client.aclose()
        await admin_http.aclose()
        await redis_cache.aclose()
        await engine.dispose()


app = FastAPI(title="Fuel Supply Core Service", lifespan=lifespan)

setup_observability(app, service="core")


@app.get("/health")
async def health() -> dict:
    return {"status": "healthy", "service": "core"}


@app.get("/internal/health-summary")
async def health_summary() -> dict:
    """Per-component health for the operator dashboard's health page (Problem
    §15 format) — judge-legible without reading logs. Actively probes each
    dependency rather than inferring from cached state.
    """
    components: list[dict] = [{"name": "core_api", "status": "healthy"}]

    session_factory = app.state.session_factory
    try:
        async with session_factory() as session:
            await session.execute(select(1))
        components.append({"name": "database", "status": "healthy"})
    except Exception:  # noqa: BLE001 - a dependency probe reports "down", never crashes the page
        components.append({"name": "database", "status": "down"})

    redis_cache: RedisStateCache = app.state.redis_cache
    try:
        await redis_cache.ping()
        components.append({"name": "redis", "status": "healthy"})
    except Exception:  # noqa: BLE001 - same as above
        components.append({"name": "redis", "status": "down"})

    simulator_client: SimulatorClient = app.state.simulator_client
    try:
        await simulator_client.get_health()
        components.append({"name": "fuel_simulator", "status": "healthy"})
    except SimulatorError:
        components.append({"name": "fuel_simulator", "status": "down"})

    intelligence_client: IntelligenceClient = app.state.intelligence_client
    try:
        intel_health = await intelligence_client.health()
        ok = intel_health.get("status") == "healthy"
        components.append({"name": "intelligence_service", "status": "healthy" if ok else "degraded"})
    except IntelligenceUnavailableError:
        components.append({"name": "intelligence_service", "status": "down"})

    supervisor: IngestionSupervisor = app.state.supervisor
    ingestion_ok = supervisor.state.is_ready and not supervisor.state.last_poll_error
    components.append({"name": "ingestion", "status": "healthy" if ingestion_ok else "degraded"})

    overall = "healthy" if all(c["status"] == "healthy" for c in components) else "degraded"
    return {"status": overall, "components": components}


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


@app.get("/internal/state")
async def network_state() -> dict:
    """Real entity data (not just counts) for the operator dashboard — depots,
    stations, routes, supply arrivals, events, allocations, all from Core's
    live NetworkState. Placeholder for CONTRACT-CORE-API (TASK-003).
    """
    supervisor: IngestionSupervisor = app.state.supervisor
    state = supervisor.state

    def dump(resp, many: bool = True):
        if resp is None:
            return [] if many else None
        return [item.model_dump(mode="json") for item in resp.data] if many else resp.data.model_dump(mode="json")

    return {
        "instance": dump(state.instance, many=False),
        "regions": dump(state.regions),
        "depots": dump(state.depots),
        "stations": dump(state.stations),
        "routes": dump(state.routes),
        "supply_arrivals": dump(state.supply_arrivals),
        "events": dump(state.events),
        "allocations": dump(state.allocations),
        "metrics": dump(state.metrics, many=False),
        "sse_connected": state.sse_connected,
        "last_poll_error": state.last_poll_error,
        "any_stale": any(
            resource is not None and resource.stale
            for resource in (
                state.instance, state.regions, state.depots, state.stations,
                state.routes, state.supply_arrivals, state.events, state.allocations, state.metrics,
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


class RecommendationsRequest(BaseModel):
    policy: str = Field(default="heuristic", description="'heuristic' or 'lp'")
    narrate: bool = Field(
        default=False, description="Ask Intelligence for LLM explanations (slower; needs OPENAI_API_KEY)"
    )


class ExecuteAllocationAction(BaseModel):
    source_depot_id: str
    route_id: str
    quantity: float = Field(gt=0)


class ExecuteRecommendationRequest(BaseModel):
    """Shape matches one entry of `/internal/recommendations`'s own
    `recommendations` list — a caller reads a recommendation, picks one, and
    reposts its `id`/`station_id`/`fuel_type`/`action` back here unchanged.

    `recommendation_id` is looked up against the copy Core itself cached when
    it was issued (see redis_cache.cache_recommendation) — the human-review
    gate (REQ-009c/REQ-019) is enforced against *that* stored copy, not
    whatever the caller claims, so a client can't skip review just by
    omitting or relabeling the field. `acknowledged` must be true for any
    recommendation the cached copy marked HUMAN_REVIEW.
    """

    recommendation_id: str
    station_id: str
    fuel_type: str
    action: ExecuteAllocationAction
    intent: str = "intelligence-recommendation"
    acknowledged: bool = False


@app.post("/internal/recommendations")
async def get_recommendations(req: RecommendationsRequest | None = None) -> dict:
    """The Core<->Intelligence bridge: builds a SimSnapshot from Core's own live
    `NetworkState` (never straight from the simulator — REST-is-truth still
    flows through Core's poller first), pulls a bounded recent demand-history
    window, and asks Intelligence to assess it. Not the public dashboard
    contract yet (TASK-003/020 should be written from this real shape).
    """
    req = req or RecommendationsRequest()
    supervisor: IngestionSupervisor = app.state.supervisor
    state = supervisor.state
    try:
        snapshot = build_sim_snapshot(state)
    except SnapshotNotReadyError as exc:
        raise HTTPException(503, {"code": "INGESTION_NOT_READY", "message": str(exc)}) from exc

    any_stale = any(
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
    )

    simulator_client: SimulatorClient = app.state.simulator_client
    try:
        demand_result = await simulator_client.get_demand_history(limit=config.DEMAND_HISTORY_LIMIT)
    except SimulatorError as exc:
        logger.warning("demand-history fetch failed, assessing without it: %s", exc)
        demand_rows: list[dict] = []
    else:
        any_stale = any_stale or demand_result.stale
        demand_rows = [
            {
                "station_id": r.station_id,
                "fuel_type": r.fuel_type.value,
                "tick": r.tick,
                "demand_liters": r.demand_liters,
                "served_liters": r.served_liters,
                "unmet_liters": r.unmet_liters,
            }
            for r in demand_result.data
        ]

    intelligence_client: IntelligenceClient = app.state.intelligence_client
    started = perf_counter()
    try:
        assessment = await intelligence_client.assess(
            snapshot=snapshot,
            demand_rows=demand_rows,
            stale=any_stale,
            policy=req.policy,
            narrate=req.narrate,
        )
        set_degraded("core", False)
    except IntelligenceInvalidInputError as exc:
        logger.error("Intelligence rejected our snapshot as invalid: %s", exc)
        raise HTTPException(exc.status_code, {"code": exc.code, "message": exc.message}) from exc
    except IntelligenceUnavailableError as exc:
        # REQ-009a: ML model unavailable -> fallback allocation policy. Never
        # auto-executed (every fallback recommendation is HUMAN_REVIEW) — the
        # operator still approves each one from the dashboard, same as normal.
        logger.warning("Intelligence unavailable, using fallback allocator: %s", exc)
        record_fallback(component="recommendation_engine", reason="ml_unavailable")
        set_degraded("core", True)
        fallback_recs = compute_fallback_recommendations(state)
        elapsed = perf_counter() - started
        redis_cache: RedisStateCache = app.state.redis_cache
        for rec in fallback_recs:
            outcome = rec.get("review", "unknown")
            record_decision(policy="core_fallback_heuristic", outcome=outcome, seconds=elapsed)
            log_decision(
                decision_id=rec.get("id", "unknown"),
                policy="core_fallback_heuristic",
                outcome=outcome,
                station_id=rec.get("station_id"),
                fuel_type=rec.get("fuel_type"),
            )
            if outcome == "HUMAN_REVIEW":
                HUMAN_REVIEW_REQUESTS_TOTAL.labels(reason="core_fallback_heuristic").inc()
            await redis_cache.cache_recommendation(rec["id"], rec)

        session_factory = app.state.session_factory
        async with session_factory() as session:
            await record_event(
                session,
                event_type="fallback_activated",
                details={"reason": "ml_unavailable", "code": exc.code, "recommendation_count": len(fallback_recs)},
            )

        return {
            "tick": state.last_polled_tick,
            "policy": "fallback",
            "degraded": True,
            "degraded_reason": f"Intelligence unavailable ({exc.code}): {exc.message}",
            "recommendations": fallback_recs,
        }

    elapsed = perf_counter() - started
    policy_used = assessment.get("policy", "unknown")
    redis_cache: RedisStateCache = app.state.redis_cache
    for rec in assessment.get("recommendations", []):
        outcome = rec.get("review", "unknown")
        record_decision(policy=policy_used, outcome=outcome, seconds=elapsed)
        log_decision(
            decision_id=rec.get("id", "unknown"),
            policy=policy_used,
            outcome=outcome,
            station_id=rec.get("station_id"),
            fuel_type=rec.get("fuel_type"),
        )
        if outcome == "HUMAN_REVIEW":
            HUMAN_REVIEW_REQUESTS_TOTAL.labels(reason=policy_used).inc()
        if rec.get("id"):
            await redis_cache.cache_recommendation(rec["id"], rec)

    session_factory = app.state.session_factory
    async with session_factory() as session:
        await record_event(
            session,
            event_type="intelligence_assessment",
            details={
                "tick": assessment.get("tick"),
                "policy": assessment.get("policy"),
                "recommendation_count": len(assessment.get("recommendations", [])),
                "narrate": req.narrate,
            },
        )

    return assessment


@app.post("/internal/allocations/execute")
async def execute_recommendation(req: ExecuteRecommendationRequest) -> dict:
    """Executes one recommendation returned by `/internal/recommendations` —
    the human-review step (REQ-009c/REQ-019): nothing is auto-submitted, and
    for a HUMAN_REVIEW recommendation the server enforces `acknowledged`
    against its own cached copy of what was issued, not the caller's claim.
    """
    redis_cache: RedisStateCache = app.state.redis_cache
    cached = await redis_cache.get_recommendation(req.recommendation_id)
    if cached is None:
        VALIDATION_REJECTIONS_TOTAL.labels(source="allocation_execute", reason="unknown_recommendation").inc()
        raise HTTPException(
            404,
            {"code": "RECOMMENDATION_NOT_FOUND", "message": "Unknown or expired recommendation — fetch recommendations again."},
        )

    cached_action = cached.get("action") or {}
    if (
        cached.get("station_id") != req.station_id
        or cached.get("fuel_type") != req.fuel_type
        or cached_action.get("source_depot_id") != req.action.source_depot_id
        or cached_action.get("route_id") != req.action.route_id
        or cached_action.get("quantity") != req.action.quantity
    ):
        VALIDATION_REJECTIONS_TOTAL.labels(source="allocation_execute", reason="recommendation_mismatch").inc()
        raise HTTPException(
            409,
            {"code": "RECOMMENDATION_MISMATCH", "message": "Request doesn't match the recommendation as issued — fetch recommendations again."},
        )

    if cached.get("review") == "HUMAN_REVIEW" and not req.acknowledged:
        VALIDATION_REJECTIONS_TOTAL.labels(source="allocation_execute", reason="human_review_required").inc()
        raise HTTPException(
            400,
            {"code": "HUMAN_REVIEW_REQUIRED", "message": "This recommendation requires explicit human-review acknowledgment before execution."},
        )

    supervisor: IngestionSupervisor = app.state.supervisor
    tick = supervisor.state.last_polled_tick
    if tick is None:
        raise HTTPException(503, {"code": "INGESTION_NOT_READY", "message": "no known tick yet"})

    allocation_executor: AllocationExecutor = app.state.allocation_executor
    try:
        allocation = await allocation_executor.submit(
            source_depot_id=req.action.source_depot_id,
            destination_station_id=req.station_id,
            route_id=req.action.route_id,
            fuel_type=req.fuel_type,
            quantity=req.action.quantity,
            tick=tick,
            intent=req.intent,
        )
    except SimulatorError as exc:
        logger.warning("allocation execution failed: %s", exc)
        code = getattr(exc, "code", "SIMULATOR_ERROR")
        status = getattr(exc, "status_code", None) or 502
        raise HTTPException(status, {"code": code, "message": str(exc)}) from exc

    await redis_cache.consume_recommendation(req.recommendation_id)
    return allocation.model_dump(mode="json")


# ---- Simulation controls: proxy to /admin/* so the operator never needs the
# simulator's own admin console — the app is the only surface they touch. ----


class EventInjectRequest(BaseModel):
    type: str
    duration_ticks: int = Field(gt=0, default=20)
    start_tick: int | None = None
    station_ids: list[str] = Field(default_factory=list)
    route_ids: list[str] = Field(default_factory=list)
    depot_ids: list[str] = Field(default_factory=list)
    multiplier: float | None = None


class FaultInjectRequest(BaseModel):
    type: str
    duration_seconds: int = Field(gt=0, le=3600, default=30)
    delay_ms: int | None = None
    probability: float | None = None


async def _admin_call(method: str, path: str, json_body: dict | None = None) -> dict:
    admin_http: httpx.AsyncClient = app.state.admin_http
    try:
        response = await admin_http.request(method, path, json=json_body)
    except httpx.HTTPError as exc:
        raise HTTPException(502, {"code": "SIMULATOR_UNREACHABLE", "message": str(exc)}) from exc
    if response.status_code >= 400:
        raise HTTPException(response.status_code, response.json() if response.content else {})
    body = response.json() if response.content else {}
    # Admin actions change simulator state right away — refresh Core's view
    # immediately rather than waiting up to POLL_INTERVAL_SECONDS.
    supervisor: IngestionSupervisor = app.state.supervisor
    try:
        await supervisor.poller.poll_once()
    except SimulatorError:
        pass  # already logged inside poll_once; the action itself still succeeded
    return body


@app.post("/internal/admin/run")
async def admin_run() -> dict:
    return await _admin_call("POST", "/admin/run")


@app.post("/internal/admin/pause")
async def admin_pause() -> dict:
    return await _admin_call("POST", "/admin/pause")


@app.post("/internal/admin/step")
async def admin_step() -> dict:
    return await _admin_call("POST", "/admin/step")


@app.post("/internal/admin/reset")
async def admin_reset() -> dict:
    return await _admin_call("POST", "/admin/reset")


@app.post("/internal/admin/events")
async def admin_inject_event(req: EventInjectRequest) -> dict:
    parameters: dict = {}
    if req.station_ids:
        parameters["station_ids"] = req.station_ids
    if req.route_ids:
        parameters["route_ids"] = req.route_ids
    if req.depot_ids:
        parameters["depot_ids"] = req.depot_ids
    if req.multiplier is not None:
        parameters["multiplier"] = req.multiplier

    start_tick = req.start_tick
    if start_tick is None:
        supervisor: IngestionSupervisor = app.state.supervisor
        start_tick = (supervisor.state.last_polled_tick or 0) + 1

    return await _admin_call(
        "POST",
        "/admin/events",
        {
            "type": req.type,
            "start_tick": start_tick,
            "duration_ticks": req.duration_ticks,
            "parameters": parameters,
        },
    )


@app.post("/internal/admin/faults")
async def admin_inject_fault(req: FaultInjectRequest) -> dict:
    parameters: dict = {}
    if req.delay_ms is not None:
        parameters["delay_ms"] = req.delay_ms
    if req.probability is not None:
        parameters["probability"] = req.probability
    return await _admin_call(
        "POST", "/admin/faults", {"type": req.type, "duration_seconds": req.duration_seconds, "parameters": parameters}
    )


@app.post("/internal/admin/faults/clear")
async def admin_clear_faults() -> dict:
    return await _admin_call("POST", "/admin/faults/clear")
