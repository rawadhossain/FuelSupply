import asyncio
import types

import httpx
from app.ingestion import IngestionSupervisor
from app.ingestion.poller import Poller
from app.ingestion.sse_listener import SSEListener
from app.ingestion.state import NetworkState
from app.simulator_client import SimulatorClient, SimulatorUnavailableError

DEPOT = {
    "id": "depot-gazipur",
    "name": "Gazipur Depot",
    "region_id": "region-dhaka",
    "status": "OPEN",
    "dispatch_capacity_per_tick": 12000.0,
    "capacity": {"DIESEL": 90000, "PETROL": 70000, "OCTANE": 45000},
    "inventory": {"DIESEL": 60000, "PETROL": 45000, "OCTANE": 26000},
}


def make_poll_handler(tick: int, fail: bool = False):
    def handler(request: httpx.Request) -> httpx.Response:
        if fail:
            return httpx.Response(503, json={"error": {"code": "FAULT_INJECTED", "message": "down"}})
        path = request.url.path
        if path == "/v1/instance":
            return httpx.Response(
                200,
                json={
                    "id": 1,
                    "scenario_id": "baseline",
                    "scenario_version": "1.0",
                    "seed": 1,
                    "sim_time": "2026-01-01T00:00:00",
                    "tick": tick,
                    "tick_minutes": 15,
                    "status": "PAUSED",
                },
            )
        if path == "/v1/depots":
            return httpx.Response(200, json=[DEPOT])
        if path == "/v1/metrics":
            return httpx.Response(
                200,
                json={
                    "served_demand_liters": 0.0,
                    "unmet_demand_liters": 0.0,
                    "service_level": 1.0,
                    "allocation_liters": 0.0,
                    "allocation_failures": 0,
                },
            )
        return httpx.Response(200, json=[])

    return handler


def test_poller_poll_once_updates_state() -> None:
    client = SimulatorClient(
        base_url="http://simulator.test", transport=httpx.MockTransport(make_poll_handler(tick=7))
    )
    state = NetworkState()
    poller = Poller(client, state)

    asyncio.run(poller.poll_once())

    assert state.last_polled_tick == 7
    assert state.depots is not None
    assert state.depots.data[0].id == "depot-gazipur"
    assert state.last_poll_error is None
    assert state.is_ready


def test_poller_keeps_last_known_good_state_on_failure() -> None:
    client = SimulatorClient(
        base_url="http://simulator.test", transport=httpx.MockTransport(make_poll_handler(tick=3))
    )
    state = NetworkState()
    poller = Poller(client, state)

    asyncio.run(poller.poll_once())
    assert state.depots is not None
    good_depots = state.depots

    # Force every subsequent call to fail and confirm state isn't clobbered.
    def always_fail(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": {"code": "FAULT_INJECTED", "message": "down"}})

    poller._client = SimulatorClient(
        base_url="http://simulator.test", transport=httpx.MockTransport(always_fail)
    )

    try:
        asyncio.run(poller.poll_once())
        raise AssertionError("expected SimulatorUnavailableError")
    except SimulatorUnavailableError:
        pass

    assert state.depots is good_depots  # untouched — last-known-good preserved
    assert state.last_poll_error is not None


def test_sse_listener_parses_events_and_tracks_tick() -> None:
    body = (
        b": connected\n\n"
        b'event: simulation.tick\ndata: {"tick": 5, "sim_time": "2026-01-01T00:00:00"}\n\n'
        b'event: simulator.notice\ndata: {"message": "hello"}\n\n'
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})

    received: list[tuple[str, dict]] = []
    listener: SSEListener | None = None

    async def on_event(event: str, payload: dict) -> None:
        received.append((event, payload))
        if len(received) >= 2:
            listener.stop()

    listener = SSEListener(
        base_url="http://simulator.test", on_event=on_event, transport=httpx.MockTransport(handler)
    )

    asyncio.run(listener.run_forever())

    assert received == [
        ("simulation.tick", {"tick": 5, "sim_time": "2026-01-01T00:00:00"}),
        ("simulator.notice", {"message": "hello"}),
    ]
    assert listener.last_event_tick == 5


def test_supervisor_gap_detection_flags_stale_sse() -> None:
    client = SimulatorClient(
        base_url="http://simulator.test", transport=httpx.MockTransport(make_poll_handler(tick=10))
    )
    supervisor = IngestionSupervisor(client=client, sse_base_url="http://simulator.test")

    # Simulate: REST says tick 10, SSE hasn't reported anything past tick 5,
    # and the listener still claims to be connected — a silent queue drop.
    supervisor.state.instance = types.SimpleNamespace(data=types.SimpleNamespace(tick=10), stale=False)
    supervisor.sse_listener.last_event_tick = 5
    supervisor.sse_listener.connected = True

    assert supervisor._gap_exists() is True

    # Within threshold — no gap.
    supervisor.sse_listener.last_event_tick = 9
    assert supervisor._gap_exists() is False

    # Not connected at all — poller alone is expected to carry state, not a "gap".
    supervisor.sse_listener.last_event_tick = 5
    supervisor.sse_listener.connected = False
    assert supervisor._gap_exists() is False
