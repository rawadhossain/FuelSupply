"""Per-tick schedules over the planning horizon, derived from current state + /v1/events.
Offset k = 0..H-1 corresponds to simulator tick snapshot.tick + k."""
from __future__ import annotations

import numpy as np

from .snapshot import Snapshot


def _window(snap: Snapshot, e, H: int) -> tuple[int, int]:
    """Offsets [a, b) during which the event affects the world. Verified on real simulator crisis
    data: an event with start_tick S / end_tick E is active for ticks S .. E *inclusive*
    (spike 100-148 raised demand on ticks 100..148; tongi 600-640 on 600..640)."""
    return max(0, min(H, e.start_tick - snap.tick)), max(0, min(H, e.end_tick + 1 - snap.tick))


def _hits_station(snap: Snapshot, e, sid: str) -> bool:
    p = e.parameters
    sids, rids = p.get("station_ids") or [], p.get("region_ids") or []
    if not sids and not rids:
        return True
    return sid in sids or snap.stations[sid].region_id in rids


def multiplier_schedule(snap: Snapshot, sid: str, H: int) -> np.ndarray:
    """Current demand_multiplier, adjusted for events that start or end inside the horizon."""
    m = np.full(H, snap.stations[sid].demand_multiplier)
    for e in snap.events:
        if e.type != "demand_spike" or e.status == "RESOLVED" or not _hits_station(snap, e, sid):
            continue
        mult = float(e.parameters.get("multiplier", 1.5))
        a, b = _window(snap, e, H)
        if e.status == "SCHEDULED" and e.start_tick >= snap.tick:
            m[a:b] *= mult                      # not yet applied by the simulator
        elif e.status == "ACTIVE":
            m[b:] /= max(mult, 0.01)            # already in demand_multiplier; undo after it ends
    return m


def station_open(snap: Snapshot, sid: str, H: int) -> np.ndarray:
    ok = np.full(H, snap.stations[sid].status == "OPEN")
    for e in snap.events:
        if e.type != "station_outage" or e.status == "RESOLVED":
            continue
        sids = e.parameters.get("station_ids") or []
        if sids and sid not in sids:
            continue
        a, b = _window(snap, e, H)
        if e.status == "SCHEDULED":
            ok[a:b] = False
        elif e.status == "ACTIVE":
            ok[max(b, 1):] = True   # offset 0 = live status: simulator resolves at the tick it processes
    return ok


def route_open(snap: Snapshot, rid: str, H: int) -> np.ndarray:
    ok = np.full(H, snap.routes[rid].status == "AVAILABLE")
    for e in snap.events:
        if e.type != "route_disruption" or e.status == "RESOLVED":
            continue
        rids = e.parameters.get("route_ids") or []
        if rids and rid not in rids:
            continue
        a, b = _window(snap, e, H)
        if e.status == "SCHEDULED":
            ok[a:b] = False
        elif e.status == "ACTIVE":
            ok[max(b, 1):] = True   # offset 0 = live status: simulator resolves at the tick it processes
    return ok


def depot_open(snap: Snapshot, did: str, H: int) -> np.ndarray:
    # CONSTRAINED depots can still ship (guide §5.3); only other statuses block.
    return np.full(H, snap.depots[did].status in ("OPEN", "CONSTRAINED"))
