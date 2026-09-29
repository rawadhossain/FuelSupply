// Section 17 one-command demo profile: ramp 0 -> 8 VUs in 20s, hold 60s,
// ramp down in 10s. Reuses lib.js so it exercises the same real endpoints
// (default: recommend) as the other k6 scripts here.
import { sleep } from 'k6';
import { fetchStateOnce, runIteration, thresholds } from './lib.js';

export const options = {
  stages: [
    { duration: '20s', target: 8 },
    { duration: '60s', target: 8 },
    { duration: '10s', target: 0 },
  ],
  thresholds,
};

export function setup() {
  return { state: fetchStateOnce() };
}

export default function (data) {
  runIteration(data.state, __ITER);
  sleep(1);
}
