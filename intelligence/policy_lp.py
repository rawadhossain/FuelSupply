"""Rolling-horizon LP allocation policy (model predictive control), solved with SciPy HiGHS.

max  Σ srv  − λ·Σ overflow + μ·(terminal stock) − γ·z·(mean series demand) − ε·Σ t·x_t
s.t. station & depot balances, capacities, route max, dispatch/tick, creation-time headroom,
     route/station/depot availability windows from events; z ≥ unmet share of every series.
Only the offset-0 moves are executed; re-solve next tick. See docs/ml-architecture.md §4.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import numpy as np
from scipy.optimize import linprog
from scipy.sparse import coo_matrix

from shared import schedule
from shared.heuristic import MIN_QTY, dispatch_used
from shared.projection import Move, inbound_schedule, supply_schedule
from shared.snapshot import Snapshot


@dataclass
class LPResult:
    moves: list[Move]
    preview: list[Move]
    status: str
    solve_seconds: float
    objective: float | None
    station_value: dict            # (sid,f) -> shadow value of 1 L at the station (t=0 balance)


def solve(snap: Snapshot, demand: dict, H: int = 96, overflow_penalty: float = 0.2,
          terminal_value: float = 0.02, fairness: float = 0.5, lateness: float = 1e-4,
          min_batch: float = 1000.0, time_limit: float = 5.0) -> LPResult:
    fuels = snap.fuels
    S = [(s, f) for s in snap.stations for f in fuels if f in snap.stations[s].capacity]
    D = [(d, f) for d in snap.depots for f in fuels if f in snap.depots[d].capacity]
    R = [(r, f) for r in snap.routes for f in fuels]
    nx, ns, nd = len(R), len(S), len(D)
    per_t = nx + 2 * ns + 2 * nd
    ix = lambda t, i: t * per_t + i
    isrv = lambda t, i: t * per_t + nx + i
    iI = lambda t, i: t * per_t + nx + ns + i
    iDp = lambda t, i: t * per_t + nx + 2 * ns + i
    iov = lambda t, i: t * per_t + nx + 2 * ns + nd + i
    z = H * per_t
    n = z + 1
    Sidx = {k: i for i, k in enumerate(S)}; Didx = {k: i for i, k in enumerate(D)}

    c = np.zeros(n); lb = np.zeros(n); ub = np.full(n, np.inf)
    inb = inbound_schedule(snap, H); sup = supply_schedule(snap, H)
    open_st = {s: schedule.station_open(snap, s, H) for s in snap.stations}
    open_rt = {r: schedule.route_open(snap, r, H) for r in snap.routes}
    open_dp = {d: schedule.depot_open(snap, d, H) for d in snap.depots}
    tot_d = sum(float(demand[k].sum()) for k in S)
    for t in range(H):
        for i, (r, f) in enumerate(R):
            rt = snap.routes[r]
            ok = open_rt[r][t] and open_dp[rt.source_depot_id][t] and \
                open_st[rt.destination_station_id][t] and t + 1 + rt.transit_ticks < H + 8
            ub[ix(t, i)] = rt.max_shipment if ok else 0.0
            c[ix(t, i)] = lateness * t   # tie-break: ship as early as useful, not at the last possible tick
        for i, (s, f) in enumerate(S):
            c[isrv(t, i)] = -1.0
            ub[isrv(t, i)] = demand[(s, f)][t] if open_st[s][t] else 0.0
            # + existing inbound so already-committed arrivals can never make the model infeasible
            ub[iI(t, i)] = snap.stations[s].capacity[f] + float(inb.get((s, f), np.zeros(H)).sum())
        for i, (d, f) in enumerate(D):
            ub[iDp(t, i)] = snap.depots[d].capacity[f]
            c[iov(t, i)] = overflow_penalty
    for i in range(ns):
        c[iI(H - 1, i)] -= terminal_value
    for i in range(nd):
        c[iDp(H - 1, i)] -= terminal_value
    c[z] = fairness * tot_d / max(ns, 1); ub[z] = 1.0

    er, ec, ev, beq = [], [], [], []
    ur, uc, uv, bub = [], [], [], []
    row = 0
    def E(coefs, rhs):
        nonlocal row
        for j, v in coefs:
            er.append(row); ec.append(j); ev.append(v)
        beq.append(rhs); row += 1
    urow = 0
    def U(coefs, rhs):
        nonlocal urow
        for j, v in coefs:
            ur.append(urow); uc.append(j); uv.append(v)
        bub.append(rhs); urow += 1

    into = {k: [] for k in S}; outof = {k: [] for k in D}; bydepot = {d: [] for d in snap.depots}
    for i, (r, f) in enumerate(R):
        rt = snap.routes[r]
        if (rt.destination_station_id, f) in Sidx: into[(rt.destination_station_id, f)].append((i, rt.transit_ticks))
        if (rt.source_depot_id, f) in Didx: outof[(rt.source_depot_id, f)].append(i)
        bydepot[rt.source_depot_id].append(i)
    for t in range(H):
        for i, (s, f) in enumerate(S):
            co = [(iI(t, i), 1.0), (isrv(t, i), 1.0)]
            if t > 0: co.append((iI(t - 1, i), -1.0))
            for j, tr in into[(s, f)]:
                t0 = t - 1 - tr
                if t0 >= 0: co.append((ix(t0, j), -1.0))
            rhs = float(inb.get((s, f), np.zeros(H))[t]) + (snap.stations[s].inventory.get(f, 0.0) if t == 0 else 0.0)
            E(co, rhs)
            # creation-time headroom: station inventory + new shipments <= capacity
            if into[(s, f)]:
                hc = [(ix(t, j), 1.0) for j, _ in into[(s, f)]]
                if t > 0:
                    U(hc + [(iI(t - 1, i), 1.0)], snap.stations[s].capacity[f])
                else:
                    U(hc, snap.stations[s].capacity[f] - snap.stations[s].inventory.get(f, 0.0))
            # fairness: unmet share <= z   ->  (Σd - Σsrv)/Σd <= z   (added once, after loop)
        for i, (d, f) in enumerate(D):
            co = [(iDp(t, i), 1.0), (iov(t, i), 1.0)] + [(ix(t, j), 1.0) for j in outof[(d, f)]]
            if t > 0: co.append((iDp(t - 1, i), -1.0))
            rhs = float(sup.get((d, f), np.zeros(H))[t]) + (snap.depots[d].inventory.get(f, 0.0) if t == 0 else 0.0)
            E(co, rhs)
        for d, dp in snap.depots.items():
            cap = dp.dispatch_capacity_per_tick - (dispatch_used(snap, d) if t == 0 else 0.0)
            U([(ix(t, j), 1.0) for j in bydepot[d]], max(cap, 0.0))
    for i, k in enumerate(S):
        dsum = float(demand[k].sum())
        if dsum > 0:
            U([(isrv(t, i), -1.0 / dsum) for t in range(H)] + [(z, -1.0)], -1.0)

    A_eq = coo_matrix((ev, (er, ec)), shape=(row, n)).tocsr()
    A_ub = coo_matrix((uv, (ur, uc)), shape=(urow, n)).tocsr()
    t0 = time.perf_counter()
    res = linprog(c, A_ub=A_ub, b_ub=bub, A_eq=A_eq, b_eq=beq, bounds=np.column_stack([lb, ub]),
                  method="highs", options={"time_limit": time_limit})
    dt = time.perf_counter() - t0
    if res.status != 0:
        return LPResult([], [], f"failed: {res.message}", dt, None, {})
    xs = res.x
    def moves_at(t, floor=MIN_QTY):
        out = []
        for i, (r, f) in enumerate(R):
            q = float(np.floor(xs[ix(t, i)] / 10) * 10)
            if q >= floor:
                out.append(Move(r, f, q, t))
        return out
    value = {}
    try:
        marg = res.eqlin.marginals
        for i, k in enumerate(S):
            value[k] = round(-float(marg[i]), 4)      # row i at t=0 is station k's balance
    except Exception:
        pass
    # Execute only batches worth a truck: small top-ups are deferred to a later re-solve, unless
    # the station would otherwise run dry before the next tick's plan could reach it.
    now = []
    for m in moves_at(0):
        rt = snap.routes[m.route_id]; k = (rt.destination_station_id, m.fuel_type)
        inv = snap.stations[k[0]].inventory.get(k[1], 0.0)
        urgent = inv < float(demand[k][: rt.transit_ticks + 3].sum())
        if m.quantity >= min_batch or urgent:
            now.append(m)
    preview = [m for t in range(1, min(H, 8)) for m in moves_at(t)]
    return LPResult(now, preview, "optimal", dt, float(res.fun), value)
