// Resilience scenario: constant ~50 VUs against recommend for ~2 minutes.
// Halfway through, scripts/loadtest.sh stops the intelligence container for
// 20s then restarts it - this script just keeps hammering /internal/recommendations
// throughout and lets the checks/error-rate capture what happens during the outage
// (core's IntelligenceUnavailableError fallback path should keep it answering 200s
// with degraded:true instead of failing outright).
import { sleep } from 'k6';
import { fetchStateOnce, runIteration, thresholds } from './lib.js';

export const options = {
  vus: 50,
  duration: '2m',
  thresholds,
};

export function setup() {
  return { state: fetchStateOnce() };
}

export default function (data) {
  runIteration(data.state, __ITER);
  sleep(1);
}
