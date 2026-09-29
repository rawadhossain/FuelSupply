# Intelligence (ML) Architecture — v3, PROPOSED as ADR-007 (2026-09-29)

v3 = v2 + the online research and benchmarks in §12 (primary decision engine is now an LP via SciPy/HiGHS, intervals are conformal). Changes from v1 are listed in §9, v2→v3 changes in §12. Covers REQ-007, 008, 009a/c, 011, 018, 019, 028, 029, 035 (Requirements Rev 2).

---

## 1. What the data says the problem really is

Measured on our baseline export (seed 12345, 2000 ticks) — see `dataset/ml/`:

| Fact | Evidence | Consequence for design |
|---|---|---|
| Normal demand is a fixed seasonal pattern + small noise | Time-of-day profile from days 1–13 predicts days 17–20 at **MAPE 5.2%**; per-slot CV ≈ 5.7% | Heavy forecasting ML has little headroom. A profile table is enough for the base forecast. |
| **Supply is finite and front-loaded** | All 22 arrivals land by tick 212 (~day 2.2), total **216,000 L**. Network demand ≈ **93,000 L/day** (D 41k, P 34k, O 18k) | After ~day 2 the network only drains. The core decision is **rationing scarce fuel**, not just topping up. |
| **Depot overflow wastes supply** | With no allocations, all 6 depot×fuel inventories ended **exactly at capacity**; e.g. Gazipur diesel 60k + 54k arrivals vs 90k cap | Arrivals into a full depot are lost. Pushing fuel to stations **before** an arrival is a decision with measurable value. |
| Lead times are short | Route transit 2–4 ticks (30–60 min) | Station stockout timing matters less than network-level allocation over hours/days. |
| Stations hold ~1–2 days | e.g. Mirpur diesel cap 15,000 vs ~8,500 L/day | Station capacity is a binding constraint on "pre-positioning". |
| Events are visible **before** they start | `/v1/events` lists SCHEDULED events with `start_tick`/`end_tick`; `demand_multiplier` is on each station | Forecast can include known future spikes; **predict** shortage, not just react. |
| 5 of 6 event types don't touch demand | outage, route, depot, delay, shortfall are state changes | Detection must be state-based as well as statistical. |

**Design principle:** use arithmetic where the simulator is deterministic, statistics where it is noisy, and rules or optimisation for decisions. Spend ML effort on decision quality, measured by service level, rather than on forecast decimals.

---

## 2. Pipeline

```
Core snapshot (entities, inventories, in-transit allocations, arrivals, events, persisted demand history)
   │  POST /intel/assess   (once per new tick, latest-wins, 2 s timeout)
   ▼
[0] GUARD      schema + sanity (non-negative, known ids, tick monotonic); stale/gap flags ─► reject+alert (009b)
[1] FORECAST   E[d(s,f,t+k)] = P(s,f,tod) × M(s,t+k) × R(s,f)            k = 1..H (H = 96 ticks = 24 h)
               P = baked time-of-day profile   M = multiplier schedule from events   R = undeclared-shift correction
               Interval: split-conformal — empirical quantiles of relative residuals on the val set; cumulative q10/q50/q90
[2] DETECT     statistical: CUSUM on log(actual / expected)  → undeclared demand anomalies
               state-diff : OUTAGE, DISRUPTED, CONSTRAINED, arrival DELAYED, arrival quantity ↓, new/ended events
               supply-side: depot overflow risk, network cover < horizon ("systemic shortage")
               data      : stale header, missing ticks
[3] PROJECT    two-echelon inventory roll-forward (deterministic):
               depot(t+1)   = min(cap, depot − dispatched + arrivals)  → overflow_loss
               station(t+1) = min(cap, station − q50 demand + in-transit arriving)  → stockout tick, unmet L
               network cover per fuel = (Σ depot + Σ station + in-transit + future arrivals) / Σ forecast
[4] DECIDE     primary: rolling-horizon LP (scipy.optimize.linprog, HiGHS), H=96, re-solved each tick (MPC)
               fallback: simple urgency heuristic in shared.allocator (no solver), see §4
[5] IMPACT     re-run [3] with the plan applied → before/after: stockout tick, unmet L, overflow L, risk band
[6] CONFIDENCE score + reasons → AUTO_ELIGIBLE | HUMAN_REVIEW  (see §5)
[7] EXPLAIN    template from the structured object; optional LLM rewrite (numbers never from LLM)
```

Everything is vectorised NumPy over 12 station×fuel series and 6 depot×fuel series × H ticks. Expected latency is under 20 ms, so no model server, GPU or feature store is needed.

---

## 3. Forecast details (REQ-007a, 029)

| Term | How | Why |
|---|---|---|
| **P** profile | Mean demand per station × fuel × tick-of-day (96 slots) over training days, **with the region factor and multiplier divided out** (so P is base demand). Baked into the image as `profile_v1.parquet` + `metrics.json`. | The world is identical across scenarios (guide §8), so it works from tick 0 and cold start is solved. |
| **M** schedule | `station.demand_multiplier` now; for future ticks apply each SCHEDULED/ACTIVE `demand_spike` event from `/v1/events` over [start_tick, end_tick), including region filters resolved to stations. | Anticipates known spikes and knows when they end. |
| **R** correction | EWMA over the last 8 ticks of actual/(P·M), clipped to [0.5, 3], decays back to 1 over the horizon. | Catches spikes the simulator didn't announce. There's no double counting, because the ratio is taken after M. |
| Intervals | **Split-conformal:** take relative residuals (actual/forecast − 1) on `demand_val.csv` per series; q10/q90 = empirical quantiles. Cumulative interval over k ticks via √k scaling of the residual quantiles (independent noise). | Distribution-free, no Gaussian assumption, ~10 lines of NumPy; coverage checked on test. |
| Not used | statsforecast MSTL, LightGBM, XGBoost — benchmarked in §12, no worthwhile gain | Fewer dependencies, faster build |

Evaluation (REQ-029): MAE/MAPE and 80% interval coverage on `demand_test.csv`; the same numbers during crisis windows on `crisis_demand_labeled.csv`, compared with a naive "same slot yesterday" forecast.

---

## 4. Decision policy (REQ-007c, 008, 028)

> **Implementation decision (§13):** after building both, the replay benchmark shows the heuristic ties the LP on service level with ~5x fewer shipments and ~10x lower latency. **Default policy = heuristic**; the LP remains selectable (`policy="lp"`) and is shown as the optimisation comparator. Re-decide after the live benchmark (`run_benchmark.bat`).

The objective is to maximise projected served litres over horizon H while minimising overflow waste, without starving any station.

**Primary: rolling-horizon LP (model predictive control).** It is solved with `scipy.optimize.linprog(method="highs")`. SciPy is already a dependency, so there's no extra solver.

| Element | Definition |
|---|---|
| Decision vars | `x[route,fuel,t]` shipment, `srv[station,fuel,t]` served, `I[station,fuel,t]`, `Dp[depot,fuel,t]`, `ov[depot,fuel,t]` overflow; t = 0..H−1 |
| Balance | station: `I_t = I_{t−1} + Σ x[r, t−transit_r] − srv_t` (in-transit allocations enter as constants); depot: `Dp_t = Dp_{t−1} − Σ x_t + arrivals_t − ov_t` |
| Bounds | `srv ≤ q50 demand × M` (event schedule), `I ≤ station cap`, `Dp ≤ depot cap`, `x ≤ route max_shipment`, `x = 0` if route DISRUPTED / station OUTAGE / depot closed during the event window |
| Coupling | `Σ_{routes from depot, fuels} x_t ≤ dispatch_capacity_per_tick` |
| Objective | max `Σ w_s·srv − λ·Σ ov + μ·Σ terminal inventory` (terminal value stops end-of-horizon dumping; `w_s` = optional station priority; λ = 0.1, μ = 0.05 to start) |
| Fairness | add `u[s,f] ≥ unmet share` and penalise `max u` (minimax term) so scarcity is shared rather than concentrated |
| Execute | only the moves for t = 0 (and t = 1 as preview); re-solve next tick with fresh state |

Measured: H = 96 gives 5,184 variables and 1,920 constraints, and **solves in 0.077 s** (prototype `dataset/benchmarks/lp_prototype.py`). Rules v2 needed as separate code — overflow push, rationing, joint depot/route choice and dispatch coupling — all fall out of the one model.

**Explainability (REQ-008).** Each LP move becomes a recommendation. The **binding constraints** come from the solution (slack = 0 on dispatch, route max, capacity). The **impact** comes from re-running the projection with and without the move. The **alternative** is the best feasible move on another route/depot (re-solve with the chosen arc fixed at 0, about 0.08 s), or the heuristic's choice. Shadow prices (`res.eqlin.marginals`) give "value per litre" at each station for the explanation.

**Fallback: heuristic** (`shared/allocator`, pure Python, no SciPy; Core imports it when Intelligence is down or the LP is infeasible or times out):
1. Filter eligible routes in the simulator's validation order.
2. Rank station×fuel by `slack = stockout_tick − now − transit`.
3. Ship `min(constraints, fill to 1 day of forecast demand)` from the shortest-transit depot with stock.

It always carries HUMAN_REVIEW.

**Benchmark (REQ-028):** no action vs heuristic vs LP, with the same seed and events via `/admin/reset` + `/admin/step`. Report service_level, unmet L, overflow L and allocation failures.

## 5. Confidence and human review (REQ-009c, 019)

Under normal conditions forecast intervals are narrow (about 1.5% over 16 ticks), so interval width would never trigger review. The real uncertainty comes from **regime** and **data** problems. The confidence score starts at 1.0 and is multiplied down by each factor below. A recommendation goes to HUMAN_REVIEW if the score is below 0.6 **or** any hard rule is hit.

| Factor | Effect |
|---|---|
| CUSUM alarm on the series (undeclared shift) | ×0.6 |
| Active or scheduled event touching the station, route or depot | ×0.8 |
| Stale data or a gap of more than 2 ticks | ×0.5 |
| Series history < 8 ticks since the last reset (R not settled) | ×0.8 |
| Online MAPE over the last 16 ticks > 2× the test MAPE | ×0.6 |
| **Hard rule:** fallback policy in use, or scarcity mode (rationing is a consequential decision), or quantity > 50% of the depot's remaining stock of that fuel | HUMAN_REVIEW |

Every factor that fired is returned as a readable reason string, which the UI and audit log both show.

---

## 6. Detection outputs (REQ-018, 011)

| Signal | Rule | Crisis it covers |
|---|---|---|
| `demand_anomaly` | Two-sided CUSUM on log residual with k = 0.5·cv and h = 5·cv; also fires on declared spikes for a visible alert | demand spike (declared or not) |
| `station_outage` | status OUTAGE, or served = 0 while inventory > 0 | regional disruption |
| `route_disrupted` | route status change | regional disruption |
| `depot_constrained` | depot status change | depot constraint |
| `supply_delayed` / `supply_shortfall` | an arrival's planned_tick increases or its quantity drops compared with the first time it was seen | shipment delay, supply shortfall |
| `overflow_risk` | projection shows depot overflow within H | supply waste |
| `systemic_shortage` | network cover per fuel < H | combined crisis / scarcity |
| `data_stale` / `data_gap` | header / missing ticks | software failure |

Evaluation: precision, recall and ticks-to-detect on `crisis_demand_labeled.csv` (run `get_crisis_dataset.bat` first).

---

## 7. Fallback, versioning and metrics

- **Fallback (009a):** Core calls `shared.allocator.plan(snapshot)` with the baked profile only (P × M, no R). It carries `policy:"fallback"` and HUMAN_REVIEW and needs no network.
- **Versioning:** `intelligence/artifacts/<version>/{profile.parquet, metrics.json}`; version shown in the API response and UI; a missing artifact triggers degraded mode.
- **Metrics (REQ-013):** `intel_forecast_mape{series}`, `intel_interval_coverage`, `intel_confidence`, `intel_signals_total{type}`, `intel_recommendations_total{policy,review}`, `intel_fallback_total`, `intel_projected_unmet_liters`, `intel_projected_overflow_liters`, `intel_assess_latency_seconds`.

## 8. Contract (CONTRACT-INTEL-OUTPUT)

`POST /intel/assess` returns `{tick, model_version, degraded, forecasts[], signals[], risks[], network{fuel: cover_ticks, mode}, recommendations[]}`. Each recommendation contains `{id, station_id, fuel_type, action{depot, route, qty}, alternatives[], signals[], constraints[], impact{stockout_before/after, unmet_before/after, overflow_before/after, risk_before/after}, confidence, review, review_reasons[], policy, explanation}`. The full JSON example is in v1 §4 and is unchanged except for the added `network`, `overflow_*` and `signals` fields.

## 9. Changes from v1 and why

| v1 | v2 | Why |
|---|---|---|
| Top-up heuristic (rank by hours-to-stockout) | + overflow push + scarcity/rationing mode | Supply is finite and front-loaded; overflow loses fuel |
| Horizon 16 ticks (4 h) | 96 ticks (24 h) for projection; per-tick forecast is still cheap | Rationing and overflow play out over hours to days, and lead times are only 2–4 ticks |
| Multiplier only as it is now | Multiplier **schedule** from SCHEDULED/ACTIVE events with end ticks | Predicts before the spike starts and knows when it ends |
| EWMA ratio against the base profile | Ratio against P×M, clipped and decaying | Avoids double counting a declared spike |
| Per-slot std (13 samples) | Pooled CV per series; cumulative σ over horizon | Stable intervals; stockout needs cumulative demand |
| Confidence mostly from interval width | Regime and data factors plus hard rules | Width is almost always tiny, so the rule would never fire |
| z-score anomaly | CUSUM on log residual | Catches subtle ×1.3 spikes within a few ticks |
| Station-only projection | Two-echelon (depot + station) plus network cover | Overflow and systemic shortage are depot/network effects |
| Forecast MAPE as evidence | Policy benchmark as headline, MAPE as supporting | Matches "decision quality" in the brief |

## 10. Build order (ML critical path)

1. `profile_v1` + conformal intervals + forecast (P × M × R); tests on `demand_test.csv`
2. Two-echelon projection; test it against the simulator after N `/admin/step`s with known allocations
3. `shared.allocator` urgency heuristic → the fallback is done first
4. LP policy from `dataset/benchmarks/lp_prototype.py` → wire it to the live snapshot, events and in-transit
5. Detection signals + confidence gate + recommendation object (binding constraints, alternatives, impact)
6. Benchmark (REQ-028): no action vs heuristic vs LP
7. Stretch: LLM explanation, fairness weights tuning

## 11. Open checks (verify before relying on them)

- **Overflow semantics:** confirm by `/admin/step` past an arrival into a near-full depot, then read `/admin/audit` for `supply.arrived` metadata. If the excess is rejected rather than capped, the logic is the same but the metric name changes.
- **Outage demand:** confirm whether demand is still recorded (as unmet) during `station_outage`, which affects the detector rule.
- **Other seeds:** the world is fixed but seeds change the noise; re-check profile MAPE on one other seed if one is available.

## 12. Online alternatives researched and benchmarked (v2 → v3)

Benchmarks were run on our own export (`dataset/benchmarks/forecast_benchmark.py`, `lp_prototype.py`). Test window: days 17–20, forecast from the end of day 16 across the whole window (multi-step), unless noted.

**Forecasting**

| Option | MAE | MAPE | Cost | Verdict |
|---|---|---|---|---|
| **Time-of-day profile (ours)** | **4.23** | **5.17%** | 1 groupby, ~0 ms | **Keep** |
| statsforecast MSTL(season 96) — [Nixtla/statsforecast](https://github.com/Nixtla/statsforecast) | 4.30 | 5.28% | 3.2 s fit; heavy deps (numba) | No gain |
| Seasonal naive, last day | 5.57 | 6.73% | trivial | Worse; used as the baseline |
| LightGBM, 1-step with lag_1 (easier task) | 4.14 | 5.06% | training + model artifact + dependency | +2% on 1-step only, not multi-step; not worth it |

MSTL ([statsmodels](https://www.statsmodels.org/dev/examples/notebooks/generated/mstl_decomposition.html)) is the standard tool for multiple seasonality, but our series has a single daily cycle that the simulator defines. The profile *is* that decomposition, without the dependency.

**Prediction intervals.** Conformal prediction ([MAPIE docs](https://mapie.readthedocs.io/en/stable/generated/regression/1-quickstart/plot_ts-tutorial/)) gives distribution-free intervals. Split-conformal on validation residuals is a few lines of NumPy, so **adopted without adding MAPIE** as a dependency.

**Change / anomaly detection.** Considered [river ADWIN](https://riverml.xyz/dev/api/drift/ADWIN/) (streaming drift) and [ruptures](https://github.com/deepcharles/ruptures) (offline change points). ruptures is offline, and ADWIN detects drift in a mean stream but doesn't use our known expected value. **Keep CUSUM on log(actual/expected)**: about 15 lines, streaming, uses the forecast directly. ADWIN is an optional second opinion.

**Decision engine.** Solvers compared: [SciPy linprog/HiGHS](https://docs.scipy.org/doc/scipy/reference/optimize.linprog-highs.html), PuLP/CBC, OR-Tools ([overview](https://realpython.com/linear-programming-python/)). HiGHS ships inside SciPy, so it needs no new dependency and is fast. Rolling-horizon LP / MPC is the established approach for multi-echelon inventory ([MPC for multi-echelon supply chains](https://www.sciencedirect.com/science/article/abs/pii/S0377221716000977), [economic MPC for inventory](https://www.sciencedirect.com/science/article/abs/pii/S0098135414000052)). The measured solve is **0.077 s** at H = 96, so **LP becomes the primary policy** and replaces the v2 rule set. The heuristic stays as the fallback.

**Reinforcement learning:** not pursued. Deterministic dynamics plus a 0.08 s exact LP leave RL nothing to learn within the hackathon time, and the brief requires beating a rule/heuristic baseline (Brief §8).

**Final dependency set for Intelligence:** `numpy`, `pandas`, `scipy`, `pydantic`, `fastapi`, `prometheus-client` (+ `anthropic` optional). No XGBoost, LightGBM, OR-Tools or statsforecast.

## 13. Implementation results (2026-09-29)

Code: `shared/` (snapshot, schedule, projection, heuristic) and `intelligence/` (forecast, detect, signals, policy_lp, assess, replay, benchmark). 22 tests pass.

**Projection validated on real simulator data:** with the recorded demand and scenario start inventories, the projection reproduces the simulator's first-unmet tick for **12/12** station × fuel series. With no dispatches, all depots end at capacity, as in the export (73,000 L of arrivals lost to overflow).

**Closed-loop replay** (`python -m intelligence.replay`): recorded simulator demand, exact validated stock semantics. The crisis runs apply the documented event effects. These are **offline estimates**; the authoritative numbers come from `run_benchmark.bat` against the real simulator.

| Scenario | Policy | Service level | Unmet L | Overflow L | Stockout series-hours | Allocations |
|---|---|---|---|---|---|---|
| Baseline 3 d | none | 30.7% | 193,586 | 73,000 | 579.5 | 0 |
| | heuristic | **100%** | 0 | 1,350 | 0 | 40 |
| | LP | **100%** | 0 | 0 | 0 | 260 |
| Baseline 7 d | none | 13.2% | 565,414 | 73,000 | 1,731.5 | 0 |
| | heuristic | 84.8% | 98,764 | 1,350 | 280.8 | 86 |
| | LP | 85.0% | 97,744 | 0 | 277.0 | 443 |
| Crisis 3 d | none | 27.6% | 225,048 | 73,000 | 587.0 | 0 |
| | heuristic | 99.9% | 356 | 0 | 0.2 | 42 |
| | LP | 100% | 0 | 0 | 0 | 273 |
| Crisis 7 d | none | 12.3% | 614,729 | 73,000 | 1,739.0 | 0 |
| | heuristic | 79.1% | 146,729 | 0 | 402.8 | 92 |
| | LP | 79.0% | 147,515 | 0 | 408.0 | 424 |

Findings:
1. **Any policy vs none is the headline.** Service level goes from 13–31% to 79–100%, and 73,000 L of overflow waste is avoided.
2. **The ceiling is total supply.** Only 553,900 L ever exist (initial stock + 22 arrivals). Over 7 days the heuristic serves every litre in the crisis run, and the LP serves 99.9% in baseline. No policy can do better after supply ends (~day 3).
3. **LP vs heuristic is a tie** (±0.15 pp). The LP makes 5x more, smaller shipments. Under human-in-the-loop (REQ-019) that is 5x more approvals, so the heuristic is the default.
4. **Bug found and fixed by the HTTP dry run:** at an event's end tick, the live DISRUPTED status must win at offset 0 (2 → 0 rejected allocations).

Latency (single run): heuristic assess p95 ≈ 9 ms, LP ≈ 100 ms (LP solve 0.05–0.1 s).

## 14. Live results and findings from the real simulator (2026-09-29)

**Live benchmark** (`run_benchmark.bat`, 288 ticks = 3 days):

| Scenario | Policy | Service level | Unmet L | Allocations (all HTTP 201) | Assess p95 |
|---|---|---|---|---|---|
| Baseline | none | 30.7% | 193,586 | 0 | — |
| | heuristic | **100%** | 0 | 40 | 17.5 ms |
| | LP | **100%** | 0 | 258 | 131 ms |
| Crisis | none | 27.5% | 226,087 | 0 | — |
| | heuristic | 99.89% | 356 | 44 | 13.5 ms |
| | LP | **100%** | 0 | 271 | 120 ms |

In the live crisis run the LP is slightly better (356 L less unmet), at about 6× more shipments. **Default remains the heuristic** because of the human-approval load (REQ-019). Use the LP when the operator enables auto-mode, or in a crisis where every litre matters. Team decision.

**Detector on real crisis data** (`python -m intelligence.eval_crisis`): unannounced spikes were caught in 24/24 affected series within 1–2 ticks, with 0 false alarms. Announced spikes raised 0 alarms, because the forecast already includes them. Forecast error during spikes was 4.7% with the multiplier schedule, against 58% without.

**Simulator facts learned and built in:**
1. Events are active for ticks **start..end inclusive**. A route disrupted from tick S is still AVAILABLE when you submit at tick S, but the truck departs at S+1 and would FAIL. The schedule therefore treats the route as closed from offset 0.
2. During a station outage, demand is still recorded, served = 0 and everything counts as unmet.
3. The replay (`intelligence/replay.py`) now reproduces live results within 0.2% (identical in baseline). Use it to rehearse demo scenarios without resetting the simulator.
