// Shared helpers for the k6 scripts in this directory.
//
// Exercises the REAL decision-making endpoints (not /health):
//   - assess:    POST {INTEL_URL}/intel/assess           (intelligence: snapshot -> recommendations)
//   - recommend: POST {CORE_URL}/internal/recommendations (core -> intelligence bridge, the e2e decision path)
//   - state:     GET  {CORE_URL}/internal/state           (dashboard backend read path)
//
// setup() fetches one live GET /internal/state from core and builds a
// realistic snapshot body from it for the assess scenario. Per VU/iteration,
// fuel-type ordering and station inventory are lightly jittered so requests
// aren't byte-identical while remaining schema-valid.
import http from 'k6/http';
import { check } from 'k6';

export const CORE_URL = __ENV.CORE_URL || 'http://core:8100';
export const INTEL_URL = __ENV.INTEL_URL || 'http://intelligence:8200';
export const SCENARIO = __ENV.SCENARIO || 'recommend';

export const thresholds = {
  'http_req_duration': ['p(95)<500'],
  'http_req_failed': ['rate<0.01'],
};

// Mark thresholds informational: a failed threshold must not abort the run
// (abortOnFail defaults to false already, but we set every threshold
// explicitly so a report always gets written even under stress/spike).
export function withAbortOnFail(list) {
  return list.map((t) => ({ threshold: t, abortOnFail: false }));
}

// Build a realistic /intel/assess body from a live /internal/state snapshot.
// core's /internal/state shape (regions/depots/stations/routes/supply_arrivals/
// events/allocations/metrics) is close to but not identical to intel/assess's
// expected `snapshot` (instance/regions/stations/depots/routes/supply-arrivals/
// allocations/events) - map field names and drop what assess doesn't need.
export function buildAssessBody(state, jitterSeed) {
  const s = state || {};
  const stations = (s.stations || []).map((st) => {
    const inv = { ...(st.inventory || {}) };
    // Light jitter: nudge each fuel level +/-2% per iteration so requests vary
    // without breaking schema validity.
    for (const k of Object.keys(inv)) {
      const factor = 1 + (((jitterSeed + k.length) % 5) - 2) / 100;
      inv[k] = Math.max(0, Math.round(inv[k] * factor * 1000) / 1000);
    }
    return { ...st, inventory: inv };
  });

  return {
    snapshot: {
      instance: s.instance || { id: 1, tick: 0, status: 'RUNNING' },
      regions: s.regions || [],
      stations,
      depots: s.depots || [],
      routes: s.routes || [],
      'supply-arrivals': s.supply_arrivals || [],
      allocations: s.allocations || [],
      events: s.events || [],
    },
    demand_rows: [],
    stale: !!s.any_stale,
    policy: 'heuristic',
    narrate: false,
  };
}

export function runIteration(state, iterSeed) {
  if (SCENARIO === 'assess') {
    const body = JSON.stringify(buildAssessBody(state, iterSeed));
    const res = http.post(`${INTEL_URL}/intel/assess`, body, {
      headers: { 'Content-Type': 'application/json' },
      tags: { scenario: 'assess' },
    });
    check(res, {
      'assess status is 200': (r) => r.status === 200,
      'assess body has recommendation content': (r) => {
        try {
          const b = r.json();
          return Array.isArray(b.recommendations);
        } catch (e) {
          return false;
        }
      },
    });
    return res;
  }

  if (SCENARIO === 'state') {
    const res = http.get(`${CORE_URL}/internal/state`, { tags: { scenario: 'state' } });
    check(res, {
      'state status is 200': (r) => r.status === 200,
      'state body has fuel/station content': (r) => {
        try {
          const b = r.json();
          return Array.isArray(b.stations);
        } catch (e) {
          return false;
        }
      },
    });
    return res;
  }

  // default: recommend - the real e2e core -> intelligence decision path
  const body = JSON.stringify({ policy: 'heuristic', narrate: false });
  const res = http.post(`${CORE_URL}/internal/recommendations`, body, {
    headers: { 'Content-Type': 'application/json' },
    tags: { scenario: 'recommend' },
  });
  check(res, {
    'recommend status is 200': (r) => r.status === 200,
    'recommend body has recommendation/fuel content': (r) => {
      try {
        const b = r.json();
        return Array.isArray(b.recommendations);
      } catch (e) {
        return false;
      }
    },
  });
  return res;
}

// setup(): fetch one live /internal/state to seed per-iteration bodies. Used
// by every scenario (assess needs it to build a snapshot; recommend/state
// scenarios call it just to confirm connectivity before the run).
export function fetchStateOnce() {
  const res = http.get(`${CORE_URL}/internal/state`);
  if (res.status !== 200) {
    console.error(`setup: GET /internal/state failed with status ${res.status}`);
    return {};
  }
  try {
    return res.json();
  } catch (e) {
    console.error('setup: /internal/state did not return JSON');
    return {};
  }
}
