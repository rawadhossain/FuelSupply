"""Split the raw simulator export into purpose-specific CSVs (no values invented).

Input : dataset/data/*.json + demand_history.csv   (from export_dataset.py)
Output: dataset/ml/*.csv
Run   : python dataset/build_ml_datasets.py
"""
import json, os
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
RAW, OUT = os.path.join(HERE, "data"), os.path.join(HERE, "ml")
os.makedirs(OUT, exist_ok=True)
TPD = 96                     # ticks per sim-day (15-min ticks)
TRAIN_END, VAL_END = 14 * TPD, 17 * TPD   # days 0-13 train, 14-16 val, 17+ test
load = lambda n: json.load(open(os.path.join(RAW, n)))

# ---------- reference tables (constraints for stockout + allocator) ----------
regions = pd.DataFrame(load("regions.json"))
regions.to_csv(f"{OUT}/ref_regions.csv", index=False)

def flat(rows, cap_key="capacity", inv_key="inventory"):
    out = []
    for r in rows:
        base = {k: v for k, v in r.items() if k not in (cap_key, inv_key)}
        for fuel in r[cap_key]:
            out.append({**base, "fuel_type": fuel, "capacity": r[cap_key][fuel],
                        "inventory_at_export": r[inv_key][fuel]})
    return pd.DataFrame(out)

stations = flat(load("stations.json"))
stations.to_csv(f"{OUT}/ref_stations.csv", index=False)
flat(load("depots.json")).to_csv(f"{OUT}/ref_depots.csv", index=False)
pd.DataFrame(load("routes.json")).to_csv(f"{OUT}/ref_routes.csv", index=False)
pd.DataFrame(load("supply_arrivals.json")).to_csv(f"{OUT}/supply_arrivals.csv", index=False)

# ---------- demand features ----------
d = pd.read_csv(f"{RAW}/demand_history.csv")
d["sim_time"] = pd.to_datetime(d["sim_time"])
meta = stations.drop_duplicates("id")[["id", "region_id", "demand_profile"]]
d = d.merge(meta, left_on="station_id", right_on="id", suffixes=("", "_st")).drop(columns="id_st")
d = d.merge(regions[["id", "demand_factor"]].rename(columns={"id": "region_id"}), on="region_id")
d = d.sort_values(["station_id", "fuel_type", "tick"]).reset_index(drop=True)
d["hour"] = d.sim_time.dt.hour
d["minute"] = d.sim_time.dt.minute
d["tick_of_day"] = d.tick % TPD
d["day_index"] = d.tick // TPD
d["day_of_week"] = d.sim_time.dt.dayofweek
g = d.groupby(["station_id", "fuel_type"])["demand_liters"]
for lag in (1, 2, 4, 96):                               # 15m, 30m, 1h, same time yesterday
    d[f"lag_{lag}"] = g.shift(lag)
d["roll_mean_4"] = g.transform(lambda s: s.shift(1).rolling(4).mean())
d["roll_mean_96"] = g.transform(lambda s: s.shift(1).rolling(96).mean())
d["roll_std_96"] = g.transform(lambda s: s.shift(1).rolling(96).std())
# targets: next tick, and total over next 4h (16 ticks) for stockout planning
d["target_next_tick"] = g.shift(-1)
d["target_next_4h"] = g.transform(lambda s: s[::-1].rolling(16).sum()[::-1].shift(-1))

d.to_csv(f"{OUT}/demand_full_features.csv", index=False)
model = d.dropna(subset=["lag_96", "roll_std_96", "target_next_tick", "target_next_4h"])
model[model.tick < TRAIN_END].to_csv(f"{OUT}/demand_train.csv", index=False)
model[(model.tick >= TRAIN_END) & (model.tick < VAL_END)].to_csv(f"{OUT}/demand_val.csv", index=False)
model[model.tick >= VAL_END].to_csv(f"{OUT}/demand_test.csv", index=False)

# ---------- normal-behaviour profile (train only) for baseline + anomaly z-score ----------
tr = d[d.tick < TRAIN_END]
prof = (tr.groupby(["station_id", "fuel_type", "tick_of_day"])["demand_liters"]
          .agg(mean="mean", std="std", p05=lambda s: s.quantile(.05),
               p95=lambda s: s.quantile(.95), n="count").reset_index())
prof.to_csv(f"{OUT}/baseline_demand_profile.csv", index=False)

# ---------- daily totals (dashboard / sanity charts) ----------
(d.groupby(["day_index", "station_id", "fuel_type"])[["demand_liters", "served_liters", "unmet_liters"]]
   .sum().reset_index().to_csv(f"{OUT}/daily_station_summary.csv", index=False))

for f in sorted(os.listdir(OUT)):
    print(f"{f:32s} {len(pd.read_csv(os.path.join(OUT, f))):>6} rows")
