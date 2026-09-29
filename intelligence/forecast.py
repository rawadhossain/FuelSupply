"""Demand forecaster: E[d] = P(station,fuel,tick_of_day) x region_factor x M(t) x R.

P  = base time-of-day profile learned from normal (baseline) data, with region factor
     and station multiplier divided out, so live multipliers can be applied on top.
M  = station demand_multiplier schedule (current value + SCHEDULED/ACTIVE demand_spike events).
R  = short-term EWMA correction of actual / (P x factor x M) for shifts nobody announced.
Intervals: split-conformal on relative residuals from the validation set.
Only numpy/pandas. See docs/ml-architecture.md §3.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

TICKS_PER_DAY = 96
QUANTILES = (0.05, 0.10, 0.50, 0.90, 0.95)
Key = tuple[str, str]  # (station_id, fuel_type)


@dataclass
class ProfileForecaster:
    version: str = "profile-v1"
    profile: dict[Key, np.ndarray] = field(default_factory=dict)       # base demand per tick_of_day (96,)
    region_factor: dict[str, float] = field(default_factory=dict)      # station_id -> demand_factor
    resid_q: dict[Key, dict[float, float]] = field(default_factory=dict)  # relative-residual quantiles
    ewma_alpha: float = 0.0          # tuned on normal val data (0 = off: noise is independent per tick)
    adaptive_alpha: float = 0.3      # used instead while the detector reports a demand anomaly
    ewma_clip: tuple[float, float] = (0.5, 3.0)
    ewma_decay_ticks: int = 16

    # ------------------------------------------------------------------ fitting
    def fit(self, train: pd.DataFrame) -> ProfileForecaster:
        """train: rows with station_id, fuel_type, tick_of_day, demand_liters, demand_factor,
        optional demand_multiplier (defaults 1.0 — baseline scenario has no events)."""
        df = train.copy()
        mult = df.get("demand_multiplier", 1.0)
        df["base"] = df["demand_liters"] / (df["demand_factor"] * mult)
        self.region_factor = df.groupby("station_id")["demand_factor"].first().to_dict()
        g = df.groupby(["station_id", "fuel_type", "tick_of_day"])["base"].mean()
        for (s, f), sub in g.groupby(level=[0, 1]):
            arr = np.full(TICKS_PER_DAY, np.nan)
            arr[sub.index.get_level_values(2).to_numpy()] = sub.to_numpy()
            if np.isnan(arr).any():  # fill any missing slot from neighbours
                arr = pd.Series(arr).interpolate(limit_direction="both").to_numpy()
            self.profile[(s, f)] = arr
        return self

    def calibrate(self, val: pd.DataFrame) -> ProfileForecaster:
        """Split-conformal: quantiles of actual/forecast - 1 per series on held-out data."""
        pred = self.point(val["station_id"], val["fuel_type"], val["tick_of_day"])
        rel = val["demand_liters"].to_numpy() / pred - 1.0
        tmp = pd.DataFrame({"s": val["station_id"].to_numpy(), "f": val["fuel_type"].to_numpy(), "r": rel})
        for (s, f), sub in tmp.groupby(["s", "f"]):
            self.resid_q[(s, f)] = {q: float(np.quantile(sub["r"], q)) for q in QUANTILES}
        return self

    # ---------------------------------------------------------------- inference
    def point(self, stations, fuels, tods, multipliers=1.0) -> np.ndarray:
        """Vectorised point forecast for aligned arrays of station, fuel, tick_of_day."""
        stations, fuels, tods = map(np.asarray, (stations, fuels, tods))
        out = np.empty(len(stations))
        for i, (s, f, t) in enumerate(zip(stations, fuels, tods)):
            out[i] = self.profile[(s, f)][int(t) % TICKS_PER_DAY] * self.region_factor[s]
        return out * np.asarray(multipliers, dtype=float)

    def ewma_ratio(self, actual: np.ndarray, expected: np.ndarray, anomaly_active: bool = False) -> float:
        """R from the most recent observations (oldest first); expected must already include M.
        Uses adaptive_alpha while an anomaly is active, else ewma_alpha. 1.0 when off or no data."""
        alpha = self.adaptive_alpha if anomaly_active else self.ewma_alpha
        if alpha <= 0 or len(actual) == 0:
            return 1.0
        r = 1.0
        for a, e in zip(actual, expected):
            if e > 0:
                r = (1 - alpha) * r + alpha * (a / e)
        return float(np.clip(r, *self.ewma_clip))

    def forecast(self, station: str, fuel: str, start_tick: int, horizon: int,
                 multiplier_schedule: np.ndarray | float = 1.0, ratio: float = 1.0) -> dict:
        """Per-tick and cumulative forecast for ticks start_tick .. start_tick+horizon-1.
        multiplier_schedule: scalar or array(horizon) of demand multipliers (from events).
        ratio: EWMA correction R; decays linearly back to 1 over ewma_decay_ticks."""
        ticks = np.arange(start_tick, start_tick + horizon)
        base = self.profile[(station, fuel)][ticks % TICKS_PER_DAY] * self.region_factor[station]
        m = np.broadcast_to(np.asarray(multiplier_schedule, dtype=float), (horizon,))
        decay = np.clip(1 - np.arange(horizon) / max(self.ewma_decay_ticks, 1), 0, 1)
        r = 1 + (ratio - 1) * decay
        q50 = base * m * r
        cum = np.cumsum(q50)
        k = np.arange(1, horizon + 1)
        rq = self.resid_q[(station, fuel)]
        res = {"ticks": ticks.tolist(), "q50": q50.tolist(), "cum_q50": cum.tolist()}
        for q in (0.10, 0.90):
            res[f"q{int(q*100)}"] = (q50 * (1 + rq[q])).tolist()
            # independent per-tick noise: relative spread of a k-tick sum shrinks ~ 1/sqrt(k)
            res[f"cum_q{int(q*100)}"] = (cum * (1 + (rq[q] - rq[0.50]) / np.sqrt(k) + rq[0.50])).tolist()
        return res

    # -------------------------------------------------------------- persistence
    def save(self, folder: str, extra_metrics: dict | None = None) -> None:
        os.makedirs(folder, exist_ok=True)
        rows = [{"station_id": s, "fuel_type": f, "tick_of_day": t, "base_liters": float(v)}
                for (s, f), arr in self.profile.items() for t, v in enumerate(arr)]
        pd.DataFrame(rows).to_csv(os.path.join(folder, "profile.csv"), index=False)
        cfg = {"version": self.version, "region_factor": self.region_factor,
               "ewma_alpha": self.ewma_alpha, "adaptive_alpha": self.adaptive_alpha, "ewma_clip": list(self.ewma_clip),
               "ewma_decay_ticks": self.ewma_decay_ticks,
               "resid_q": {f"{s}|{f}": {str(q): v for q, v in d.items()} for (s, f), d in self.resid_q.items()}}
        with open(os.path.join(folder, "model.json"), "w") as fh:
            json.dump(cfg, fh, indent=2)
        if extra_metrics is not None:
            with open(os.path.join(folder, "metrics.json"), "w") as fh:
                json.dump(extra_metrics, fh, indent=2)

    @classmethod
    def load(cls, folder: str) -> ProfileForecaster:
        with open(os.path.join(folder, "model.json")) as fh:
            cfg = json.load(fh)
        m = cls(version=cfg["version"], region_factor=cfg["region_factor"], ewma_alpha=cfg["ewma_alpha"], adaptive_alpha=cfg.get("adaptive_alpha", 0.3),
                ewma_clip=tuple(cfg["ewma_clip"]), ewma_decay_ticks=cfg["ewma_decay_ticks"])
        m.resid_q = {tuple(k.split("|")): {float(q): v for q, v in d.items()} for k, d in cfg["resid_q"].items()}
        prof = pd.read_csv(os.path.join(folder, "profile.csv"))
        for (s, f), sub in prof.groupby(["station_id", "fuel_type"]):
            m.profile[(s, f)] = sub.sort_values("tick_of_day")["base_liters"].to_numpy()
        return m
