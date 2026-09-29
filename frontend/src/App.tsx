import { useEffect, useState, useCallback } from "react";
import {
  getHealth, getState, getRecommendations, executeRecommendation,
  type NetworkState, type Recommendation, type Allocation,
} from "./api";

function fmt(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  return n.toLocaleString(undefined, { maximumFractionDigits: 0 });
}

function StatusBadge({ status }: { status: string }) {
  const ok = ["OPEN", "AVAILABLE", "ARRIVED", "PENDING", "healthy", "PAUSED", "RUNNING"].includes(status);
  const bad = ["OUTAGE", "DISRUPTED", "CONSTRAINED", "FAILED", "CLOSED"].includes(status);
  const color = bad ? "#c0392b" : ok ? "#1e8449" : "#8a6d0a";
  return <span style={{ color, fontWeight: 600 }}>{status}</span>;
}

export default function App() {
  const [health, setHealth] = useState<string>("checking...");
  const [state, setState] = useState<NetworkState | null>(null);
  const [assessment, setAssessment] = useState<{ tick: number; recommendations: Recommendation[] } | null>(null);
  const [loadingRecs, setLoadingRecs] = useState(false);
  const [recError, setRecError] = useState<string | null>(null);
  const [executing, setExecuting] = useState<string | null>(null);
  const [history, setHistory] = useState<Allocation[]>([]);

  const refresh = useCallback(async () => {
    try {
      const [h, s] = await Promise.all([getHealth(), getState()]);
      setHealth(h.status);
      setState(s);
      setHistory(s.allocations);
    } catch {
      setHealth("unreachable");
    }
  }, []);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 5000);
    return () => clearInterval(id);
  }, [refresh]);

  async function fetchRecommendations() {
    setLoadingRecs(true);
    setRecError(null);
    try {
      const a = await getRecommendations("heuristic", false);
      setAssessment({ tick: a.tick, recommendations: a.recommendations });
    } catch (e) {
      setRecError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoadingRecs(false);
    }
  }

  async function approve(rec: Recommendation) {
    setExecuting(rec.id);
    try {
      await executeRecommendation(rec);
      setAssessment((prev) =>
        prev ? { ...prev, recommendations: prev.recommendations.filter((r) => r.id !== rec.id) } : prev,
      );
      await refresh();
    } catch (e) {
      alert(`Execution failed: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setExecuting(null);
    }
  }

  return (
    <main style={{ fontFamily: "system-ui, sans-serif", maxWidth: 1200, margin: "0 auto", padding: 16 }}>
      <div style={{ background: "#7d3c98", color: "white", padding: "6px 12px", borderRadius: 4, marginBottom: 12, fontSize: 13 }}>
        SIMULATED ENVIRONMENT — all data below comes from the organizer-provided fuel supply simulator, not a real network.
      </div>

      <h1 style={{ marginBottom: 4 }}>Fuel Supply Operations</h1>
      <p style={{ color: "#555", marginTop: 0 }}>
        Core: <StatusBadge status={health} /> · Tick: {state?.instance?.tick ?? "—"} ·
        {" "}Sim status: <StatusBadge status={state?.instance?.status ?? "—"} /> ·
        {" "}SSE: <StatusBadge status={state?.sse_connected ? "OPEN" : "OUTAGE"} />
        {state?.any_stale && <span style={{ color: "#c0392b", marginLeft: 8 }}>⚠ stale data</span>}
      </p>

      <section style={{ marginTop: 24 }}>
        <h2>Depots</h2>
        <table style={{ borderCollapse: "collapse", width: "100%" }}>
          <thead>
            <tr style={{ textAlign: "left", borderBottom: "2px solid #ddd" }}>
              <th>ID</th><th>Status</th><th>Diesel</th><th>Petrol</th><th>Octane</th><th>Dispatch/tick</th>
            </tr>
          </thead>
          <tbody>
            {state?.depots.map((d) => (
              <tr key={d.id} style={{ borderBottom: "1px solid #eee" }}>
                <td>{d.name || d.id}</td>
                <td><StatusBadge status={d.status} /></td>
                <td>{fmt(d.inventory.DIESEL)} / {fmt(d.capacity.DIESEL)}</td>
                <td>{fmt(d.inventory.PETROL)} / {fmt(d.capacity.PETROL)}</td>
                <td>{fmt(d.inventory.OCTANE)} / {fmt(d.capacity.OCTANE)}</td>
                <td>{fmt(d.dispatch_capacity_per_tick)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section style={{ marginTop: 24 }}>
        <h2>Stations</h2>
        <table style={{ borderCollapse: "collapse", width: "100%" }}>
          <thead>
            <tr style={{ textAlign: "left", borderBottom: "2px solid #ddd" }}>
              <th>ID</th><th>Status</th><th>Profile</th><th>Diesel</th><th>Petrol</th><th>Octane</th><th>Demand ×</th>
            </tr>
          </thead>
          <tbody>
            {state?.stations.map((s) => (
              <tr key={s.id} style={{ borderBottom: "1px solid #eee" }}>
                <td>{s.name || s.id}</td>
                <td><StatusBadge status={s.status} /></td>
                <td>{s.demand_profile}</td>
                <td>{fmt(s.inventory.DIESEL)} / {fmt(s.capacity.DIESEL)}</td>
                <td>{fmt(s.inventory.PETROL)} / {fmt(s.capacity.PETROL)}</td>
                <td>{fmt(s.inventory.OCTANE)} / {fmt(s.capacity.OCTANE)}</td>
                <td>{s.demand_multiplier !== 1 ? <b>{s.demand_multiplier}×</b> : "1×"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section style={{ marginTop: 24 }}>
        <h2>Routes</h2>
        <table style={{ borderCollapse: "collapse", width: "100%" }}>
          <thead>
            <tr style={{ textAlign: "left", borderBottom: "2px solid #ddd" }}>
              <th>ID</th><th>From → To</th><th>Status</th><th>Transit (ticks)</th><th>Max shipment</th>
            </tr>
          </thead>
          <tbody>
            {state?.routes.map((r) => (
              <tr key={r.id} style={{ borderBottom: "1px solid #eee" }}>
                <td>{r.id}</td>
                <td>{r.source_depot_id} → {r.destination_station_id}</td>
                <td><StatusBadge status={r.status} /></td>
                <td>{r.transit_ticks}</td>
                <td>{fmt(r.max_shipment)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      {state && state.events.length > 0 && (
        <section style={{ marginTop: 24 }}>
          <h2>Active / scheduled disruptions</h2>
          <ul>
            {state.events.map((e) => (
              <li key={e.id}>
                <b>{e.type}</b> — <StatusBadge status={e.status} /> (ticks {e.start_tick}–{e.end_tick})
                {" "}{JSON.stringify(e.parameters)}
              </li>
            ))}
          </ul>
        </section>
      )}

      <section style={{ marginTop: 24 }}>
        <h2>Recommendations</h2>
        <button onClick={fetchRecommendations} disabled={loadingRecs} style={{ padding: "8px 16px", cursor: "pointer" }}>
          {loadingRecs ? "Asking Intelligence..." : "Get recommendations"}
        </button>
        {recError && <p style={{ color: "#c0392b" }}>Error: {recError}</p>}
        {assessment && (
          <p style={{ color: "#555" }}>
            Assessed at tick {assessment.tick} — {assessment.recommendations.length} recommendation(s)
          </p>
        )}
        {assessment?.recommendations.map((rec) => (
          <div key={rec.id} style={{ border: "1px solid #ddd", borderRadius: 6, padding: 12, marginBottom: 10 }}>
            <div style={{ display: "flex", justifyContent: "space-between" }}>
              <b>{rec.station_id} — {rec.fuel_type}</b>
              <span>confidence {(rec.confidence * 100).toFixed(0)}% · <StatusBadge status={rec.review} /></span>
            </div>
            <p style={{ margin: "6px 0" }}>{rec.explanation}</p>
            <p style={{ fontSize: 13, color: "#555" }}>
              Risk: {(rec.impact.risk_before * 100).toFixed(0)}% → {(rec.impact.risk_after * 100).toFixed(0)}%
              {" · "}Unmet: {fmt(rec.impact.unmet_before_l)} L → {fmt(rec.impact.unmet_after_l)} L
            </p>
            {rec.constraints.length > 0 && (
              <p style={{ fontSize: 12, color: "#777" }}>Constraints: {rec.constraints.join("; ")}</p>
            )}
            {rec.alternatives.length > 0 && (
              <p style={{ fontSize: 12, color: "#777" }}>
                Alternatives: {rec.alternatives.map((a) => `${a.why_not} (${fmt(a.quantity)} L)`).join("; ")}
              </p>
            )}
            <button
              onClick={() => approve(rec)}
              disabled={executing === rec.id}
              style={{ marginTop: 8, padding: "6px 14px", cursor: "pointer", background: "#1e8449", color: "white", border: "none", borderRadius: 4 }}
            >
              {executing === rec.id ? "Submitting..." : "Approve & submit"}
            </button>
          </div>
        ))}
      </section>

      <section style={{ marginTop: 24, marginBottom: 40 }}>
        <h2>Decision history</h2>
        <table style={{ borderCollapse: "collapse", width: "100%" }}>
          <thead>
            <tr style={{ textAlign: "left", borderBottom: "2px solid #ddd" }}>
              <th>ID</th><th>Route</th><th>Fuel</th><th>Qty (L)</th><th>Status</th><th>Created tick</th>
            </tr>
          </thead>
          <tbody>
            {history.map((a) => (
              <tr key={a.id} style={{ borderBottom: "1px solid #eee" }}>
                <td>{a.id}</td>
                <td>{a.source_depot_id} → {a.destination_station_id}</td>
                <td>{a.fuel_type}</td>
                <td>{fmt(a.quantity)}</td>
                <td><StatusBadge status={a.status} /></td>
                <td>{a.created_tick}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </main>
  );
}
