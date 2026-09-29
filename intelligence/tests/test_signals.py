"""Tests for inventory reconciliation, transport-failure prediction, supply ETA, priorities,
bottlenecks and reset handling. Run from repo root: python -m pytest intelligence/tests -q"""
import copy
import os

import numpy as np
import pandas as pd
import pytest

from intelligence import heuristic, policy_lp
from intelligence.assess import Assessor
from intelligence.projection import Move
from intelligence.replay import ROOT, ReplaySim, initial_snapshot
from intelligence.signals import SupplyHistory, allocations_at_risk, reconcile_inventory
from intelligence.snapshot import Allocation, Event

DEM = os.path.join(ROOT, "dataset", "ml", "demand_full_features.csv")
pytestmark = pytest.mark.skipif(not os.path.exists(DEM), reason="needs dataset/ml CSVs")


@pytest.fixture(scope="module")
def dem():
    return pd.read_csv(DEM)


def two_ticks(dem, start=20, ship=True):
    """Consecutive snapshots from the replay (with one allocation created in between)."""
    sim = ReplaySim(dem)
    for _ in range(start):
        sim.step()
    prev = copy.deepcopy(sim.snap)
    if ship:
        sim.submit(Move("route-gazipur-tongi", "DIESEL", 3000, 0))
    rows = sim.step()
    served = {(r["station_id"], r["fuel_type"]): r["served_liters"] for r in rows}
    return prev, copy.deepcopy(sim.snap), served


def test_reconciliation_quiet_on_normal_tick(dem):
    prev, now, served = two_ticks(dem)
    assert reconcile_inventory(prev, now, served) == []


def test_reconciliation_flags_station_leak(dem):
    prev, now, served = two_ticks(dem)
    now.stations["station-mirpur"].inventory["PETROL"] -= 800      # fuel vanished
    sig = reconcile_inventory(prev, now, served)
    assert len(sig) == 1 and sig[0]["entity_id"] == "station-mirpur|PETROL"
    assert sig[0]["evidence"]["unexplained_l"] == pytest.approx(-800, abs=1) and sig[0]["severity"] == "high"


def test_reconciliation_flags_depot_gain(dem):
    prev, now, served = two_ticks(dem)
    now.depots["depot-patiya"].inventory["OCTANE"] += 300
    sig = reconcile_inventory(prev, now, served)
    assert [s["entity_id"] for s in sig] == ["depot-patiya|OCTANE"]


def test_allocation_at_risk_when_route_closes_at_departure():
    snap = initial_snapshot()
    snap.allocations = [Allocation(7, "route-gazipur-tongi", "depot-gazipur", "station-tongi", "DIESEL",
                                   2000, snap.tick, snap.tick + 3, "PENDING")]
    assert allocations_at_risk(snap) == []
    snap.events = [Event(1, "route_disruption", snap.tick + 1, snap.tick + 20, "SCHEDULED",
                         {"route_ids": ["route-gazipur-tongi"]})]
    sig = allocations_at_risk(snap)
    assert len(sig) == 1 and sig[0]["entity_id"] == "7" and "cancel" in sig[0]["advice"]


def test_supply_outlook_learns_depot_delay():
    snap = initial_snapshot()
    h = SupplyHistory()
    h.update(snap)
    first = next(a for a in snap.arrivals if a.depot_id == "depot-gazipur")
    first.planned_tick += 4; first.status = "ARRIVED"             # it arrived 4 ticks late
    h.update(snap)
    nxt = next(r for r in h.outlook(snap) if r["depot_id"] == "depot-gazipur")
    assert nxt["eta_tick"] == pytest.approx(nxt["planned_tick"] + 4)
    assert nxt["on_time_rate"] == 0.0 and "mean observed delay" in nxt["basis"]


def scarce_snapshot():
    """Two stations nearly dry on diesel, depots hold enough diesel for only one of them."""
    snap = initial_snapshot()
    snap.tick = 300
    snap.arrivals = []
    for d in snap.depots.values():
        d.inventory["DIESEL"] = 0.0
    snap.depots["depot-patiya"].inventory["DIESEL"] = 1500.0
    for s in ("station-mirpur", "station-coxsbazar"):
        snap.stations[s].inventory["DIESEL"] = 50.0
    demand = {(s, f): np.full(96, 30.0) for s in snap.stations for f in ("DIESEL", "PETROL", "OCTANE")}
    return snap, demand


@pytest.mark.parametrize("vip", ["station-mirpur", "station-coxsbazar"])
def test_heuristic_priority_decides_who_gets_scarce_fuel(vip):
    snap, demand = scarce_snapshot()
    moves = [m for m in heuristic.plan(snap, demand, 96, priorities={vip: 5.0}) if m.fuel_type == "DIESEL"]
    assert moves and snap.routes[moves[0].route_id].destination_station_id == vip


def test_lp_priority_shifts_fuel_to_priority_station():
    snap, demand = scarce_snapshot()
    def to(res, s):
        return sum(m.quantity for m in res.moves + res.preview
                   if m.fuel_type == "DIESEL" and snap.routes[m.route_id].destination_station_id == s)
    r = policy_lp.solve(snap, demand, 96, priorities={"station-coxsbazar": 5.0}, min_batch=0)
    assert r.status == "optimal" and to(r, "station-coxsbazar") > to(r, "station-mirpur")


def test_bottlenecks_explain_unserved_station():
    snap, _ = scarce_snapshot()
    out = Assessor().assess(snap, [], policy="heuristic")
    b = out["bottlenecks"]
    assert set(b) == {"binding_constraint_counts", "depot_dispatch_utilisation", "at_risk_not_addressed"}
    reasons = {(u["station_id"], u["fuel_type"]): u["reason"] for u in b["at_risk_not_addressed"]}
    assert any("no diesel left" in r for r in reasons.values())


def test_reset_is_handled_not_rejected():
    ass = Assessor()
    s = initial_snapshot(); s.tick = 50
    ass.assess(s, [])
    out = ass.assess(initial_snapshot(), [])           # tick 0 < 50: simulator was reset
    assert any(sg["type"] == "simulation_reset" for sg in out["signals"])


def test_no_false_demand_alarm_at_spike_end(dem):
    """Declared spike 100..148 (inclusive): the tick after it ends must not raise a demand alarm."""
    sim, ass, rows = ReplaySim(dem, crisis=True), Assessor(), []
    for _ in range(160):
        out = ass.assess(copy.deepcopy(sim.snap), rows, policy="heuristic")
        assert not [s for s in out["signals"] if s["type"] == "demand_anomaly"], sim.snap.tick
        for r in out["recommendations"]:
            a = r["action"]; sim.submit(Move(a["route_id"], r["fuel_type"], a["quantity"], 0))
        rows = sim.step()
