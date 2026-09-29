"""Composition root for ingestion: owns the Poller, the SSEListener, and the
shared NetworkState they both update.

Every SSE event triggers an extra immediate poll (re-GET) on top of the
periodic poll loop that keeps running regardless — so state stays fresh on
REST polling alone if SSE is down entirely (hard rule). A background watchdog
compares the real tick (from REST) against the last tick seen over SSE; if
SSE claims to be connected but has fallen behind, the queue likely overflowed
silently (200-event cap, no replay) and the connection is force-restarted.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

from app.simulator_client import SimulatorClient

from .poller import OnPollSuccess, Poller
from .sse_listener import SSEListener
from .state import NetworkState

logger = logging.getLogger(__name__)


class IngestionSupervisor:
    def __init__(
        self,
        client: SimulatorClient,
        sse_base_url: str,
        sse_transport: httpx.BaseTransport | None = None,
        poll_interval_seconds: float = 5.0,
        gap_threshold_ticks: int = 2,
        gap_check_interval_seconds: float = 5.0,
        on_poll_success: OnPollSuccess | None = None,
    ) -> None:
        self.state = NetworkState()
        self.poller = Poller(client, self.state, on_poll_success=on_poll_success)
        self.sse_listener = SSEListener(
            base_url=sse_base_url, on_event=self._on_sse_event, transport=sse_transport
        )
        self.poll_interval_seconds = poll_interval_seconds
        self.gap_threshold_ticks = gap_threshold_ticks
        self.gap_check_interval_seconds = gap_check_interval_seconds
        self._tasks: list[asyncio.Task] = []
        self._stopped = False

    async def _on_sse_event(self, event: str, payload: dict) -> None:
        try:
            await self.poller.poll_once()
        except Exception as exc:  # noqa: BLE001 - already logged in poll_once; SSE loop must survive it
            logger.debug("re-GET after SSE event %r failed (already logged): %s", event, exc)
        self.state.last_sse_event_tick = self.sse_listener.last_event_tick
        self.state.sse_connected = self.sse_listener.connected

    def _gap_exists(self) -> bool:
        real_tick = self.state.last_polled_tick
        sse_tick = self.sse_listener.last_event_tick
        return (
            self.sse_listener.connected
            and real_tick is not None
            and sse_tick is not None
            and real_tick - sse_tick > self.gap_threshold_ticks
        )

    async def _watch_for_gaps(self) -> None:
        while not self._stopped:
            await asyncio.sleep(self.gap_check_interval_seconds)
            self.state.sse_connected = self.sse_listener.connected
            if self._gap_exists():
                logger.warning(
                    "SSE tick gap detected (real tick=%s, last SSE tick=%s) — forcing reconnect",
                    self.state.last_polled_tick,
                    self.sse_listener.last_event_tick,
                )
                self.sse_listener.force_reconnect()

    async def run(self) -> None:
        self._tasks = [
            asyncio.create_task(self.poller.run_forever(self.poll_interval_seconds)),
            asyncio.create_task(self.sse_listener.run_forever()),
            asyncio.create_task(self._watch_for_gaps()),
        ]
        await asyncio.gather(*self._tasks)

    async def stop(self) -> None:
        self._stopped = True
        self.sse_listener.stop()
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
