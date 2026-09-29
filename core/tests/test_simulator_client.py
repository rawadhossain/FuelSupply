import asyncio

import httpx
from app.simulator_client import (
    SimulatorClient,
    SimulatorInvalidResponseError,
    SimulatorRequestError,
    SimulatorUnavailableError,
)


def make_client(handler) -> SimulatorClient:
    transport = httpx.MockTransport(handler)
    return SimulatorClient(base_url="http://simulator.test", transport=transport)


def test_get_depots_parses_real_shape() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/depots"
        return httpx.Response(
            200,
            json=[
                {
                    "id": "depot-gazipur",
                    "name": "Gazipur Depot",
                    "region_id": "region-dhaka",
                    "status": "OPEN",
                    "dispatch_capacity_per_tick": 12000.0,
                    "capacity": {"DIESEL": 90000, "PETROL": 70000, "OCTANE": 45000},
                    "inventory": {"DIESEL": 60000, "PETROL": 45000, "OCTANE": 26000},
                }
            ],
        )

    async def scenario():
        async with make_client(handler) as client:
            return await client.get_depots()

    result = asyncio.run(scenario())
    assert result.stale is False
    assert len(result.data) == 1
    assert result.data[0].id == "depot-gazipur"
    assert result.data[0].inventory.DIESEL == 60000


def test_stale_header_is_surfaced() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[], headers={"X-Simulator-Stale": "true"})

    async def scenario():
        async with make_client(handler) as client:
            return await client.get_regions()

    result = asyncio.run(scenario())
    assert result.stale is True


def test_fault_injected_503_raises_unavailable_with_error_envelope() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            503,
            json={
                "error": {
                    "code": "FAULT_INJECTED",
                    "message": "Simulator API temporarily unavailable.",
                }
            },
        )

    async def scenario():
        async with make_client(handler) as client:
            await client.get_depots()

    try:
        asyncio.run(scenario())
        raise AssertionError("expected SimulatorUnavailableError")
    except SimulatorUnavailableError as exc:
        assert exc.code == "FAULT_INJECTED"
        assert exc.status_code == 503


def test_business_error_409_raises_request_error_with_detail_envelope() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            409,
            json={
                "detail": {
                    "code": "ROUTE_MISMATCH",
                    "message": "Route does not connect selected depot and station",
                }
            },
        )

    async def scenario():
        async with make_client(handler) as client:
            await client.get_depot("depot-gazipur")

    try:
        asyncio.run(scenario())
        raise AssertionError("expected SimulatorRequestError")
    except SimulatorRequestError as exc:
        assert exc.code == "ROUTE_MISMATCH"
        assert exc.status_code == 409


def test_malformed_payload_raises_invalid_response_not_silently_accepted() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        # Missing every required field — must be rejected, not passed downstream.
        return httpx.Response(200, json=[{"unexpected": "shape"}])

    async def scenario():
        async with make_client(handler) as client:
            await client.get_depots()

    try:
        asyncio.run(scenario())
        raise AssertionError("expected SimulatorInvalidResponseError")
    except SimulatorInvalidResponseError:
        pass


def test_demand_history_limit_is_clamped_to_documented_bounds() -> None:
    seen_params: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen_params.update(dict(request.url.params))
        return httpx.Response(200, json=[])

    async def scenario():
        async with make_client(handler) as client:
            await client.get_demand_history(station_id="station-mirpur", limit=999999)
            assert seen_params["limit"] == "2000"
            await client.get_demand_history(station_id="station-mirpur", limit=0)
            assert seen_params["limit"] == "1"

    asyncio.run(scenario())


def test_connection_failure_raises_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    async def scenario():
        async with make_client(handler) as client:
            await client.get_instance()

    try:
        asyncio.run(scenario())
        raise AssertionError("expected SimulatorUnavailableError")
    except SimulatorUnavailableError as exc:
        assert exc.code == "CONNECTION_ERROR"
