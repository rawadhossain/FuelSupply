import asyncio

import httpx
from app.intelligence_client import (
    IntelligenceClient,
    IntelligenceInvalidInputError,
    IntelligenceUnavailableError,
)


def make_client(handler) -> IntelligenceClient:
    transport = httpx.MockTransport(handler)
    return IntelligenceClient(base_url="http://intelligence.test", transport=transport)


def test_assess_success_returns_parsed_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/intel/assess"
        return httpx.Response(200, json={"tick": 42, "policy": "heuristic", "recommendations": []})

    async def scenario():
        async with make_client(handler) as client:
            return await client.assess(snapshot={"instance": {"tick": 42}})

    result = asyncio.run(scenario())
    assert result == {"tick": 42, "policy": "heuristic", "recommendations": []}


def test_model_unavailable_503_raises_unavailable_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            503,
            json={
                "detail": {
                    "code": "MODEL_UNAVAILABLE",
                    "message": "model not loaded",
                    "fallback": "Core should run intelligence.heuristic.plan",
                }
            },
        )

    async def scenario():
        async with make_client(handler) as client:
            await client.assess(snapshot={})

    try:
        asyncio.run(scenario())
        raise AssertionError("expected IntelligenceUnavailableError")
    except IntelligenceUnavailableError as exc:
        assert exc.code == "MODEL_UNAVAILABLE"
        assert exc.status_code == 503


def test_invalid_snapshot_422_raises_invalid_input_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            422,
            json={"detail": {"code": "INVALID_SNAPSHOT", "message": "bad snapshot", "alert": True}},
        )

    async def scenario():
        async with make_client(handler) as client:
            await client.assess(snapshot={})

    try:
        asyncio.run(scenario())
        raise AssertionError("expected IntelligenceInvalidInputError")
    except IntelligenceInvalidInputError as exc:
        assert exc.code == "INVALID_SNAPSHOT"
        assert exc.status_code == 422


def test_connection_failure_raises_unavailable_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    async def scenario():
        async with make_client(handler) as client:
            await client.assess(snapshot={})

    try:
        asyncio.run(scenario())
        raise AssertionError("expected IntelligenceUnavailableError")
    except IntelligenceUnavailableError as exc:
        assert exc.code == "CONNECTION_ERROR"
