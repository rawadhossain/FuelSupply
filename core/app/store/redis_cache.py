"""Latest-state snapshot cache in Redis.

Deliberately minimal: a single overwritten key with the latest ingestion
summary, not a pub/sub fan-out (that's TASK-020/021's job once the dashboard
WebSocket exists to consume it, and Redis itself is first in line to cut per
`docs/cut-list.md` if time runs short — this stays small on purpose).
"""

from __future__ import annotations

import json

import redis.asyncio as redis

from app.ingestion.state import NetworkState

STATE_KEY = "fuelsupply:network_state"


class RedisStateCache:
    def __init__(self, redis_url: str) -> None:
        self._client = redis.from_url(redis_url, decode_responses=True)

    async def write_snapshot(self, state: NetworkState) -> None:
        snapshot = {
            "tick": state.last_polled_tick,
            "polled_at": state.last_poll_at.isoformat() if state.last_poll_at else None,
            "poll_error": state.last_poll_error,
            "sse_connected": state.sse_connected,
            "counts": {
                "regions": len(state.regions.data) if state.regions else None,
                "depots": len(state.depots.data) if state.depots else None,
                "stations": len(state.stations.data) if state.stations else None,
                "routes": len(state.routes.data) if state.routes else None,
                "supply_arrivals": len(state.supply_arrivals.data) if state.supply_arrivals else None,
                "events": len(state.events.data) if state.events else None,
                "allocations": len(state.allocations.data) if state.allocations else None,
            },
        }
        await self._client.set(STATE_KEY, json.dumps(snapshot))

    async def read_snapshot(self) -> dict | None:
        raw = await self._client.get(STATE_KEY)
        return json.loads(raw) if raw else None

    async def aclose(self) -> None:
        await self._client.aclose()
