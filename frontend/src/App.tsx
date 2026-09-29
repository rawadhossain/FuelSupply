import { useEffect, useState, useCallback, useMemo } from "react";
import {
  getHealth, getHealthSummary, getState, getRecommendations, executeRecommendation,
  simRun, simPause, simStep, simReset, injectEvent, injectFault, clearFaults,
  type NetworkState, type Recommendation, type Allocation, type EventType, type FaultType, type HealthSummary,
} from "./api";

type Alert = { level: "bad" | "warn" | "info"; text: string };

function computeAlerts(state: NetworkState | null): Alert[] {
  if (!state) return [];
  const alerts: Alert[] = [];

  if (state.any_stale) alerts.push({ level: "warn", text: "Simulator data flagged stale — figures may be out of date." });
  if (!state.sse_connected) alerts.push({ level: "warn", text: "Live updates disconnected — falling back to polling." });
  if (state.last_poll_error) alerts.push({ level: "bad", text: `Invalid simulator response rejected: ${state.last_poll_error}` });

  for (const e of state.events) {
    if (e.status === "ACTIVE" || e.status === "SCHEDULED") {
      alerts.push({ level: e.status === "ACTIVE" ? "bad" : "warn", text: `${e.type.replace(/_/g, " ")} ${e.status.toLowerCase()} (ticks ${e.start_tick}–${e.end_tick})` });
    }
  }

  for (const s of state.stations) {
    for (const fuel of ["DIESEL", "PETROL", "OCTANE"] as const) {
      const ratio = s.capacity[fuel] > 0 ? s.inventory[fuel] / s.capacity[fuel] : 1;
      if (ratio < 0.15) {
        alerts.push({ level: "bad", text: `${s.name || s.id}: ${fuel.toLowerCase()} at ${(ratio * 100).toFixed(0)}% capacity — shortage risk.` });
      }
    }
  }

  for (const a of state.allocations) {
    if (a.status === "FAILED") {
      alerts.push({ level: "bad", text: `Allocation #${a.id} failed${a.failure_reason ? `: ${a.failure_reason}` : "."}` });
    }
  }

  return alerts;
}

function fmt(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  return n.toLocaleString(undefined, { maximumFractionDigits: 0 });
}

const OK = new Set(["OPEN", "AVAILABLE", "ARRIVED", "PENDING", "healthy", "PAUSED", "RUNNING", "SCHEDULED"]);
const BAD = new Set(["OUTAGE", "DISRUPTED", "CONSTRAINED", "FAILED", "CLOSED", "unreachable"]);

function Pill({ status }: { status: string }) {
  const cls = BAD.has(status) ? "pill-bad" : OK.has(status) ? "pill-ok" : "pill-warn";
  return <span className={`pill ${cls}`}>{status}</span>;
}

const EVENT_TYPES: EventType[] = [
  "demand_spike", "route_disruption", "station_outage", "depot_constraint", "shipment_delay", "supply_shortfall",
];
const FAULT_TYPES: FaultType[] = ["latency", "unavailable", "error_rate", "stale_data", "stream_disconnect"];

export default function App() {
  const [health, setHealth] = useState<string>("checking...");
  const [state, setState] = useState<NetworkState | null>(null);
  const [assessment, setAssessment] = useState<
    { tick: number; recommendations: Recommendation[]; degraded?: boolean; degraded_reason?: string } | null
  >(null);
  const [loadingRecs, setLoadingRecs] = useState(false);
  const [recError, setRecError] = useState<string | null>(null);
  const [executing, setExecuting] = useState<string | null>(null);
  const [history, setHistory] = useState<Allocation[]>([]);
  const [simBusy, setSimBusy] = useState(false);
  const [healthSummary, setHealthSummary] = useState<HealthSummary | null>(null);

  const alerts = useMemo(() => computeAlerts(state), [state]);

  const [eventType, setEventType] = useState<EventType>("demand_spike");
  const [eventStations, setEventStations] = useState<string[]>([]);
  const [eventMultiplier, setEventMultiplier] = useState(2);
  const [eventDuration, setEventDuration] = useState(40);

  const [faultType, setFaultType] = useState<FaultType>("latency");
  const [faultDuration, setFaultDuration] = useState(30);

  const refresh = useCallback(async () => {
    try {
      const [h, s] = await Promise.all([getHealth(), getState()]);
      setHealth(h.status);
      setState(s);
      setHistory(s.allocations);
    } catch {
      setHealth("unreachable");
    }
    try {
      setHealthSummary(await getHealthSummary());
    } catch {
      setHealthSummary(null);
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
      setAssessment({ tick: a.tick, recommendations: a.recommendations, degraded: a.degraded, degraded_reason: a.degraded_reason });
    } catch (e) {
      setRecError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoadingRecs(false);
    }
  }

  async function approve(rec: Recommendation) {
    if (rec.review === "HUMAN_REVIEW") {
      const ok = confirm(
        `This recommendation is flagged for human review (low confidence or fallback policy). ` +
          `Submit ${rec.action.quantity.toFixed(0)} L ${rec.fuel_type} to ${rec.station_id} anyway?`,
      );
      if (!ok) return;
    }
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

  async function runSimAction(fn: () => Promise<unknown>) {
    setSimBusy(true);
    try {
      await fn();
      await refresh();
    } catch (e) {
      alert(e instanceof Error ? e.message : String(e));
    } finally {
      setSimBusy(false);
    }
  }

  function toggleStation(id: string) {
    setEventStations((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));
  }

  async function submitEvent() {
    await runSimAction(() =>
      injectEvent({
        type: eventType,
        duration_ticks: eventDuration,
        station_ids: eventStations,
        multiplier: eventType === "demand_spike" ? eventMultiplier : undefined,
      }),
    );
  }

  async function submitFault() {
    await runSimAction(() => injectFault({ type: faultType, duration_seconds: faultDuration }));
  }

  const isRunning = state?.instance?.status === "RUNNING";

  return (
    <main className="page">
      <div className="banner">SIMULATED ENVIRONMENT — all data below comes from the organizer-provided fuel supply simulator, not a real network.</div>

      <div className="topbar">
        <h1>Fuel Supply Operations</h1>
        <div className="statline">
          <span>Core: <Pill status={health} /></span>
          <span>Tick: <strong>{state?.instance?.tick ?? "—"}</strong></span>
          <span>Sim: <Pill status={state?.instance?.status ?? "—"} /></span>
          <span>SSE: <Pill status={state?.sse_connected ? "OPEN" : "OUTAGE"} /></span>
          {state?.any_stale && <span className="pill pill-warn">stale data</span>}
        </div>
      </div>

      <div className="grid-cols">
        <div className="card">
          <h2>System health</h2>
          {!healthSummary && <p className="empty">Checking components...</p>}
          {healthSummary && (
            <table>
              <tbody>
                {healthSummary.components.map((c) => (
                  <tr key={c.name}>
                    <td style={{ textTransform: "capitalize" }}>{c.name.replace(/_/g, " ")}</td>
                    <td style={{ textAlign: "right" }}>
                      <Pill status={c.status === "healthy" ? "healthy" : c.status === "down" ? "FAILED" : "DEGRADED"} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        <div className="card">
          <h2>Alerts</h2>
          {alerts.length === 0 && <p className="empty">No active alerts.</p>}
          {alerts.map((a, i) => (
            <p key={i} className="rec-detail" style={{ fontSize: 13, color: "var(--text)", display: "flex", gap: 8, alignItems: "center" }}>
              <span className={`pill ${a.level === "bad" ? "pill-bad" : a.level === "warn" ? "pill-warn" : "pill-ok"}`}>
                {a.level}
              </span>
              {a.text}
            </p>
          ))}
        </div>
      </div>

      <div className="card">
        <h2>Simulation controls</h2>
        <div className="btn-row" style={{ marginBottom: 16 }}>
          <button className="btn btn-primary" disabled={simBusy || isRunning} onClick={() => runSimAction(simRun)}>Run</button>
          <button className="btn btn-ghost" disabled={simBusy || !isRunning} onClick={() => runSimAction(simPause)}>Pause</button>
          <button className="btn btn-ghost" disabled={simBusy} onClick={() => runSimAction(simStep)}>Step 1 tick</button>
          <button
            className="btn btn-danger"
            disabled={simBusy}
            onClick={() => confirm("Reset wipes all progress and reloads the baseline scenario. Continue?") && runSimAction(simReset)}
          >
            Reset
          </button>
        </div>

        <div className="grid-cols">
          <div>
            <p className="rec-meta" style={{ marginBottom: 8 }}>INJECT CRISIS EVENT</p>
            <div className="form-row">
              <label className="field">
                Type
                <select value={eventType} onChange={(e) => setEventType(e.target.value as EventType)}>
                  {EVENT_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
                </select>
              </label>
              <label className="field">
                Duration (ticks)
                <input type="number" value={eventDuration} min={1} onChange={(e) => setEventDuration(Number(e.target.value))} style={{ width: 80 }} />
              </label>
              {eventType === "demand_spike" && (
                <label className="field">
                  Multiplier
                  <input type="number" step={0.1} value={eventMultiplier} onChange={(e) => setEventMultiplier(Number(e.target.value))} style={{ width: 70 }} />
                </label>
              )}
            </div>
            {(eventType === "demand_spike" || eventType === "station_outage") && state && (
              <div className="checkbox-group" style={{ marginBottom: 10 }}>
                {state.stations.map((s) => (
                  <label key={s.id}>
                    <input type="checkbox" checked={eventStations.includes(s.id)} onChange={() => toggleStation(s.id)} />
                    {s.name || s.id}
                  </label>
                ))}
              </div>
            )}
            <button className="btn btn-primary" disabled={simBusy} onClick={submitEvent}>Inject event</button>
          </div>

          <div>
            <p className="rec-meta" style={{ marginBottom: 8 }}>INJECT FAULT</p>
            <div className="form-row">
              <label className="field">
                Type
                <select value={faultType} onChange={(e) => setFaultType(e.target.value as FaultType)}>
                  {FAULT_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
                </select>
              </label>
              <label className="field">
                Duration (s)
                <input type="number" value={faultDuration} min={1} max={3600} onChange={(e) => setFaultDuration(Number(e.target.value))} style={{ width: 80 }} />
              </label>
            </div>
            <div className="btn-row">
              <button className="btn btn-primary" disabled={simBusy} onClick={submitFault}>Inject fault</button>
              <button className="btn btn-ghost" disabled={simBusy} onClick={() => runSimAction(clearFaults)}>Clear faults</button>
            </div>
          </div>
        </div>
      </div>

      <div className="grid-cols">
        <div className="card">
          <h2>Depots</h2>
          <table>
            <thead><tr><th>ID</th><th>Status</th><th>Diesel</th><th>Petrol</th><th>Octane</th></tr></thead>
            <tbody>
              {state?.depots.map((d) => (
                <tr key={d.id}>
                  <td>{d.name || d.id}</td>
                  <td><Pill status={d.status} /></td>
                  <td>{fmt(d.inventory.DIESEL)}/{fmt(d.capacity.DIESEL)}</td>
                  <td>{fmt(d.inventory.PETROL)}/{fmt(d.capacity.PETROL)}</td>
                  <td>{fmt(d.inventory.OCTANE)}/{fmt(d.capacity.OCTANE)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className="card">
          <h2>Stations</h2>
          <table>
            <thead><tr><th>ID</th><th>Status</th><th>Diesel</th><th>Petrol</th><th>Octane</th><th>×</th></tr></thead>
            <tbody>
              {state?.stations.map((s) => (
                <tr key={s.id}>
                  <td>{s.name || s.id}</td>
                  <td><Pill status={s.status} /></td>
                  <td>{fmt(s.inventory.DIESEL)}/{fmt(s.capacity.DIESEL)}</td>
                  <td>{fmt(s.inventory.PETROL)}/{fmt(s.capacity.PETROL)}</td>
                  <td>{fmt(s.inventory.OCTANE)}/{fmt(s.capacity.OCTANE)}</td>
                  <td>{s.demand_multiplier !== 1 ? <b>{s.demand_multiplier}×</b> : "1×"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="card">
        <h2>Routes</h2>
        <table>
          <thead><tr><th>ID</th><th>From → To</th><th>Status</th><th>Transit</th><th>Max shipment</th></tr></thead>
          <tbody>
            {state?.routes.map((r) => (
              <tr key={r.id}>
                <td>{r.id}</td>
                <td>{r.source_depot_id} → {r.destination_station_id}</td>
                <td><Pill status={r.status} /></td>
                <td>{r.transit_ticks} ticks</td>
                <td>{fmt(r.max_shipment)} L</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {state && state.events.length > 0 && (
        <div className="card">
          <h2>Active / scheduled disruptions</h2>
          {state.events.map((e) => (
            <p key={e.id} className="rec-detail" style={{ fontSize: 13, color: "var(--text)" }}>
              <b>{e.type}</b> <Pill status={e.status} /> · ticks {e.start_tick}–{e.end_tick} · {JSON.stringify(e.parameters)}
            </p>
          ))}
        </div>
      )}

      <div className="card">
        <h2>Recommendations</h2>
        <button className="btn btn-primary" onClick={fetchRecommendations} disabled={loadingRecs} style={{ marginBottom: 14 }}>
          {loadingRecs ? "Asking Intelligence..." : "Get recommendations"}
        </button>
        {recError && <p className="error-text">Error: {recError}</p>}
        {assessment?.degraded && (
          <p className="pill pill-warn" style={{ display: "block", marginBottom: 10, padding: "6px 10px" }}>
            FALLBACK POLICY ACTIVE — {assessment.degraded_reason || "Intelligence service unavailable"}. Recommendations below are from Core's own naive heuristic, not the ML/LP model — review carefully.
          </p>
        )}
        {assessment && (
          <p className="rec-meta" style={{ marginBottom: 10 }}>
            Assessed at tick {assessment.tick} — {assessment.recommendations.length} recommendation(s)
          </p>
        )}
        {assessment?.recommendations.length === 0 && <p className="empty">No urgent recommendations right now.</p>}
        {assessment?.recommendations.map((rec) => (
          <div key={rec.id} className="rec-card">
            <div className="rec-head">
              <span>{rec.station_id} — {rec.fuel_type}</span>
              <span className="rec-meta">{(rec.confidence * 100).toFixed(0)}% confidence · <Pill status={rec.review} /></span>
            </div>
            <p className="rec-explanation">{rec.explanation}</p>
            <p className="rec-detail">
              Risk: {(rec.impact.risk_before * 100).toFixed(0)}% → {(rec.impact.risk_after * 100).toFixed(0)}%
              {" · "}Unmet: {fmt(rec.impact.unmet_before_l)} L → {fmt(rec.impact.unmet_after_l)} L
            </p>
            {rec.constraints.length > 0 && <p className="rec-detail">Constraints: {rec.constraints.join("; ")}</p>}
            {rec.alternatives.length > 0 && (
              <p className="rec-detail">Alternatives: {rec.alternatives.map((a) => `${a.why_not} (${fmt(a.quantity)} L)`).join("; ")}</p>
            )}
            <button className="btn btn-primary" disabled={executing === rec.id} onClick={() => approve(rec)} style={{ marginTop: 10 }}>
              {executing === rec.id ? "Submitting..." : "Approve & submit"}
            </button>
          </div>
        ))}
      </div>

      <div className="card">
        <h2>Decision history</h2>
        {history.length === 0 && <p className="empty">No allocations yet.</p>}
        {history.length > 0 && (
          <table>
            <thead><tr><th>ID</th><th>Route</th><th>Fuel</th><th>Qty (L)</th><th>Status</th><th>Tick</th></tr></thead>
            <tbody>
              {history.map((a) => (
                <tr key={a.id}>
                  <td>{a.id}</td>
                  <td>{a.source_depot_id} → {a.destination_station_id}</td>
                  <td>{a.fuel_type}</td>
                  <td>{fmt(a.quantity)}</td>
                  <td><Pill status={a.status} /></td>
                  <td>{a.created_tick}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </main>
  );
}
