"""Async client for the Intelligence service's `/intel/*` API.

Single-shot, no retry/backoff (same design choice as `simulator_client`:
resilience layering is a separate concern, TASK-030). Every call is a plain
HTTP round trip against `POST /intel/assess` et al.
"""

from __future__ import annotations

from typing import Self

import httpx

from .errors import IntelligenceError, IntelligenceInvalidInputError, IntelligenceUnavailableError


def _parse_error(response: httpx.Response) -> IntelligenceError:
    try:
        body = response.json()
    except ValueError:
        body = None

    detail = body.get("detail") if isinstance(body, dict) else None
    code = detail.get("code", "UNKNOWN") if isinstance(detail, dict) else "UNKNOWN"
    message = detail.get("message", response.text[:200]) if isinstance(detail, dict) else response.text[:200]

    if response.status_code == 422:
        return IntelligenceInvalidInputError(code=code, message=message, status_code=422)
    return IntelligenceUnavailableError(code=code, message=message, status_code=response.status_code)


class IntelligenceClient:
    def __init__(
        self, base_url: str, timeout: float = 15.0, transport: httpx.BaseTransport | None = None
    ) -> None:
        # Longer default timeout than the simulator client: an LP solve or an
        # LLM narration call can take real time (RES-006/RES-008 benchmarks:
        # LP ~120-130ms, OpenAI calls ~2-3s).
        self._client = httpx.AsyncClient(base_url=base_url, timeout=timeout, transport=transport)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def assess(
        self,
        snapshot: dict,
        demand_rows: list[dict] | None = None,
        stale: bool = False,
        policy: str = "heuristic",
        narrate: bool = False,
    ) -> dict:
        body = {
            "snapshot": snapshot,
            "demand_rows": demand_rows or [],
            "stale": stale,
            "policy": policy,
            "narrate": narrate,
        }
        try:
            response = await self._client.post("/intel/assess", json=body)
        except httpx.TimeoutException as exc:
            raise IntelligenceUnavailableError(code="TIMEOUT", message=str(exc)) from exc
        except httpx.TransportError as exc:
            raise IntelligenceUnavailableError(code="CONNECTION_ERROR", message=str(exc)) from exc

        if response.status_code >= 400:
            raise _parse_error(response)
        return response.json()

    async def health(self) -> dict:
        try:
            response = await self._client.get("/health")
        except httpx.TimeoutException as exc:
            raise IntelligenceUnavailableError(code="TIMEOUT", message=str(exc)) from exc
        except httpx.TransportError as exc:
            raise IntelligenceUnavailableError(code="CONNECTION_ERROR", message=str(exc)) from exc
        # /health itself returns 503 with a body when degraded — that's a
        # valid, parseable answer here, not an error to raise.
        return response.json()
