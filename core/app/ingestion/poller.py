"""Periodic REST polling — the baseline freshness mechanism.

Runs continuously regardless of SSE health, so the platform keeps working on
polling alone if SSE is unavailable (hard rule). A failed poll leaves the
previous state untouched — last-known-good, per REQ-009d — rather than
partially overwriting it with a mix of fresh and missing fields.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from app.simulator_client import SimulatorClient, SimulatorError

from .state import NetworkState

logger = logging.getLogger(__name__)

OnPollSuccess = Callable[[NetworkState], Awaitable[None]]


class Poller:
    def __init__(
        self,
        client: SimulatorClient,
        state: NetworkState,
        on_poll_success: OnPollSuccess | None = None,
    ) -> None:
        self._client = client
        self.state = state
        self._on_poll_success = on_poll_success

    async def poll_once(self) -> None:
        try:
            instance = await self._client.get_instance()
            regions = await self._client.get_regions()
            depots = await self._client.get_depots()
            stations = await self._client.get_stations()
            routes = await self._client.get_routes()
            supply_arrivals = await self._client.get_supply_arrivals()
            events = await self._client.get_events()
            allocations = await self._client.get_allocations()
            metrics = await self._client.get_metrics()
        except SimulatorError as exc:
            self.state.mark_poll_error(str(exc))
            logger.warning("poll_once failed, keeping last-known-good state: %s", exc)
            raise

        self.state.instance = instance
        self.state.regions = regions
        self.state.depots = depots
        self.state.stations = stations
        self.state.routes = routes
        self.state.supply_arrivals = supply_arrivals
        self.state.events = events
        self.state.allocations = allocations
        self.state.metrics = metrics
        self.state.mark_poll_success()

        if self._on_poll_success is not None:
            try:
                await self._on_poll_success(self.state)
            except Exception:
                logger.exception("on_poll_success hook failed")

    async def run_forever(self, interval_seconds: float = 5.0) -> None:
        while True:
            try:
                await self.poll_once()
            except SimulatorError:
                pass  # already logged and recorded on state; try again next cycle
            await asyncio.sleep(interval_seconds)
