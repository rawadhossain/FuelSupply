"""Deterministic idempotency-key generation (ADR-004).

Keys are a hash of the full intended shipment — depot, station, route, fuel,
quantity, tick, an `intent` tag, and an `attempt` counter — not a random UUID
per HTTP call. A network-level retry (dropped connection, timeout) for the
*same* intended shipment reuses the same key, so the simulator's own
idempotency check deduplicates it instead of creating a second real shipment.

Refinements over the original SPEC.md decision, required by the simulator's
actual documented behavior (Guide §5.4, verified live in VER-003):
  - quantity and route_id are part of the hash: without them, a re-planned
    quantity in the same tick would collide and get rejected as
    IDEMPOTENCY_KEY_MISMATCH instead of minting a fresh key.
  - `attempt` exists because a key is permanently consumed even by a
    cancelled or FAILED allocation — a deliberate re-decision for the same
    tick (e.g. retrying after FAILED) must bump `attempt` to get a new key.
    A caller must never bump `attempt` on its own network retry — that would
    defeat the point of the key being deterministic.
"""

from __future__ import annotations

import hashlib


def make_idempotency_key(
    *,
    source_depot_id: str,
    destination_station_id: str,
    route_id: str,
    fuel_type: str,
    quantity: float,
    tick: int,
    intent: str,
    attempt: int = 0,
) -> str:
    raw = "|".join(
        [
            source_depot_id,
            destination_station_id,
            route_id,
            fuel_type,
            f"{quantity:.3f}",
            str(tick),
            intent,
            str(attempt),
        ]
    )
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
    return f"alloc-{digest}"
