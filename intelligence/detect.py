"""Streaming two-sided CUSUM on log(actual / expected), per station x fuel.
Detects demand shifts that the forecast (incl. declared event multipliers) does not explain.
See docs/ml-architecture.md §6. Only numpy."""
from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass
class CusumState:
    pos: float = 0.0
    neg: float = 0.0
    alarm: str | None = None      # "up" | "down" | None
    since_tick: int | None = None


@dataclass
class CusumDetector:
    sigma: dict[tuple[str, str], float]   # std of log residual per series (from validation data)
    k: float = 0.5                         # allowance, in sigmas
    h: float = 5.0                         # decision threshold, in sigmas
    states: dict[tuple[str, str], CusumState] = field(default_factory=dict)

    def update(self, station: str, fuel: str, tick: int, actual: float, expected: float) -> dict | None:
        """Feed one observation. Returns a signal dict when an alarm starts, else None.
        A sustained shift raises one alarm; it clears once both sums fall back below h."""
        key = (station, fuel)
        st = self.states.setdefault(key, CusumState())
        if actual <= 0 or expected <= 0:
            return None
        z = math.log(actual / expected) / self.sigma[key]
        cap = 1.5 * self.h  # bounded memory: alarm clears within ~h/(2k) ticks after the shift ends
        st.pos = min(cap, max(0.0, st.pos + z - self.k))
        st.neg = min(cap, max(0.0, st.neg - z - self.k))
        new = "up" if st.pos > self.h else "down" if st.neg > self.h else None
        if new and st.alarm is None:
            st.alarm, st.since_tick = new, tick
            return {"type": "demand_anomaly", "station_id": station, "fuel_type": fuel,
                    "direction": new, "since_tick": tick,
                    "score": round(max(st.pos, st.neg), 2)}
        if new is None and st.alarm is not None:
            # back to normal: fully re-arm, otherwise sums sitting just under h re-fire on noise
            # (seen on real crisis data as "aftershock" alarms 11-27 ticks after a spike ended)
            st.pos = st.neg = 0.0
            st.alarm, st.since_tick = None, None
        return None

    def reset(self, station: str | None = None, fuel: str | None = None) -> None:
        """Call after the forecast has absorbed a shift (e.g. new multiplier) to re-arm."""
        for key in list(self.states):
            if (station is None or key[0] == station) and (fuel is None or key[1] == fuel):
                self.states[key] = CusumState()

    def active(self) -> dict[tuple[str, str], CusumState]:
        return {k: v for k, v in self.states.items() if v.alarm}
