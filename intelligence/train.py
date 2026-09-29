"""Train, tune and evaluate the demand forecaster + anomaly detector.

    python -m intelligence.train            (from the repo root)

Inputs : dataset/ml/demand_full_features.csv (built by dataset/build_ml_datasets.py)
Split  : train ticks < 1344 (days 0-13) | val 1344-1631 (days 14-16) | test >= 1632 (days 17-20)
Output : intelligence/artifacts/profile-v1/{profile.csv, model.json, detector.json, metrics.json}
No test data is used for fitting or tuning.
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np
import pandas as pd

from .detect import CusumDetector
from .forecast import ProfileForecaster, TICKS_PER_DAY

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "dataset", "ml", "demand_full_features.csv")
OUT = os.path.join(ROOT, "intelligence", "artifacts", "profile-v1")
TRAIN_END, VAL_END = 14 * TICKS_PER_DAY, 17 * TICKS_PER_DAY
ALPHAS = [0.0, 0.05, 0.1, 0.2, 0.3]
H_GRID = [4.0, 5.0, 6.0, 7.0, 8.0, 10.0]
MAX_FALSE_ALARMS_PER_SERIES_DAY = 0.05   # ~0.6 false alerts/day across all 12 series
ADAPTIVE_ALPHA = 0.3   # EWMA used only while a demand anomaly is active (regime switch)


def mae_mape(y, p):
    e = np.abs(y - p)
    return {"mae": round(float(e.mean()), 3), "mape_pct": round(float((e / y).mean() * 100), 3)}


def rolling_one_step(model: ProfileForecaster, df: pd.DataFrame, alpha: float) -> np.ndarray:
    """1-step-ahead forecast using R from observations strictly before each tick."""
    model.ewma_alpha = alpha
    preds = np.empty(len(df))
    for (s, f), sub in df.groupby(["station_id", "fuel_type"], sort=False):
        sub = sub.sort_values("tick")
        exp = model.point(sub.station_id, sub.fuel_type, sub.tick_of_day)
        act = sub.demand_liters.to_numpy()
        r, out = 1.0, np.empty(len(sub))
        for i in range(len(sub)):
            out[i] = exp[i] * r
            if alpha > 0 and exp[i] > 0:
                r = float(np.clip((1 - alpha) * r + alpha * act[i] / exp[i], *model.ewma_clip))
        preds[df.index.get_indexer(sub.index)] = out
    return preds


def cumulative_coverage(model, df, window):
    """Share of rolling windows whose actual k-tick total lies inside [cum_q10, cum_q90]."""
    hits = total = 0
    for (s, f), sub in df.groupby(["station_id", "fuel_type"]):
        sub = sub.sort_values("tick")
        act = sub.demand_liters.to_numpy()
        for start in range(0, len(sub) - window + 1, window // 2 or 1):
            fc = model.forecast(s, f, int(sub.tick.iloc[start]), window)
            tot = act[start:start + window].sum()
            hits += fc["cum_q10"][-1] <= tot <= fc["cum_q90"][-1]
            total += 1
    return round(hits / total, 3)


def count_alarms(model, df, sigma, h, mult=1.0, onset=None):
    """False alarms (mult=1) or detection delays (mult!=1 applied from `onset` index per series)."""
    alarms, delays = 0, []
    for (s, f), sub in df.groupby(["station_id", "fuel_type"]):
        sub = sub.sort_values("tick")
        det = CusumDetector(sigma=sigma, h=h)
        exp = model.point(sub.station_id, sub.fuel_type, sub.tick_of_day)
        hit = None
        for i, (t, a) in enumerate(zip(sub.tick, sub.demand_liters)):
            if onset is not None and i >= onset:
                a *= mult
            if det.update(s, f, int(t), float(a), float(exp[i])):
                if onset is None:
                    alarms += 1
                elif i >= onset and hit is None:
                    hit = i - onset + 1
        delays.append(hit)
    return alarms, delays


def main() -> int:
    if not os.path.exists(DATA):
        print(f"Missing {DATA}. Run dataset/export_dataset.py then dataset/build_ml_datasets.py", file=sys.stderr)
        return 1
    d = pd.read_csv(DATA)
    need = {"station_id", "fuel_type", "tick", "tick_of_day", "demand_liters", "demand_factor"}
    assert need <= set(d.columns), f"missing columns: {need - set(d.columns)}"
    assert (d.demand_liters >= 0).all(), "negative demand in data"
    train = d[d.tick < TRAIN_END].reset_index(drop=True)
    val = d[(d.tick >= TRAIN_END) & (d.tick < VAL_END)].reset_index(drop=True)
    test = d[d.tick >= VAL_END].reset_index(drop=True)

    # 1. fit base profile on train
    model = ProfileForecaster().fit(train)

    # 2. tune EWMA alpha on val (1-step)
    tuning = {a: mae_mape(val.demand_liters.to_numpy(), rolling_one_step(model, val, a)) for a in ALPHAS}
    best_alpha = min(tuning, key=lambda a: tuning[a]["mae"])
    model.ewma_alpha = best_alpha

    # 3. conformal calibration on val
    model.calibrate(val)

    # 4. detector sigma = std of log residual on val
    lr = np.log(val.demand_liters.to_numpy() / model.point(val.station_id, val.fuel_type, val.tick_of_day))
    sig = pd.Series(lr).groupby([val.station_id, val.fuel_type]).std()
    sigma = {k: float(v) for k, v in sig.items()}

    # 4b. tune CUSUM threshold h on val: smallest h meeting the false-alarm budget
    val_series_days = len(val) / TICKS_PER_DAY
    h_tuning = {}
    for h in H_GRID:
        fa, _ = count_alarms(model, val, sigma, h)
        _, dl = count_alarms(model, val, sigma, h, mult=1.3, onset=48)
        found = [x for x in dl if x is not None]
        h_tuning[h] = {"false_alarms_per_series_day": round(fa / val_series_days, 4),
                       "shift_1.3_detected": f"{len(found)}/{len(dl)}",
                       "shift_1.3_median_ticks": float(np.median(found)) if found else None}
    ok = [h for h in H_GRID if h_tuning[h]["false_alarms_per_series_day"] <= MAX_FALSE_ALARMS_PER_SERIES_DAY]
    best_h = min(ok) if ok else max(H_GRID)

    # 5. evaluate on test (never seen)
    y = test.demand_liters.to_numpy()
    p_multi = model.point(test.station_id, test.fuel_type, test.tick_of_day)
    last_day = d[(d.tick >= VAL_END - TICKS_PER_DAY) & (d.tick < VAL_END)].set_index(
        ["station_id", "fuel_type", "tick_of_day"]).demand_liters
    p_naive = last_day.reindex(pd.MultiIndex.from_frame(test[["station_id", "fuel_type", "tick_of_day"]])).to_numpy()
    p_one = rolling_one_step(model, test, best_alpha)

    lo = np.array([model.resid_q[(s, f)][0.10] for s, f in zip(test.station_id, test.fuel_type)])
    hi = np.array([model.resid_q[(s, f)][0.90] for s, f in zip(test.station_id, test.fuel_type)])
    cov_tick = float(((y >= p_multi * (1 + lo)) & (y <= p_multi * (1 + hi))).mean())

    per_series = {f"{s}|{f}": mae_mape(sub.demand_liters.to_numpy(),
                                       model.point(sub.station_id, sub.fuel_type, sub.tick_of_day))
                  for (s, f), sub in test.groupby(["station_id", "fuel_type"])}

    # 6. detector on normal test data: false alarms
    alarms, _ = count_alarms(model, test, sigma, best_h)
    series_days = len(test) / TICKS_PER_DAY

    # 7. sanity check with an injected multiplicative shift on test data (NOT real crisis data;
    #    real evaluation needs dataset/ml/crisis_demand_labeled.csv from export_crisis_dataset.py)
    delays = {}
    for mult in (1.2, 1.3, 1.5, 1.8, 0.5):
        _, dl = count_alarms(model, test, sigma, best_h, mult=mult, onset=48)
        found = [x for x in dl if x is not None]
        delays[str(mult)] = {"detected_series": f"{len(found)}/{len(dl)}",
                             "median_ticks_to_detect": float(np.median(found)) if found else None,
                             "max_ticks_to_detect": max(found) if found else None}

    metrics = {
        "model_version": model.version,
        "data": {"source": "BUP simulator baseline, seed 12345, 2000 ticks", "train_rows": len(train),
                 "val_rows": len(val), "test_rows": len(test),
                 "split_ticks": {"train": [0, TRAIN_END - 1], "val": [TRAIN_END, VAL_END - 1],
                                 "test": [VAL_END, int(d.tick.max())]}},
        "tuning_val_one_step_by_alpha": {str(k): v for k, v in tuning.items()},
        "chosen_ewma_alpha": best_alpha,
        "adaptive_ewma_alpha_during_anomaly": ADAPTIVE_ALPHA,
        "tuning_val_cusum_h": {str(k): v for k, v in h_tuning.items()},
        "chosen_cusum_h": best_h,
        "test": {
            "multi_step_profile": mae_mape(y, p_multi),
            "multi_step_seasonal_naive_baseline": mae_mape(y, p_naive),
            "one_step_with_ewma": mae_mape(y, p_one),
            "interval_q10_q90_coverage_per_tick": round(cov_tick, 3),
            "interval_coverage_cumulative_16_ticks": cumulative_coverage(model, test, 16),
            "interval_coverage_cumulative_96_ticks": cumulative_coverage(model, test, 96),
            "per_series_multi_step": per_series,
        },
        "detector": {"k_sigma": 0.5, "h_sigma": best_h,
                     "false_alarms_on_normal_test": alarms,
                     "false_alarms_per_series_day": round(alarms / series_days, 4),
                     "injected_shift_sanity_check": delays,
                     "note": "Injected shifts are a sanity check only; evaluate on crisis_demand_labeled.csv."},
    }
    model.save(OUT, metrics)
    with open(os.path.join(OUT, "detector.json"), "w") as fh:
        json.dump({"k": 0.5, "h": best_h, "adaptive_ewma_alpha": ADAPTIVE_ALPHA, "sigma": {f"{s}|{f}": v for (s, f), v in sigma.items()}}, fh, indent=2)
    print(json.dumps({k: metrics[k] for k in ("chosen_ewma_alpha", "chosen_cusum_h", "tuning_val_cusum_h")}, indent=1))
    print(json.dumps({k: v for k, v in metrics["test"].items() if k != "per_series_multi_step"}, indent=1))
    print(json.dumps({k: v for k, v in metrics["detector"].items() if k != "note"}, indent=1))
    print(f"Artifacts written to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
