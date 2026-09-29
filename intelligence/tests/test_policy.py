"""Tests for projection, policies and the assessment pipeline.
Run from repo root:  python -m pytest intelligence/tests -q"""
import os

import pandas as pd
import pytest

from intelligence import heuristic, policy_lp, schedule
from intelligence.assess import Assessor, InvalidInput
from intelligence.projection import project
from intelligence.replay import ROOT, ReplaySim, initial_snapshot
from intelligence.snapshot import Event

DEM = os.path.join(ROOT, "dataset", "ml", "demand_full_features.csv")
pytestmark = pytest.mark.skipif(not os.path.exists(DEM), reason="needs dataset/ml CSVs")


@pytest.fixture(scope="module")
def demand_df():
    return pd.read_csv(DEM)


def recorded_demand(df, H=200):
    return {(s, f): g.sort_values("tick").demand_liters.to_numpy()[:H]
            for (s, f), g in df[df.tick < H].groupby(["station_id", "fuel_type"])}


def test_projection_reproduces_simulator_stockout_ticks(demand_df):
    """Real-data check: with recorded demand and no allocations, the first unmet tick per series
    must equal what the simulator produced (its served/unmet columns)."""
    snap, H = initial_snapshot(), 200
    P = project(snap, recorded_demand(demand_df, H), H)
    for (s, f), g in demand_df[demand_df.tick < H].groupby(["station_id", "fuel_type"]):
        sim_first = int(g[g.unmet_liters > 0].tick.min())
        assert P.stockout_offset((s, f)) == sim_first, (s, f)


def test_depot_overflow_matches_export(demand_df):
    """Without dispatches every depot ends at capacity (as in the real export) and excess is overflow."""
    snap = initial_snapshot()
    P = project(snap, recorded_demand(demand_df, 250), 250)
    for (d, f), inv in P.depot_inv.items():
        assert inv[-1] == pytest.approx(snap.depots[d].capacity[f])
    assert P.total_overflow() == pytest.approx(73000)


def feasible(snap, moves):
    used = {}
    for m in moves:
        r = snap.routes[m.route_id]; st = snap.stations[r.destination_station_id]; dp = snap.depots[r.source_depot_id]
        assert r.status == "AVAILABLE" and st.status == "OPEN"
        assert 0 < m.quantity <= r.max_shipment
        used[dp.id] = used.get(dp.id, 0) + m.quantity
        assert used[dp.id] <= dp.dispatch_capacity_per_tick + 1e-6
        assert m.quantity <= dp.inventory[m.fuel_type] + 1e-6
        assert st.inventory[m.fuel_type] + m.quantity <= st.capacity[m.fuel_type] + 1e-6


def advanced_snapshot(demand_df, ticks=60):
    sim = ReplaySim(demand_df)
    for _ in range(ticks):
        sim.step()
    return sim.snap


def test_heuristic_moves_are_feasible_and_urgent(demand_df):
    snap = advanced_snapshot(demand_df, 60)
    d = recorded_demand(demand_df.assign(tick=demand_df.tick - 60).query("tick >= 0"), 96)
    moves = heuristic.plan(snap, d, 96)
    assert moves, "tongi diesel runs dry at tick 65 — heuristic must act at tick 60"
    feasible(snap, moves)


def test_lp_optimal_fast_and_feasible(demand_df):
    snap = advanced_snapshot(demand_df, 60)
    d = recorded_demand(demand_df.assign(tick=demand_df.tick - 60).query("tick >= 0"), 96)
    res = policy_lp.solve(snap, d, 96)
    assert res.status == "optimal" and res.solve_seconds < 2.0
    feasible(snap, res.moves)
    assert project(snap, d, 96, res.moves).total_unmet() <= project(snap, d, 96).total_unmet()


def test_lp_respects_scheduled_route_disruption(demand_df):
    snap = advanced_snapshot(demand_df, 60)
    snap.events = [Event(1, "route_disruption", 60, 200, "SCHEDULED", {"route_ids": ["route-gazipur-tongi"]})]
    assert not schedule.route_open(snap, "route-gazipur-tongi", 5).any()
    d = recorded_demand(demand_df.assign(tick=demand_df.tick - 60).query("tick >= 0"), 96)
    res = policy_lp.solve(snap, d, 96)
    assert all(m.route_id != "route-gazipur-tongi" for m in res.moves + res.preview)


def test_event_end_keeps_live_status_at_offset_zero():
    snap = initial_snapshot()
    snap.routes["route-gazipur-tongi"].status = "DISRUPTED"
    snap.events = [Event(1, "route_disruption", 0, snap.tick, "ACTIVE", {"route_ids": ["route-gazipur-tongi"]})]
    ok = schedule.route_open(snap, "route-gazipur-tongi", 4)
    assert not ok[0] and ok[1:].all()


def test_multiplier_schedule_future_spike():
    snap = initial_snapshot()
    snap.events = [Event(1, "demand_spike", 10, 20, "SCHEDULED", {"region_ids": ["region-dhaka"], "multiplier": 1.8})]
    m = schedule.multiplier_schedule(snap, "station-mirpur", 30)
    assert m[9] == 1.0 and m[10] == pytest.approx(1.8) and m[20] == pytest.approx(1.8) and m[21] == 1.0  # end inclusive
    assert schedule.multiplier_schedule(snap, "station-coxsbazar", 30).max() == 1.0


@pytest.mark.parametrize("policy", ["heuristic", "lp"])
def test_assess_contract_fields(policy):
    out = Assessor().assess(initial_snapshot(), [], policy=policy)
    for k in ("tick", "model_version", "policy", "network", "signals", "risks", "forecasts", "recommendations", "projected"):
        assert k in out
    assert len(out["risks"]) == 12 and len(out["forecasts"]) == 12
    for r in out["recommendations"]:
        for k in ("station_id", "fuel_type", "action", "alternatives", "constraints", "impact", "confidence",
                  "review", "review_reasons", "confidence_notes", "explanation"):
            assert k in r
        assert r["alternatives"] and "Simulated" in r["explanation"]
        assert r["impact"]["unmet_after_l"] <= r["impact"]["unmet_before_l"]


def test_invalid_snapshot_rejected():
    snap = initial_snapshot()
    snap.stations["station-mirpur"].inventory["DIESEL"] = -5
    with pytest.raises(InvalidInput):
        Assessor().assess(snap, [])


def test_lp_failure_falls_back_to_heuristic(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("solver down")
    monkeypatch.setattr(policy_lp, "solve", boom)
    out = Assessor().assess(initial_snapshot(), [], policy="lp")
    assert out["policy"] == "fallback" and out["degraded"]
    assert all(r["review"] == "HUMAN_REVIEW" for r in out["recommendations"])


def test_closed_loop_replay_beats_no_action(demand_df):
    from intelligence.replay import run
    none = run("none", 150, demand_df)
    heur = run("heuristic", 150, demand_df)
    assert heur["service_level"] > 0.99 > none["service_level"]
    assert heur["rejected"] == {}


def test_review_reasons_only_when_review_required():
    out = Assessor().assess(initial_snapshot(), [], policy="heuristic")
    for r in out["recommendations"]:
        if r["review"] == "AUTO_ELIGIBLE":
            assert r["review_reasons"] == []
        else:
            assert r["review_reasons"]
    def boom(*a, **k):
        raise RuntimeError("down")
    import intelligence.policy_lp as pl
    orig, pl.solve = pl.solve, boom
    try:
        s = initial_snapshot(); s.stations["station-tongi"].inventory["DIESEL"] = 100.0
        fb = Assessor().assess(s, [], policy="lp")
        assert fb["recommendations"] and all(r["review_reasons"] for r in fb["recommendations"])
    finally:
        pl.solve = orig
