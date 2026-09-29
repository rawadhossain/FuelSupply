// Shared helpers for the k6 scripts in this directory.
import http from 'k6/http';
import { check } from 'k6';

// Defaults hit core and intelligence health/readiness directly on their
// compose/kind host ports. TODO: once the teammate ships the real decision
// endpoint (e.g. POST /intel/assess or a core recommend endpoint), point
// TARGET_PATHS at it instead so the load test exercises the actual workload.
export const CORE_URL = __ENV.CORE_URL || 'http://localhost:8100';
export const INTELLIGENCE_URL = __ENV.INTELLIGENCE_URL || 'http://localhost:8200';
export const TARGET_PATHS = (__ENV.TARGET_PATHS || '/health,/ready').split(',');

export function hitTargets() {
  for (const base of [CORE_URL, INTELLIGENCE_URL]) {
    for (const path of TARGET_PATHS) {
      const res = http.get(`${base}${path}`);
      check(res, {
        [`${base}${path} status is 200`]: (r) => r.status === 200,
      });
    }
  }
}

export const thresholds = {
  http_req_duration: ['p(95)<500'],
  http_req_failed: ['rate<0.01'],
};
