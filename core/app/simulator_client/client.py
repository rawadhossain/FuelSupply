"""Typed async client for the BUP Fuel Supply Simulator's `/v1/*` read surface.

Single-shot requests only — no retry/backoff here, that's TASK-030's circuit
breaker layered on top. Every read is validated against the shared Pydantic
models (shared/fuelsupply_shared/models.py, verified live); a shape mismatch
raises SimulatorInvalidResponseError rather than passing untrusted data
downstream (REQ-009b).
"""

from __future__ import annotations

from typing import Generic, Self, TypeVar

import httpx
from fuelsupply_shared.models import (
    Allocation,
    AllocationRequest,
    DemandHistoryEntry,
    Depot,
    DomainEvent,
    HealthResponse,
    InstanceState,
    Region,
    Route,
    SimulatorMetrics,
    Station,
    SupplyArrival,
)
from pydantic import BaseModel, ValidationError

from .errors import (
    SimulatorError,
    SimulatorInvalidResponseError,
    SimulatorRequestError,
    SimulatorUnavailableError,
)

T = TypeVar("T")


class SimulatorResponse(Generic[T]):
    """A parsed simulator read plus the `X-Simulator-Stale` flag (REQ-010).

    Callers must check `.stale` before treating `.data` as fresh — the
    simulator can serve stale data without failing the request.
    """

    __slots__ = ("data", "stale")

    def __init__(self, data: T, stale: bool) -> None:
        self.data = data
        self.stale = stale


def _parse_error(response: httpx.Response) -> SimulatorError:
    try:
        body = response.json()
    except ValueError:
        body = None

    if isinstance(body, dict) and "error" in body:
        err = body["error"] or {}
        return SimulatorUnavailableError(
            code=err.get("code", "UNKNOWN"),
            message=err.get("message", response.text),
            status_code=response.status_code,
        )
    if isinstance(body, dict) and "detail" in body:
        detail = body["detail"]
        if isinstance(detail, dict):
            return SimulatorRequestError(
                code=detail.get("code", "UNKNOWN"),
                message=detail.get("message", ""),
                status_code=response.status_code,
            )
        # FastAPI's own pydantic-validation shape for a malformed request we sent: {"detail": [...]}.
        return SimulatorRequestError(
            code="VALIDATION_ERROR", message=str(detail), status_code=response.status_code
        )
    return SimulatorUnavailableError(
        code="UNKNOWN",
        message=f"Unexpected {response.status_code} response: {response.text[:200]}",
        status_code=response.status_code,
    )


class SimulatorClient:
    def __init__(
        self, base_url: str, timeout: float = 5.0, transport: httpx.BaseTransport | None = None
    ) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=timeout, transport=transport)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def _get(self, path: str, params: dict[str, object] | None = None) -> SimulatorResponse[object]:
        try:
            response = await self._client.get(path, params=params)
        except httpx.TimeoutException as exc:
            raise SimulatorUnavailableError(code="TIMEOUT", message=str(exc)) from exc
        except httpx.TransportError as exc:
            raise SimulatorUnavailableError(code="CONNECTION_ERROR", message=str(exc)) from exc

        if response.status_code >= 400:
            raise _parse_error(response)

        stale = response.headers.get("X-Simulator-Stale", "").lower() == "true"
        try:
            body = response.json()
        except ValueError as exc:
            # 2xx but not even valid JSON (e.g. chaos-proxy corrupt_json mode) —
            # same REQ-009b path as a schema mismatch, not an unhandled 500.
            raise SimulatorInvalidResponseError(
                endpoint=path, raw_body=response.text[:500], validation_error=exc
            ) from exc
        return SimulatorResponse(data=body, stale=stale)

    async def _get_model(
        self, path: str, model: type[BaseModel], params: dict[str, object] | None = None
    ) -> SimulatorResponse[BaseModel]:
        raw = await self._get(path, params=params)
        try:
            parsed = model.model_validate(raw.data)
        except ValidationError as exc:
            raise SimulatorInvalidResponseError(
                endpoint=path, raw_body=raw.data, validation_error=exc
            ) from exc
        return SimulatorResponse(data=parsed, stale=raw.stale)

    async def _get_model_list(
        self, path: str, model: type[BaseModel], params: dict[str, object] | None = None
    ) -> SimulatorResponse[list[BaseModel]]:
        raw = await self._get(path, params=params)
        try:
            parsed = [model.model_validate(item) for item in raw.data]
        except ValidationError as exc:
            raise SimulatorInvalidResponseError(
                endpoint=path, raw_body=raw.data, validation_error=exc
            ) from exc
        return SimulatorResponse(data=parsed, stale=raw.stale)

    async def _post(self, path: str, json_body: dict[str, object]) -> SimulatorResponse[object]:
        try:
            response = await self._client.post(path, json=json_body)
        except httpx.TimeoutException as exc:
            raise SimulatorUnavailableError(code="TIMEOUT", message=str(exc)) from exc
        except httpx.TransportError as exc:
            raise SimulatorUnavailableError(code="CONNECTION_ERROR", message=str(exc)) from exc

        if response.status_code >= 400:
            raise _parse_error(response)

        stale = response.headers.get("X-Simulator-Stale", "").lower() == "true"
        try:
            body = response.json()
        except ValueError as exc:
            raise SimulatorInvalidResponseError(
                endpoint=path, raw_body=response.text[:500], validation_error=exc
            ) from exc
        return SimulatorResponse(data=body, stale=stale)

    async def _post_model(
        self, path: str, model: type[BaseModel], json_body: dict[str, object]
    ) -> SimulatorResponse[BaseModel]:
        raw = await self._post(path, json_body)
        try:
            parsed = model.model_validate(raw.data)
        except ValidationError as exc:
            raise SimulatorInvalidResponseError(
                endpoint=path, raw_body=raw.data, validation_error=exc
            ) from exc
        return SimulatorResponse(data=parsed, stale=raw.stale)

    # ---- /v1/* reads --------------------------------------------------

    async def get_health(self) -> SimulatorResponse[HealthResponse]:
        return await self._get_model("/v1/health", HealthResponse)

    async def get_instance(self) -> SimulatorResponse[InstanceState]:
        return await self._get_model("/v1/instance", InstanceState)

    async def get_regions(self) -> SimulatorResponse[list[Region]]:
        return await self._get_model_list("/v1/regions", Region)

    async def get_depots(self) -> SimulatorResponse[list[Depot]]:
        return await self._get_model_list("/v1/depots", Depot)

    async def get_depot(self, depot_id: str) -> SimulatorResponse[Depot]:
        return await self._get_model(f"/v1/depots/{depot_id}", Depot)

    async def get_stations(self) -> SimulatorResponse[list[Station]]:
        return await self._get_model_list("/v1/stations", Station)

    async def get_station(self, station_id: str) -> SimulatorResponse[Station]:
        return await self._get_model(f"/v1/stations/{station_id}", Station)

    async def get_routes(self) -> SimulatorResponse[list[Route]]:
        return await self._get_model_list("/v1/routes", Route)

    async def get_supply_arrivals(self) -> SimulatorResponse[list[SupplyArrival]]:
        return await self._get_model_list("/v1/supply-arrivals", SupplyArrival)

    async def get_events(self) -> SimulatorResponse[list[DomainEvent]]:
        return await self._get_model_list("/v1/events", DomainEvent)

    async def get_allocations(self) -> SimulatorResponse[list[Allocation]]:
        return await self._get_model_list("/v1/allocations", Allocation)

    async def get_demand_history(
        self, station_id: str | None = None, limit: int = 200
    ) -> SimulatorResponse[list[DemandHistoryEntry]]:
        # RISK-006: the table grows unboundedly — never call without an explicit,
        # bounded limit, even though the simulator also clamps server-side.
        clamped_limit = max(1, min(limit, 2000))
        params: dict[str, object] = {"limit": clamped_limit}
        if station_id is not None:
            params["station_id"] = station_id
        return await self._get_model_list("/v1/demand-history", DemandHistoryEntry, params=params)

    async def get_metrics(self) -> SimulatorResponse[SimulatorMetrics]:
        return await self._get_model("/v1/metrics", SimulatorMetrics)

    # ---- /v1/allocations write path — the only domain write (CONTRACT-SIM-ALLOC) ----

    async def post_allocation(self, request: AllocationRequest) -> SimulatorResponse[Allocation]:
        # Both 200 and 201 are treated as success here (ASM-010: live image
        # returns 201 on both first submission and idempotent replay).
        return await self._post_model(
            "/v1/allocations", Allocation, request.model_dump(mode="json")
        )

    async def post_cancel_allocation(self, allocation_id: int) -> SimulatorResponse[Allocation]:
        return await self._post_model(
            f"/v1/allocations/{allocation_id}/cancel", Allocation, {}
        )
