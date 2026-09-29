# Intelligence — trained models

Design: `docs/ml-architecture.md` (v3). Data: `dataset/ml/` (built from the simulator export).

| Module | What it is |
|---|---|
| `forecast.py` | `ProfileForecaster`: time-of-day base profile × region factor × event multiplier schedule × EWMA correction (only while an anomaly is active); split-conformal q10/q90 per tick and cumulative |
| `detect.py` | `CusumDetector`: streaming two-sided CUSUM on log(actual/expected) per station × fuel |
| `train.py` | Fits on train days, tunes on val days, evaluates on test days, writes `artifacts/profile-v1/` |
| `signals.py` | State-change + statistical risk signals for all event types |
| `policy_lp.py` | Rolling-horizon LP (SciPy HiGHS) — optimiser mode |
| `assess.py` | `Assessor.assess(snapshot, new_demand_rows, policy)` → full recommendation object (contract in `docs/ml-architecture.md` §8) |
| `replay.py` | Offline closed-loop benchmark on recorded demand (`--crisis` for scripted events) |
| `benchmark.py` | Live benchmark against the real simulator (`run_benchmark.bat`) |
| `../shared/` | `snapshot`, `schedule`, `projection`, `heuristic` — imported by Core too (fallback without this service) |
| `artifacts/profile-v1/` | `profile.csv` (12 series × 96 slots), `model.json` (region factors, conformal quantiles, EWMA settings), `detector.json` (sigma per series, k, h), `metrics.json` (all results) |

```bash
pip install -r intelligence/requirements.txt
python -m intelligence.train                 # retrain (~1 s)
python -m pytest intelligence/tests -q       # 22 tests
```

Use at runtime:
```python
from intelligence.forecast import ProfileForecaster
m = ProfileForecaster.load("intelligence/artifacts/profile-v1")
fc = m.forecast("station-mirpur", "DIESEL", start_tick=812, horizon=96,
                multiplier_schedule=sched,   # array from station.demand_multiplier + /v1/events
                ratio=m.ewma_ratio(recent_actual, recent_expected, anomaly_active=alarm))
```

Test-set results (days 17–20, never used for fitting or tuning): see `artifacts/profile-v1/metrics.json`.
Assess a live snapshot:
```python
from shared.snapshot import Snapshot
from intelligence.assess import Assessor
ass = Assessor()                                   # load once
out = ass.assess(Snapshot.from_api(json_by_endpoint, stale=hdr_stale), new_demand_rows)   # policy="heuristic" default
```

Benchmarks: `python -m intelligence.replay --ticks 288 --crisis` (offline) · double-click `run_benchmark.bat` (live, resets the simulator).
Still to do: detector evaluation on real crisis data (`get_crisis_dataset.bat`), live benchmark run, FastAPI wrapper (`POST /intel/assess`, `/health`, `/metrics`).
