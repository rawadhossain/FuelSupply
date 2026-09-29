"""Consumes `/v1/stream` as an advisory-only wake-up signal.

Hard rule: no field is ever read out of an SSE payload as authoritative
state. `on_event` is called with (event_name, payload) purely to trigger a
REST re-GET; `payload` is inspected here only to track the `simulation.tick`
number for gap detection, never for business data.

Handles: no replay on reconnect (every reconnect is treated as "state
unknown"), 15s keepalive comments, and a `stream_disconnect` fault (503
instead of an open stream) — all via the same backoff-and-retry loop, since
from this listener's point of view they look the same: the connection didn't
give us events, so back off and try again.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

import httpx
from httpx_sse import aconnect_sse

logger = logging.getLogger(__name__)

OnEvent = Callable[[str, dict], Awaitable[None]]


class SSEListener:
    def __init__(
        self,
        base_url: str,
        on_event: OnEvent,
        transport: httpx.BaseTransport | None = None,
        max_backoff_seconds: float = 30.0,
    ) -> None:
        self._base_url = base_url
        self._on_event = on_event
        self._transport = transport
        self._max_backoff = max_backoff_seconds
        self.connected = False
        self.last_event_tick: int | None = None
        self._stop = False
        self._current_task: asyncio.Task | None = None

    def stop(self) -> None:
        self._stop = True
        if self._current_task is not None:
            self._current_task.cancel()

    def force_reconnect(self) -> None:
        """Cancel the current connection so the reconnect loop opens a fresh one.

        Used when a tick-gap check suspects the per-subscriber queue silently
        overflowed (200-event cap, no server-side signal) — the connection can
        look alive while no longer delivering events.
        """
        if self._current_task is not None:
            self._current_task.cancel()

    async def run_forever(self) -> None:
        backoff = 1.0
        while not self._stop:
            self._current_task = asyncio.ensure_future(self._connect_and_listen())
            try:
                await self._current_task
                backoff = 1.0  # clean return (server closed normally)
            except asyncio.CancelledError:
                backoff = 1.0  # deliberate stop() or force_reconnect() — retry immediately
                if self._stop:
                    break
            except Exception as exc:  # noqa: BLE001 - listener must survive any connection failure
                self.connected = False
                logger.warning("SSE connection lost, reconnecting in %.1fs: %s", backoff, exc)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, self._max_backoff)
        self.connected = False

    async def _connect_and_listen(self) -> None:
        timeout = httpx.Timeout(connect=5.0, read=20.0, write=5.0, pool=5.0)
        async with httpx.AsyncClient(
            base_url=self._base_url, transport=self._transport, timeout=timeout
        ) as client, aconnect_sse(client, "GET", "/v1/stream") as event_source:
            self.connected = True
            async for sse in event_source.aiter_sse():
                if not sse.event:
                    continue  # keepalive comment
                try:
                    payload = sse.json()
                except ValueError:
                    payload = {}
                if sse.event == "simulation.tick":
                    tick = payload.get("tick")
                    if isinstance(tick, int):
                        self.last_event_tick = tick
                await self._on_event(sse.event, payload)
        self.connected = False
