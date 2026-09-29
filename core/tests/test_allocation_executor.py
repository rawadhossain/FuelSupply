import asyncio
import json

import httpx
from app.allocation_executor import AllocationExecutor, make_idempotency_key
from app.simulator_client import SimulatorClient, SimulatorRequestError

ALLOCATION_RECORD = {
    "id": 1,
    "idempotency_key": "alloc-placeholder",
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


def test_make_idempotency_key_is_deterministic() -> None:
    base = {
        "source_depot_id": "depot-gazipur",
        "destination_station_id": "station-mirpur",
        "route_id": "route-gazipur-mirpur",
        "fuel_type": "DIESEL",
        "quantity": 1000.0,
        "tick": 15,
        "intent": "heuristic",
    }
    key_a = make_idempotency_key(**base, attempt=0)
    key_b = make_idempotency_key(**base, attempt=0)
    assert key_a == key_b

    key_diff_quantity = make_idempotency_key(**{**base, "quantity": 2000.0}, attempt=0)
    assert key_diff_quantity != key_a

    key_diff_attempt = make_idempotency_key(**base, attempt=1)
    assert key_diff_attempt != key_a

    assert 1 <= len(key_a) <= 150


def test_submit_sends_deterministic_key_and_confirms_via_followup_get() -> None:
    seen_request_body: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/v1/allocations":
            seen_request_body.update(json.loads(request.content))
            record = {**ALLOCATION_RECORD, "idempotency_key": seen_request_body["idempotency_key"]}
            return httpx.Response(201, json=record)
        if request.method == "GET" and request.url.path == "/v1/allocations":
            record = {**ALLOCATION_RECORD, "idempotency_key": seen_request_body.get("idempotency_key", "")}
            return httpx.Response(200, json=[record])
        raise AssertionError(f"unexpected request: {request.method} {request.url.path}")

    client = SimulatorClient(base_url="http://simulator.test", transport=httpx.MockTransport(handler))
    executor = AllocationExecutor(client)

    expected_key = make_idempotency_key(
        source_depot_id="depot-gazipur",
        destination_station_id="station-mirpur",
        route_id="route-gazipur-mirpur",
        fuel_type="DIESEL",
        quantity=1000.0,
        tick=15,
        intent="heuristic",
        attempt=0,
    )

    allocation = asyncio.run(
        executor.submit(
            source_depot_id="depot-gazipur",
            destination_station_id="station-mirpur",
            route_id="route-gazipur-mirpur",
            fuel_type="DIESEL",
            quantity=1000.0,
            tick=15,
            intent="heuristic",
        )
    )

    assert seen_request_body["idempotency_key"] == expected_key
    assert allocation.id == 1
    assert allocation.status.value == "PENDING"


def test_cancel_confirms_via_followup_get() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/v1/allocations/1/cancel":
            return httpx.Response(200, json={**ALLOCATION_RECORD, "status": "CANCELLED"})
        if request.method == "GET" and request.url.path == "/v1/allocations":
            return httpx.Response(200, json=[{**ALLOCATION_RECORD, "status": "CANCELLED"}])
        raise AssertionError(f"unexpected request: {request.method} {request.url.path}")

    client = SimulatorClient(base_url="http://simulator.test", transport=httpx.MockTransport(handler))
    executor = AllocationExecutor(client)

    allocation = asyncio.run(executor.cancel(1))
    assert allocation.status.value == "CANCELLED"


def test_submit_propagates_business_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            409,
            json={"detail": {"code": "ROUTE_MISMATCH", "message": "Route does not connect selected depot and station"}},
        )

    client = SimulatorClient(base_url="http://simulator.test", transport=httpx.MockTransport(handler))
    executor = AllocationExecutor(client)

    try:
        asyncio.run(
            executor.submit(
                source_depot_id="depot-gazipur",
                destination_station_id="station-mirpur",
                route_id="route-patiya-coxsbazar",
                fuel_type="DIESEL",
                quantity=1000.0,
                tick=15,
                intent="heuristic",
            )
        )
        raise AssertionError("expected SimulatorRequestError")
    except SimulatorRequestError as exc:
        assert exc.code == "ROUTE_MISMATCH"
