"""End-to-end assessment: guard → forecast → detect → project → decide → impact → confidence → explain.
Produces the CONTRACT-INTEL-OUTPUT object (docs/ml-architecture.md §8). Stateful only for the
detector/online-error memory; everything else is recomputed from the snapshot each tick."""
from __future__ import annotations

import json
import math
import os
import time
from collections import defaultdict, deque

import numpy as np

from shared import heuristic, schedule
from shared.projection import Move, network_cover, project
from shared.snapshot import Snapshot

from . import policy_lp
from .detect import CusumDetector
from .forecast import ProfileForecaster
from .signals import SignalTracker, SupplyHistory, allocations_at_risk, reconcile_inventory

DEFAULT_ART = os.path.join(os.path.dirname(os.path.abspath(__file__)), "artifacts", "profile-v1")


class InvalidInput(ValueError):
    """Snapshot failed validation: Core must reject it and raise an alert (REQ-009b)."""


def _phi(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def validate(snap: Snapshot) -> None:
    errs = []
    for s in snap.stations.values():
        for f, v in s.inventory.items():
            if v < 0 or v > s.capacity.get(f, math.inf) * 1.01:
                errs.append(f"station {s.id} {f} inventory {v} outside [0, capacity]")
    for d in snap.depots.values():
        for f, v in d.inventory.items():
            if v < 0 or v > d.capacity.get(f, math.inf) * 1.01:
                errs.append(f"depot {d.id} {f} inventory {v} outside [0, capacity]")
    for r in snap.routes.values():
        if r.source_depot_id not in snap.depots or r.destination_station_id not in snap.stations:
            errs.append(f"route {r.id} references unknown entity")
        if r.transit_ticks < 0 or r.max_shipment <= 0:
            errs.append(f"route {r.id} has invalid transit/max_shipment")
    if errs:
        raise InvalidInput("; ".join(errs))


class Assessor:
    def __init__(self, artifact_dir: str = DEFAULT_ART, horizon: int = 96, risk_window: int = 96,
                 tick_minutes: int = 15, priorities: dict | None = None):
        """priorities: station_id -> weight (default 1.0) for priority-based allocation."""
        self.artifact_dir, self.priorities = artifact_dir, priorities or {}
        self.model = ProfileForecaster.load(artifact_dir)
        with open(os.path.join(artifact_dir, "detector.json")) as fh:
            dj = json.load(fh)
        sigma = {tuple(k.split("|")): v for k, v in dj["sigma"].items()}
        self.tracker = SignalTracker(CusumDetector(sigma=sigma, k=dj["k"], h=dj["h"]))
        with open(os.path.join(artifact_dir, "metrics.json")) as fh:
            self.test_mape = json.load(fh)["test"]["multi_step_profile"]["mape_pct"] / 100
        self.H, self.W, self.tick_minutes = horizon, risk_window, tick_minutes
        self.recent = defaultdict(lambda: deque(maxlen=8))       # (s,f) -> (actual, expected)
        self.abs_err = defaultdict(lambda: deque(maxlen=16))     # online relative error
        self.first_tick: int | None = None
        self.last_tick: int | None = None
        self.prev_snap: Snapshot | None = None
        self.supply = SupplyHistory()

    def _reset_state(self) -> None:
        """Simulator was reset (tick went backwards): forget all per-run memory, keep the model."""
        with open(os.path.join(self.artifact_dir, "detector.json")) as fh:
            dj = json.load(fh)
        sigma = {tuple(k.split("|")): v for k, v in dj["sigma"].items()}
        self.tracker = SignalTracker(CusumDetector(sigma=sigma, k=dj["k"], h=dj["h"]))
        self.recent.clear(); self.abs_err.clear()
        self.first_tick = self.last_tick = None
        self.prev_snap, self.supply = None, SupplyHistory()

    # ------------------------------------------------------------------ helpers
    def _expected_now(self, snap: Snapshot, s: str, f: str, tick: int) -> float:
        """Expected demand for an observed row at `tick`, using the multiplier that was in force on
        that tick. The previous snapshot's schedule knows events starting at, or ending after, its
        tick (events are active start..end inclusive), so it is exact for rows at prev.tick."""
        prev = self.prev_snap
        if prev is not None and prev.tick == tick and s in prev.stations:
            mult = float(schedule.multiplier_schedule(prev, s, 1)[0])
        else:
            mult = snap.stations[s].demand_multiplier
        return float(self.model.point([s], [f], [tick % 96], [mult])[0])

    def _risk_prob(self, snap, key, demand50, spread, inbound_extra=None) -> float:
        s, f = key
        if snap.stations[s].status != "OPEN":
            return 1.0
        W = min(self.W, self.H)
        k = np.arange(1, W + 1)
        mean = np.cumsum(demand50[key][:W])
        from shared.projection import inbound_schedule
        inb = inbound_schedule(snap, W).get(key, np.zeros(W))
        if inbound_extra is not None:
            inb = inb + inbound_extra[:W]
        avail = snap.stations[s].inventory.get(f, 0.0) + np.cumsum(inb)
        sd = np.maximum(mean * spread / np.sqrt(k), 1e-6)
        return float(max(_phi((m - a) / sd_) for m, a, sd_ in zip(mean, avail, sd)))

    def _hours(self, ticks):
        return None if ticks is None else round(ticks * self.tick_minutes / 60, 2)

    # --------------------------------------------------------------------- main
    def assess(self, snap: Snapshot, demand_rows: list[dict] | None = None, policy: str = "heuristic") -> dict:
        """policy: "heuristic" (default — ties the LP on service level with 5x fewer shipments, see
        artifacts/replay_*.json), "lp" (optimiser; falls back to heuristic on failure), "fallback"."""
        t_start = time.perf_counter()
        was_reset = self.last_tick is not None and snap.tick < self.last_tick
        if was_reset:
            self._reset_state()
        validate(snap)
        self.first_tick = snap.tick if self.first_tick is None else self.first_tick
        self.last_tick = snap.tick
        H = self.H

        # 1. detect (statistical) + online error
        rows = demand_rows or []
        for r in rows:
            if r["tick"] > self.tracker.last_tick_fed and r["station_id"] in snap.stations:
                e = self._expected_now(snap, r["station_id"], r["fuel_type"], r["tick"])
                self.recent[(r["station_id"], r["fuel_type"])].append((r["demand_liters"], e))
                if e > 0:
                    self.abs_err[(r["station_id"], r["fuel_type"])].append(abs(r["demand_liters"] / e - 1))
        signals = self.tracker.feed_demand(
            [r for r in rows if r["station_id"] in snap.stations],
            lambda s, f, t: self._expected_now(snap, s, f, t))
        active = {k for k in self.tracker.detector.active()}
        signals = [{**s_, "entity_id": f'{s_["station_id"]}|{s_["fuel_type"]}', "severity": "high"} for s_ in signals]
        signals += [{"type": "demand_anomaly_active", "entity_id": f"{k[0]}|{k[1]}", "severity": "medium",
                     "since_tick": v.since_tick, "direction": v.alarm} for k, v in self.tracker.detector.active().items()]
        signals += self.tracker.state_signals(snap)
        if was_reset:
            signals.append({"type": "simulation_reset", "entity_id": "simulator", "severity": "medium"})
        # abnormal inventory changes (needs the previous tick's snapshot + served litres)
        prev = self.prev_snap
        if prev is not None:
            served = {(r["station_id"], r["fuel_type"]): float(r["served_liters"]) for r in rows
                      if r.get("tick") == prev.tick and "served_liters" in r}
            if served:
                signals += reconcile_inventory(prev, snap, served)
        # transport-failure prediction + supply arrival outlook
        signals += allocations_at_risk(snap)
        self.supply.update(snap)
        supply_outlook = self.supply.outlook(snap, self.tick_minutes)
        self.prev_snap = snap

        # 2. forecast
        d50, d90, spread, fc_out = {}, {}, {}, []
        for s, st in snap.stations.items():
            m = schedule.multiplier_schedule(snap, s, H)
            for f in st.capacity:
                key = (s, f)
                if key not in self.model.profile:
                    continue                      # unknown series: no forecast (REQ-041 safe)
                rec = self.recent[key]
                ratio = self.model.ewma_ratio(np.array([a for a, _ in rec]), np.array([e for _, e in rec]),
                                              anomaly_active=key in active)
                fc = self.model.forecast(s, f, snap.tick, H, multiplier_schedule=m, ratio=ratio)
                d50[key], d90[key] = np.array(fc["q50"]), np.array(fc["q90"])
                rq = self.model.resid_q[key]
                spread[key] = (rq[0.90] - rq[0.10]) / 2.563
                fc_out.append({"station_id": s, "fuel_type": f, "horizon": H, "ratio": round(ratio, 3),
                               "q10": np.round(fc["q10"][:16], 2).tolist(), "q50": np.round(fc["q50"][:16], 2).tolist(),
                               "q90": np.round(fc["q90"][:16], 2).tolist(),
                               "cum_q50_24h": round(fc["cum_q50"][-1], 1)})

        # 3. project + network state
        base = project(snap, d50, H)
        pess = project(snap, d90, H)
        cover = network_cover(snap, d50, H)
        for f, c in cover.items():
            if c["systemic_shortage"]:
                signals.append({"type": "systemic_shortage", "entity_id": f, "severity": "high", "evidence": c})
        for (d, f), ov in base.overflow.items():
            if ov.sum() > 0:
                signals.append({"type": "overflow_risk", "entity_id": f"{d}|{f}", "severity": "medium",
                                "evidence": {"liters": round(float(ov.sum()), 1),
                                             "first_offset": int(np.nonzero(ov)[0][0])}})

        # 4. decide
        policy_used, lp_info = policy, {}
        moves: list[Move] = []
        if policy == "lp":
            try:
                r = policy_lp.solve(snap, d50, H, priorities=self.priorities)
                lp_info = {"status": r.status, "solve_seconds": round(r.solve_seconds, 3)}
                if r.status == "optimal":
                    moves = r.moves
                else:
                    policy_used = "fallback"
            except Exception as ex:   # never let the optimiser take the pipeline down
                lp_info, policy_used = {"status": f"error: {ex}"}, "fallback"
        if policy in ("heuristic", "fallback") or policy_used == "fallback":
            moves = heuristic.plan(snap, d50, H, priorities=self.priorities)
            policy_used = "heuristic" if policy == "heuristic" else "fallback"

        # 5-7. recommendations
        risks = []
        for key in d50:
            so, so90 = base.stockout_offset(key), pess.stockout_offset(key)
            p = self._risk_prob(snap, key, d50, spread[key])
            risks.append({"station_id": key[0], "fuel_type": key[1],
                          "inventory": round(snap.stations[key[0]].inventory.get(key[1], 0), 1),
                          "stockout_in_hours_p50": self._hours(so), "stockout_in_hours_p90": self._hours(so90),
                          "projected_unmet_l_24h": round(float(base.unmet[key].sum()), 1),
                          "stockout_risk": round(p, 3), "risk_window_h": self._hours(self.W),
                          # band by time-to-stockout (operator urgency); probability reported separately
                          "risk": "high" if so is not None and so <= 12 else
                                  "medium" if so is not None and so <= 48 else "low"})
        recs = [self._recommend(snap, m, d50, spread, base, cover, active, policy_used) for m in moves]
        recs.sort(key=lambda r: (-r["impact"]["risk_before"], -r["impact"]["unmet_avoided_l"]))
        bottlenecks = self._bottlenecks(snap, moves, recs, risks, H)
        return {"tick": snap.tick, "model_version": self.model.version, "policy": policy_used,
                "degraded": policy_used == "fallback", "lp": lp_info,
                "latency_ms": round((time.perf_counter() - t_start) * 1000, 1),
                "network": {f: {**c, "mode": "rationing" if c["systemic_shortage"] else "normal"} for f, c in cover.items()},
                "projected": {"unmet_l_24h": round(base.total_unmet(), 1), "overflow_l_24h": round(base.total_overflow(), 1),
                              "unmet_l_24h_with_plan": round(project(snap, d50, H, moves).total_unmet(), 1),
                              "overflow_l_24h_with_plan": round(project(snap, d50, H, moves).total_overflow(), 1)},
                "signals": signals, "risks": sorted(risks, key=lambda r: -r["stockout_risk"]),
                "bottlenecks": bottlenecks, "supply_outlook": supply_outlook,
                "priorities": self.priorities,
                "forecasts": fc_out, "recommendations": recs}

    # ------------------------------------------------------------ bottlenecks
    def _bottlenecks(self, snap, moves, recs, risks, H) -> dict:
        """Where the network is constrained right now: which limits capped this tick's shipments,
        how loaded each depot's dispatch capacity is, and at-risk stations the plan does not fix."""
        kinds = {"route max_shipment": "route_capacity", "stock": "depot_stock",
                 "dispatch left": "depot_dispatch_capacity", "station headroom": "station_tank_space"}
        counts: dict = {}
        for r in recs:
            for b in r["binding_constraints"]:
                k = next((v for key, v in kinds.items() if key in b), "other")
                counts[k] = counts.get(k, 0) + 1
        util = {}
        for d, dp in snap.depots.items():
            used = heuristic.dispatch_used(snap, d)
            planned = sum(m.quantity for m in moves if snap.routes[m.route_id].source_depot_id == d)
            util[d] = round((used + planned) / dp.dispatch_capacity_per_tick, 3)
        covered = {(r["station_id"], r["fuel_type"]) for r in recs}
        unresolved = []
        for rk in risks:
            key = (rk["station_id"], rk["fuel_type"])
            if key in covered or rk["stockout_in_hours_p50"] is None or rk["stockout_in_hours_p50"] > 24:
                continue
            s, f = key
            open_routes = [r for r in snap.routes.values() if r.destination_station_id == s
                           and schedule.route_open(snap, r.id, 1)[0]]
            if snap.stations[s].status != "OPEN":
                why = "station closed"
            elif not open_routes:
                why = "no open route into the station"
            elif all(snap.depots[r.source_depot_id].inventory.get(f, 0) < heuristic.MIN_QTY for r in open_routes):
                why = f"no {f.lower()} left at any depot that can reach it"
            elif all(util.get(r.source_depot_id, 0) >= 0.999 for r in open_routes):
                why = "depot dispatch capacity used up this tick"
            else:
                why = "not urgent yet: will be scheduled closer to the stockout"
            unresolved.append({"station_id": s, "fuel_type": f, "stockout_in_hours": rk["stockout_in_hours_p50"],
                               "reason": why})
        return {"binding_constraint_counts": counts, "depot_dispatch_utilisation": util,
                "at_risk_not_addressed": unresolved}

    # ---------------------------------------------------------------- one rec
    def _recommend(self, snap, m: Move, d50, spread, base, cover, active, policy_used) -> dict:
        H = self.H
        r = snap.routes[m.route_id]; s, f = r.destination_station_id, m.fuel_type
        key = (s, f)
        extra = np.zeros(H)
        if 1 + r.transit_ticks < H:
            extra[1 + r.transit_ticks] = m.quantity
        after = project(snap, d50, H, [m])
        p0, p1 = self._risk_prob(snap, key, d50, spread[key]), self._risk_prob(snap, key, d50, spread[key], extra)
        so0, so1 = base.stockout_offset(key), after.stockout_offset(key)
        u0, u1 = float(base.unmet[key].sum()), float(after.unmet[key].sum())
        ov0, ov1 = base.total_overflow(), after.total_overflow()

        # binding constraints (what capped the quantity)
        dp = snap.depots[r.source_depot_id]
        used = heuristic.dispatch_used(snap, dp.id)
        head = snap.stations[s].capacity[f] - snap.stations[s].inventory.get(f, 0)
        constraints = [f"route max_shipment {r.max_shipment:,.0f} L",
                       f"depot {dp.id} {f} stock {dp.inventory.get(f, 0):,.0f} L",
                       f"depot dispatch left this tick {dp.dispatch_capacity_per_tick - used:,.0f} L",
                       f"station headroom {head:,.0f} L", f"transit {r.transit_ticks} ticks"]
        binding = [c for c, v in ((constraints[0], r.max_shipment), (constraints[1], dp.inventory.get(f, 0)),
                                  (constraints[2], dp.dispatch_capacity_per_tick - used), (constraints[3], head))
                   if abs(v - m.quantity) <= 10]

        # alternatives: every other open route into this station
        alts = []
        for r2 in snap.routes.values():
            if r2.id == r.id or r2.destination_station_id != s or not schedule.route_open(snap, r2.id, 1)[0]:
                continue
            q2 = float(np.floor(min(m.quantity, r2.max_shipment, snap.depots[r2.source_depot_id].inventory.get(f, 0)) / 10) * 10)
            if q2 < heuristic.MIN_QTY:
                alts.append({"route_id": r2.id, "source_depot_id": r2.source_depot_id, "quantity": 0,
                             "why_not": "insufficient depot stock"})
                continue
            a2 = project(snap, d50, H, [Move(r2.id, f, q2, 0)])
            alts.append({"route_id": r2.id, "source_depot_id": r2.source_depot_id, "quantity": q2,
                         "arrives_in_hours": self._hours(1 + r2.transit_ticks),
                         "unmet_after_l": round(float(a2.unmet[key].sum()), 1),
                         "why_not": f"slower ({r2.transit_ticks} vs {r.transit_ticks} ticks)" if r2.transit_ticks > r.transit_ticks
                         else "draws on a depot the plan needs elsewhere"})
        alts.append({"route_id": None, "quantity": 0, "why_not": "do nothing", "unmet_after_l": round(u0, 1)})

        # confidence gate
        conf, reasons = 1.0, []
        if key in active:
            conf *= 0.6; reasons.append("unexplained demand shift detected at this station")
        touching = [e for e in snap.events if e.status in ("ACTIVE", "SCHEDULED") and (
            s in (e.parameters.get("station_ids") or []) or r.id in (e.parameters.get("route_ids") or [])
            or dp.id in (e.parameters.get("depot_ids") or []) or snap.stations[s].region_id in (e.parameters.get("region_ids") or [])
            or not any(e.parameters.get(k) for k in ("station_ids", "route_ids", "depot_ids", "region_ids")))]
        if touching:
            conf *= 0.8; reasons.append(f"active/scheduled event: {', '.join(sorted({e.type for e in touching}))}")
        if snap.stale:
            conf *= 0.5; reasons.append("simulator data flagged stale")
        if snap.tick - (self.first_tick or snap.tick) < 8:
            conf *= 0.8; reasons.append("short history since start/reset")
        errs = self.abs_err[key]
        if len(errs) >= 8 and np.mean(errs) > 2 * self.test_mape:
            conf *= 0.6; reasons.append(f"recent forecast error {np.mean(errs):.0%} > 2x normal")
        hard = []
        if policy_used == "fallback":
            hard.append("fallback policy in use (intelligence degraded)")
        if cover.get(f, {}).get("systemic_shortage"):
            hard.append(f"{f.lower()} is in network-wide shortage (rationing decision)")
        if m.quantity > 0.5 * max(dp.inventory.get(f, 0), 1):
            hard.append("uses more than half of the depot's remaining stock")
        review = "HUMAN_REVIEW" if hard or conf < 0.6 else "AUTO_ELIGIBLE"

        exp_d = float(d50[key][:self.W].sum())
        text = (f"{s} {f.title()}: projected stockout in "
                f"{self._hours(so0) if so0 is not None else '>' + str(self._hours(H))} h; expected demand next "
                f"{self._hours(self.W)} h {exp_d:,.0f} L. Recommend {m.quantity:,.0f} L from {dp.id} via {r.id} "
                f"(arrives in {self._hours(1 + r.transit_ticks)} h). Stockout risk (next {self._hours(self.W):g} h) {p0:.0%} → {p1:.0%}; "
                f"unmet next 24 h {u0:,.0f} → {u1:,.0f} L."
                + (f" Limited by: {', '.join(binding)}." if binding else "") + " [Simulated environment]")
        return {"id": f"rec-{snap.tick}-{s}-{f}-{r.id}", "station_id": s, "fuel_type": f,
                "action": {"source_depot_id": dp.id, "route_id": r.id, "quantity": m.quantity},
                "alternatives": alts, "constraints": constraints, "binding_constraints": binding,
                "signals": [x for x in reasons],
                "impact": {"stockout_before_h": self._hours(so0), "stockout_after_h": self._hours(so1),
                           "unmet_before_l": round(u0, 1), "unmet_after_l": round(u1, 1),
                           "unmet_avoided_l": round(u0 - u1, 1),
                           "overflow_before_l": round(ov0, 1), "overflow_after_l": round(ov1, 1),
                           "risk_before": round(p0, 3), "risk_after": round(p1, 3)},
                "confidence": round(conf, 2), "review": review,
                "review_reasons": hard + reasons if review == "HUMAN_REVIEW" else [],   # why a human must approve
                "confidence_notes": reasons,                                            # caveats that lowered confidence
                "policy": policy_used, "explanation": text}
