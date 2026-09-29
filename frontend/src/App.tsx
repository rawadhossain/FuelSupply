import { useEffect, useState, useCallback, useMemo, useRef } from "react";
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

/* ---- Minimal inline icon set (no external assets/fonts) ---- */

function Icon({ name, size = 17 }: { name: string; size?: number }) {
  const common = {
    width: size, height: size, viewBox: "0 0 24 24", fill: "none",
    stroke: "currentColor", strokeWidth: 1.8, strokeLinecap: "round" as const, strokeLinejoin: "round" as const,
  };
  switch (name) {
    case "dashboard":
      return <svg {...common}><rect x="3" y="3" width="7" height="7" rx="1.5" /><rect x="14" y="3" width="7" height="7" rx="1.5" /><rect x="3" y="14" width="7" height="7" rx="1.5" /><rect x="14" y="14" width="7" height="7" rx="1.5" /></svg>;
    case "network":
      return <svg {...common}><circle cx="6" cy="6" r="2.6" /><circle cx="18" cy="6" r="2.6" /><circle cx="12" cy="18" r="2.6" /><line x1="7.8" y1="7.8" x2="10.5" y2="15.8" /><line x1="16.2" y1="7.8" x2="13.5" y2="15.8" /></svg>;
    case "depot":
      return <svg {...common}><rect x="4" y="6" width="16" height="13" rx="2" /><line x1="4" y1="11.5" x2="20" y2="11.5" /></svg>;
    case "station":
      return <svg {...common}><rect x="8" y="8" width="8" height="8" rx="1.5" transform="rotate(45 12 12)" /></svg>;
    case "allocations":
      return <svg {...common}><circle cx="4.5" cy="6" r="1" fill="currentColor" stroke="none" /><line x1="8" y1="6" x2="20" y2="6" /><circle cx="4.5" cy="12" r="1" fill="currentColor" stroke="none" /><line x1="8" y1="12" x2="20" y2="12" /><circle cx="4.5" cy="18" r="1" fill="currentColor" stroke="none" /><line x1="8" y1="18" x2="20" y2="18" /></svg>;
    case "insights":
      return <svg {...common}><circle cx="12" cy="12" r="1.8" /><line x1="12" y1="2" x2="12" y2="7" /><line x1="12" y1="17" x2="12" y2="22" /><line x1="2" y1="12" x2="7" y2="12" /><line x1="17" y1="12" x2="22" y2="12" /></svg>;
    case "simulation":
      return <svg {...common} fill="currentColor"><polygon points="6,4 20,12 6,20" /></svg>;
    case "alerts":
      return <svg {...common}><path d="M5 17 h14 a1 1 0 0 0 0 -2 c-1.5 -1 -2 -3 -2 -6 a5 5 0 0 0 -10 0 c0 3 -0.5 5 -2 6 a1 1 0 0 0 0 2 Z" /><path d="M9.5 19 a2.5 2.5 0 0 0 5 0" /></svg>;
    case "droplet":
      return <svg {...common}><path d="M12 3 C12 3 6 11 6 15.5 A6 6 0 0 0 18 15.5 C18 11 12 3 12 3 Z" /></svg>;
    case "truck":
      return <svg {...common}><rect x="2" y="8" width="12" height="8" rx="1.2" /><path d="M14 11h4l3 3v2h-7z" /><circle cx="6.5" cy="18" r="1.6" fill="currentColor" stroke="none" /><circle cx="17" cy="18" r="1.6" fill="currentColor" stroke="none" /></svg>;
    default:
      return null;
  }
}

const NAV_ITEMS = [
  { id: "overview", label: "Dashboard", icon: "dashboard" },
  { id: "network", label: "Map", icon: "network" },
  { id: "depots", label: "Depots", icon: "depot" },
  { id: "stations", label: "Stations", icon: "station" },
  { id: "recommendations", label: "AI Insights", icon: "insights" },
  { id: "history", label: "Allocations", icon: "allocations" },
  { id: "alerts-section", label: "Alerts", icon: "alerts" },
];

/* ---- Circular progress gauge (service level) — plain ring, no fabricated data ---- */

function Gauge({ value, size = 76, stroke = 9 }: { value: number | null; size?: number; stroke?: number }) {
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const v = value == null ? 0 : Math.min(1, Math.max(0, value));
  const offset = c * (1 - v);
  return (
    <div className="gauge-wrap" style={{ width: size, height: size }}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--border)" strokeWidth={stroke} />
        <circle
          cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--accent)" strokeWidth={stroke}
          strokeDasharray={c} strokeDashoffset={offset} strokeLinecap="round"
          transform={`rotate(-90 ${size / 2} ${size / 2})`}
        />
      </svg>
      <div className="gauge-center">
        <strong>{value == null ? "—" : Math.round(value * 100)}</strong>
        <span>/100</span>
      </div>
    </div>
  );
}

/* ---- Fleet map: schematic depot -> station layout (no real geo-coordinates in the
   simulator's data model), with trucks positioned along each route by real transit
   progress (created/departure/expected-arrival ticks) — not animated for show, this
   reflects actual allocation state each poll. ---- */

type Node = { id: string; name: string; x: number; y: number };

function allocProgress(a: Allocation, currentTick: number | null): number {
  if (a.status === "ARRIVED") return 1;
  if (currentTick == null) return 0;
  if (a.departure_tick == null) return 0.03; // queued at the depot, not yet dispatched
  if (a.expected_arrival_tick == null || a.expected_arrival_tick <= a.departure_tick) return 0.5;
  const t = (currentTick - a.departure_tick) / (a.expected_arrival_tick - a.departure_tick);
  return Math.min(1, Math.max(0, t));
}

function MapPanel({ state }: { state: NetworkState | null }) {
  const layout = useMemo(() => {
    if (!state) return null;
    const width = 820;
    const rows = Math.max(state.depots.length, state.stations.length, 1);
    const height = Math.max(240, rows * 72 + 40);
    const colX = { depot: 90, station: width - 90 };

    const place = (list: { id: string; name: string }[], x: number): Node[] => {
      const step = list.length > 1 ? (height - 60) / (list.length - 1) : 0;
      return list.map((item, i) => ({
        id: item.id,
        name: item.name || item.id,
        x,
        y: list.length > 1 ? 30 + i * step : height / 2,
      }));
    };

    const depotNodes = place(state.depots, colX.depot);
    const stationNodes = place(state.stations, colX.station);
    const depotPos = new Map(depotNodes.map((n) => [n.id, n]));
    const stationPos = new Map(stationNodes.map((n) => [n.id, n]));

    const routeLines = state.routes
      .map((r) => {
        const from = depotPos.get(r.source_depot_id);
        const to = stationPos.get(r.destination_station_id);
        if (!from || !to) return null;
        return { id: r.id, from, to, available: r.status === "AVAILABLE" };
      })
      .filter((r): r is { id: string; from: Node; to: Node; available: boolean } => r !== null);

    const currentTick = state.instance?.tick ?? null;
    const trucks = state.allocations
      .filter((a) => a.status === "PENDING" || a.status === "IN_TRANSIT")
      .map((a) => {
        const from = depotPos.get(a.source_depot_id);
        const to = stationPos.get(a.destination_station_id);
        if (!from || !to) return null;
        const t = allocProgress(a, currentTick);
        return {
          id: a.id,
          x: from.x + (to.x - from.x) * t,
          y: from.y + (to.y - from.y) * t,
          angle: (Math.atan2(to.y - from.y, to.x - from.x) * 180) / Math.PI,
          inTransit: a.status === "IN_TRANSIT",
          label: `#${a.id} ${a.fuel_type} ${fmt(a.quantity)} L · ${a.source_depot_id} → ${a.destination_station_id} · ${a.status}`,
        };
      })
      .filter((t): t is NonNullable<typeof t> => t !== null);

    return { width, height, depotNodes, stationNodes, routeLines, trucks };
  }, [state]);

  if (!layout) {
    return <div className="map-panel"><p className="empty" style={{ padding: 16 }}>Loading network…</p></div>;
  }

  const { width, height, depotNodes, stationNodes, routeLines, trucks } = layout;

  return (
    <div className="map-panel">
      <svg className="map-svg" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Fleet deployment map">
        {routeLines.map((r) => (
          <line
            key={r.id}
            x1={r.from.x} y1={r.from.y} x2={r.to.x} y2={r.to.y}
            stroke={r.available ? "var(--ok)" : "var(--bad)"}
            strokeWidth={1.5}
            strokeDasharray={r.available ? undefined : "5 4"}
            opacity={0.45}
          >
            <title>{r.id} · {r.available ? "available" : "disrupted"}</title>
          </line>
        ))}

        {depotNodes.map((n) => (
          <g key={n.id} transform={`translate(${n.x} ${n.y})`}>
            <title>{n.name} (depot)</title>
            <polygon points="-10,-2 0,-10 10,-2" fill="var(--ink)" />
            <rect x="-9" y="-2" width="18" height="11" rx="1.5" fill="var(--ink)" />
            <text y={30} textAnchor="middle" fontSize="11" fontWeight={600} fill="var(--text)">{n.name}</text>
          </g>
        ))}

        {stationNodes.map((n) => (
          <g key={n.id} transform={`translate(${n.x} ${n.y})`}>
            <title>{n.name} (destination)</title>
            <circle cx="0" cy="-4" r="7" fill="var(--accent)" />
            <polygon points="-6,1 6,1 0,13" fill="var(--accent)" />
            <circle cx="0" cy="-4" r="2.4" fill="#fff" />
            <text y={30} textAnchor="middle" fontSize="11" fontWeight={600} fill="var(--text)">{n.name}</text>
          </g>
        ))}

        {trucks.map((t) => (
          <g key={t.id} transform={`translate(${t.x} ${t.y}) rotate(${t.angle})`}>
            <title>{t.label}</title>
            <rect x="-9" y="-5" width="14" height="8" rx="1.5" fill={t.inTransit ? "var(--accent)" : "#fff"} stroke="var(--ink)" strokeWidth={1.2} />
            <rect x="5" y="-3" width="5" height="6" rx="1" fill={t.inTransit ? "var(--accent)" : "#fff"} stroke="var(--ink)" strokeWidth={1.2} />
            <circle cx="-5" cy="4.5" r="1.8" fill="var(--ink)" />
            <circle cx="3" cy="4.5" r="1.8" fill="var(--ink)" />
          </g>
        ))}
      </svg>
      <div className="map-legend">
        <span className="map-legend-item"><span className="map-legend-swatch" style={{ background: "var(--ink)" }} /> Depot</span>
        <span className="map-legend-item"><span className="map-legend-swatch" style={{ background: "var(--accent)" }} /> Station (destination)</span>
        <span className="map-legend-item"><span className="map-legend-swatch" style={{ background: "var(--ok)" }} /> Route available</span>
        <span className="map-legend-item"><span className="map-legend-swatch" style={{ background: "var(--bad)" }} /> Route disrupted</span>
        <span className="map-legend-item"><span className="map-legend-swatch" style={{ background: "var(--accent)", borderRadius: "50%" }} /> Truck in transit</span>
        <span>{trucks.length} truck{trucks.length === 1 ? "" : "s"} currently deployed</span>
      </div>
    </div>
  );
}

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
  const [activeNav, setActiveNav] = useState("overview");

  const alerts = useMemo(() => computeAlerts(state), [state]);

  const [eventType, setEventType] = useState<EventType>("demand_spike");
  const [eventStations, setEventStations] = useState<string[]>([]);
  const [eventRoutes, setEventRoutes] = useState<string[]>([]);
  const [eventDepots, setEventDepots] = useState<string[]>([]);
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

  // Lightweight scroll-spy for the sidebar nav — highlights whichever section is nearest the top.
  const sectionIds = useRef(NAV_ITEMS.map((n) => n.id));
  useEffect(() => {
    function onScroll() {
      let closest = sectionIds.current[0];
      let closestDist = Infinity;
      for (const id of sectionIds.current) {
        const el = document.getElementById(id);
        if (!el) continue;
        const dist = Math.abs(el.getBoundingClientRect().top - 90);
        if (dist < closestDist) {
          closestDist = dist;
          closest = id;
        }
      }
      setActiveNav(closest);
    }
    window.addEventListener("scroll", onScroll, { passive: true });
    onScroll();
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

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
    let acknowledged = false;
    if (rec.review === "HUMAN_REVIEW") {
      const ok = confirm(
        `This recommendation is flagged for human review (low confidence or fallback policy). ` +
          `Submit ${rec.action.quantity.toFixed(0)} L ${rec.fuel_type} to ${rec.station_id} anyway?`,
      );
      if (!ok) return;
      acknowledged = true;
    }
    setExecuting(rec.id);
    try {
      await executeRecommendation(rec, acknowledged);
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

  function toggleFrom(list: string[], setList: (v: string[]) => void, id: string) {
    setList(list.includes(id) ? list.filter((x) => x !== id) : [...list, id]);
  }

  async function submitEvent() {
    // route_ids/depot_ids empty does NOT mean "all" despite the integration guide —
    // verified live: an empty list disrupts nothing. A real target list is required.
    if (eventType === "route_disruption" && eventRoutes.length === 0) {
      alert("Pick at least one route to disrupt — an empty selection has no effect (simulator quirk, not a UI bug).");
      return;
    }
    if (eventType === "depot_constraint" && eventDepots.length === 0) {
      alert("Pick at least one depot to constrain — an empty selection has no effect (simulator quirk, not a UI bug).");
      return;
    }
    if (state?.instance?.status !== "RUNNING") {
      const ok = confirm(
        "Simulator is not RUNNING. The event will sit SCHEDULED and won't take effect until you Run or Step past its start tick. Inject anyway?",
      );
      if (!ok) return;
    }
    await runSimAction(() =>
      injectEvent({
        type: eventType,
        duration_ticks: eventDuration,
        station_ids: eventStations,
        route_ids: eventRoutes,
        depot_ids: eventDepots,
        multiplier: eventType === "demand_spike" ? eventMultiplier : undefined,
      }),
    );
  }

  async function submitFault() {
    await runSimAction(() => injectFault({ type: faultType, duration_seconds: faultDuration }));
  }

  const isRunning = state?.instance?.status === "RUNNING";

  // ---- KPI strip: all derived from real NetworkState, nothing fabricated ----
  const kpis = useMemo(() => {
    if (!state) return null;
    const fuels = ["DIESEL", "PETROL", "OCTANE"] as const;
    const totalFuel =
      state.depots.reduce((sum, d) => sum + fuels.reduce((s, f) => s + d.inventory[f], 0), 0) +
      state.stations.reduce((sum, s) => sum + fuels.reduce((s2, f) => s2 + s.inventory[f], 0), 0);

    const inTransit = state.allocations.filter((a) => a.status === "PENDING" || a.status === "IN_TRANSIT").length;

    const stockedOut = state.stations.filter((s) => fuels.some((f) => s.capacity[f] > 0 && s.inventory[f] === 0)).length;

    return {
      totalFuel,
      inTransit,
      totalAllocations: state.allocations.length,
      stockedOut,
      totalStations: state.stations.length,
      serviceLevel: state.metrics?.service_level ?? null,
      allocationLiters: state.metrics?.allocation_liters ?? 0,
      allocationFailures: state.metrics?.allocation_failures ?? 0,
    };
  }, [state]);

  const serviceLevelStatus = (v: number | null) => {
    if (v == null) return { label: "No data yet", cls: "" };
    if (v >= 0.9) return { label: "Doing great", cls: "" };
    if (v >= 0.7) return { label: "Stable", cls: "" };
    if (v >= 0.5) return { label: "Under pressure", cls: "" };
    return { label: "At risk", cls: "error-text" };
  };

  // ---- Recent activity timeline (real allocations, newest first) ----
  const recentActivity = useMemo(() => history.slice(-6).reverse(), [history]);

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="sidebar-brand">
          <div className="sidebar-brand-icon"><Icon name="droplet" size={18} /></div>
          <div className="sidebar-brand-text">
            <strong>BUP Fuel Supply</strong>
            <span>Intelligence &amp; Resilience</span>
          </div>
        </div>
        <div className="sidebar-section-label">Overview</div>
        <nav className="sidebar-nav">
          {NAV_ITEMS.map((item) => (
            <a
              key={item.id}
              href={`#${item.id}`}
              className={activeNav === item.id ? "active" : ""}
              onClick={(e) => {
                e.preventDefault();
                document.getElementById(item.id)?.scrollIntoView({ behavior: "smooth", block: "start" });
              }}
            >
              <Icon name={item.icon} />
              {item.label}
            </a>
          ))}
        </nav>
        <div className="sidebar-spacer" />
        <div className="sidebar-sim">
          <div className="sidebar-sim-label">Simulation</div>
          <div className="sidebar-sim-tick">
            Tick {state?.instance?.tick ?? "—"} <Pill status={state?.instance?.status ?? "—"} />
          </div>
          <div className="sidebar-sim-btns">
            <button className="btn btn-primary btn-sm" disabled={simBusy || isRunning} onClick={() => runSimAction(simRun)}>Run</button>
            <button className="btn btn-ghost btn-sm" disabled={simBusy || !isRunning} onClick={() => runSimAction(simPause)} style={{ color: "#fff", borderColor: "rgba(255,255,255,0.2)" }}>Pause</button>
            <button className="btn btn-ghost btn-sm" disabled={simBusy} onClick={() => runSimAction(simStep)} style={{ color: "#fff", borderColor: "rgba(255,255,255,0.2)" }}>Step</button>
            <button
              className="btn btn-danger btn-sm"
              disabled={simBusy}
              onClick={() => confirm("Reset wipes all progress and reloads the baseline scenario. Continue?") && runSimAction(simReset)}
            >
              Reset
            </button>
          </div>
        </div>
      </aside>

      <main className="main">
        <div className="topbar" id="overview">
          <h1>Fuel Supply Operations</h1>
          <div className="statline">
            <span>Core: <Pill status={health} /></span>
            <span>SSE: <Pill status={state?.sse_connected ? "OPEN" : "OUTAGE"} /></span>
            {state?.any_stale && <span className="pill pill-warn">stale data</span>}
          </div>
        </div>

        <div className="kpi-row">
          <div className="kpi-card">
            <div className="kpi-icon accent"><Icon name="droplet" /></div>
            <div className="kpi-body">
              <p className="kpi-label">Total fuel in network</p>
              <div className="kpi-value">{kpis ? `${fmt(kpis.totalFuel)} L` : "—"}</div>
              <p className="kpi-sub">Depots + stations, live</p>
            </div>
          </div>
          <div className="kpi-card">
            <div className="kpi-icon ok"><Icon name="truck" /></div>
            <div className="kpi-body">
              <p className="kpi-label">Allocations in transit</p>
              <div className="kpi-value">{kpis ? `${kpis.inTransit} / ${kpis.totalAllocations}` : "—"}</div>
              <p className="kpi-sub">Pending or moving</p>
            </div>
          </div>
          <div className="kpi-card">
            <div className="kpi-icon bad"><Icon name="alerts" /></div>
            <div className="kpi-body">
              <p className="kpi-label">Station stockouts</p>
              <div className="kpi-value">{kpis ? `${kpis.stockedOut} / ${kpis.totalStations}` : "—"}</div>
              <p className="kpi-sub">At 0% on any fuel</p>
            </div>
          </div>
          <div className="gauge-card">
            <Gauge value={kpis?.serviceLevel ?? null} />
            <div className="gauge-body">
              <p>Service level</p>
              <p className="gauge-status">{serviceLevelStatus(kpis?.serviceLevel ?? null).label}</p>
              <p className="kpi-sub">{kpis ? `${fmt(kpis.allocationLiters)} L delivered · ${kpis.allocationFailures} failed` : "cumulative since reset"}</p>
            </div>
          </div>
        </div>

        <div className="grid-2" id="network">
          <div className="card">
            <h2>Fleet map</h2>
            <MapPanel state={state} />
          </div>

          <div className="card">
            <h2>Live activity</h2>
            {recentActivity.length === 0 && <p className="empty">No allocations yet.</p>}
            {recentActivity.length > 0 && (
              <div className="timeline">
                {recentActivity.map((a) => {
                  const level = a.status === "FAILED" ? "bad" : a.status === "ARRIVED" ? "ok" : "info";
                  return (
                    <div className="timeline-item" key={a.id}>
                      <span className={`timeline-dot ${level}`} />
                      <p className="timeline-title">Allocation #{a.id} — {a.status}</p>
                      <p className="timeline-text">
                        {fmt(a.quantity)} L {a.fuel_type} · {a.source_depot_id} → {a.destination_station_id} · tick {a.created_tick}
                      </p>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </div>

        <div className="card" id="alerts-section">
          <div className="grid-cols">
            <div>
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

            <div>
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
        </div>

        <div className="card" id="sim-controls">
          <h2>Simulation controls</h2>
          <p className="rec-meta" style={{ marginBottom: 12 }}>Run / pause / step / reset live in the sidebar. Inject scripted crisis events and dependency faults below.</p>

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
                      <input type="checkbox" checked={eventStations.includes(s.id)} onChange={() => toggleFrom(eventStations, setEventStations, s.id)} />
                      {s.name || s.id}
                    </label>
                  ))}
                </div>
              )}
              {eventType === "route_disruption" && state && (
                <div className="checkbox-group" style={{ marginBottom: 10 }}>
                  {state.routes.map((r) => (
                    <label key={r.id}>
                      <input type="checkbox" checked={eventRoutes.includes(r.id)} onChange={() => toggleFrom(eventRoutes, setEventRoutes, r.id)} />
                      {r.source_depot_id} → {r.destination_station_id}
                    </label>
                  ))}
                </div>
              )}
              {eventType === "depot_constraint" && state && (
                <div className="checkbox-group" style={{ marginBottom: 10 }}>
                  {state.depots.map((d) => (
                    <label key={d.id}>
                      <input type="checkbox" checked={eventDepots.includes(d.id)} onChange={() => toggleFrom(eventDepots, setEventDepots, d.id)} />
                      {d.name || d.id}
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

        <div className="grid-cols" id="depots">
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

          <div className="card" id="stations">
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

        <div className="card" id="recommendations">
          <h2>AI Insights</h2>
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
          {assessment?.recommendations.map((rec, i) => (
            <div key={rec.id} className={`rec-card ${i === 0 ? "featured" : ""}`}>
              <div className="rec-head">
                <span>{i === 0 && <span className="pill pill-ok" style={{ marginRight: 8 }}>TOP PICK</span>}{rec.station_id} — {rec.fuel_type}</span>
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

        <div className="card" id="history">
          <h2>Allocations / decision history</h2>
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
    </div>
  );
}
