# ML datasets (dataset/ml)

All values come from the BUP simulator (baseline scenario, seed 12345, 2000 ticks = ~20.8 sim-days, 15-min ticks).
No values are invented; engineered columns (lags, rolling stats, targets) are computed from the fetched data.
Rebuild: `python dataset/build_ml_datasets.py` (after `export_dataset.py`).

| File | Requirement | Use |
|---|---|---|
| demand_train.csv | REQ-007 Prediction | Train demand forecaster. Ticks 96-1343 (days 1-13). |
| demand_val.csv | REQ-007 | Tune hyperparameters / pick model. Days 14-16. |
| demand_test.csv | REQ-007, REQ-013 (prediction error) | Final held-out score, never train on it. Days 17-20. |
| demand_full_features.csv | REQ-007 | All 24,000 rows + features, incl. warm-up rows with empty lags. |
| baseline_demand_profile.csv | REQ-009c confidence, Detection, fallback | Mean/std/p05/p95 per station x fuel x 15-min slot (train only). Seasonal-naive baseline, anomaly z-score, fallback when ML is down. |
| ref_stations.csv / ref_depots.csv / ref_routes.csv / ref_regions.csv | REQ-008 recommendations | Capacities, transit ticks, max shipment, dispatch caps: allocator constraints. `inventory_at_export` is end-of-run and NOT a starting value. |
| supply_arrivals.csv | REQ-008, shortage prediction | Scheduled depot resupply (tick, fuel, quantity). |
| daily_station_summary.csv | REQ-001 dashboard | Daily demand/served/unmet per station/fuel: charts, sanity checks. |
| crisis_demand_labeled.csv | REQ-011 crisis, Detection eval | Created by `export_crisis_dataset.py`. 7-day run with injected spikes/outage; `is_anomaly` label = test set for anomaly detector. |
| crisis_events.csv | REQ-011 | The injected events (ground truth timeline). |

Columns: target is `target_next_tick` (next 15-min demand) or `target_next_4h` (sum of next 16 ticks, for stockout horizon).
Features use only past data (lag_1/2/4/96, roll_mean_4/96, roll_std_96, hour, tick_of_day, day_of_week, demand_profile, demand_factor).

Caveats:
- No allocations were sent, so stations ran dry on day 1: `served_liters`/`unmet_liters` show a no-replenishment world.
  Use `demand_liters` as the forecasting target; do not train on served/unmet.
- Baseline has no events, so train/val/test are "normal" demand only. Detection must be evaluated on the crisis file.
