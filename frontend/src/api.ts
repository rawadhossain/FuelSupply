// Relative URLs only — nginx strips /api and proxies to Core. See docs/frontend-api-contract.md.

export type FuelAmounts = { DIESEL: number; PETROL: number; OCTANE: number };

export type Depot = {
  id: string; name: string; region_id: string; status: string;
  dispatch_capacity_per_tick: number; capacity: FuelAmounts; inventory: FuelAmounts;
};

export type Station = {
  id: string; name: string; region_id: string; status: string;
  demand_profile: string; demand_multiplier: number; capacity: FuelAmounts; inventory: FuelAmounts;
};

export type Route = {
  id: string; source_depot_id: string; destination_station_id: string;
  transit_ticks: number; max_shipment: number; status: string;
};

export type DomainEvent = {
  id: number; type: string; status: string; start_tick: number; end_tick: number; parameters: Record<string, unknown>;
};

export type Allocation = {
  id: number; idempotency_key: string; source_depot_id: string; destination_station_id: string;
  route_id: string; fuel_type: string; quantity: number; created_tick: number;
  departure_tick: number | null; expected_arrival_tick: number | null; actual_arrival_tick: number | null;
  status: string; failure_reason: string | null;
};

export type NetworkState = {
  instance: { tick: number; status: string; seed: number } | null;
  regions: { id: string; name: string; demand_factor: number }[];
  depots: Depot[];
  stations: Station[];
  routes: Route[];
  supply_arrivals: unknown[];
  events: DomainEvent[];
  allocations: Allocation[];
  metrics: { service_level: number; allocation_liters: number; allocation_failures: number } | null;
  sse_connected: boolean;
  last_poll_error: string | null;
  any_stale: boolean;
};

export type RecommendationAction = { source_depot_id: string; route_id: string; quantity: number };

export type Recommendation = {
  id: string;
  station_id: string;
  fuel_type: string;
  action: RecommendationAction;
  alternatives: { route_id: string | null; quantity: number; why_not: string }[];
  constraints: string[];
  binding_constraints: string[];
  signals: string[];
  impact: {
    stockout_before_h: number; stockout_after_h: number;
    unmet_before_l: number; unmet_after_l: number; risk_before: number; risk_after: number;
  };
  confidence: number;
  review: string;
  policy: string;
  explanation: string;
};

export type Assessment = {
  tick: number;
  policy: string;
  recommendations: Recommendation[];
  degraded?: boolean;
  degraded_reason?: string;
  [key: string]: unknown;
};

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    const detail = body?.detail;
    const message = typeof detail === "object" ? detail?.message || JSON.stringify(detail) : String(body);
    throw new Error(`${res.status}: ${message}`);
  }
  return res.json() as Promise<T>;
}

export type HealthSummary = { status: string; components: { name: string; status: string }[] };

export const getHealth = () => req<{ status: string; service: string }>("/api/health");
export const getHealthSummary = () => req<HealthSummary>("/api/internal/health-summary");
export const getState = () => req<NetworkState>("/api/internal/state");
export const getStoreState = () => req<Record<string, unknown>>("/api/internal/store-state");

export const getRecommendations = (policy: "heuristic" | "lp" = "heuristic", narrate = false) =>
  req<Assessment>("/api/internal/recommendations", {
    method: "POST",
    body: JSON.stringify({ policy, narrate }),
  });

export const executeRecommendation = (rec: Recommendation, acknowledged: boolean) =>
  req<Allocation>("/api/internal/allocations/execute", {
    method: "POST",
    body: JSON.stringify({
      recommendation_id: rec.id,
      station_id: rec.station_id,
      fuel_type: rec.fuel_type,
      action: rec.action,
      intent: `intelligence-${rec.policy}`,
      acknowledged,
    }),
  });

// ---- Simulation controls — proxied through Core, never the sim directly ----

export const simRun = () => req<Record<string, unknown>>("/api/internal/admin/run", { method: "POST" });
export const simPause = () => req<Record<string, unknown>>("/api/internal/admin/pause", { method: "POST" });
export const simStep = () => req<Record<string, unknown>>("/api/internal/admin/step", { method: "POST" });
export const simReset = () => req<Record<string, unknown>>("/api/internal/admin/reset", { method: "POST" });

export type EventType =
  | "demand_spike" | "route_disruption" | "station_outage" | "depot_constraint"
  | "shipment_delay" | "supply_shortfall";

export const injectEvent = (body: {
  type: EventType; duration_ticks: number; station_ids?: string[]; route_ids?: string[];
  depot_ids?: string[]; multiplier?: number;
}) => req<Record<string, unknown>>("/api/internal/admin/events", { method: "POST", body: JSON.stringify(body) });

export type FaultType = "latency" | "unavailable" | "error_rate" | "stale_data" | "stream_disconnect";

export const injectFault = (body: { type: FaultType; duration_seconds: number; delay_ms?: number; probability?: number }) =>
  req<Record<string, unknown>>("/api/internal/admin/faults", { method: "POST", body: JSON.stringify(body) });

export const clearFaults = () => req<Record<string, unknown>>("/api/internal/admin/faults/clear", { method: "POST" });
