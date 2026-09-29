import { sleep } from 'k6';
import { fetchStateOnce, runIteration, thresholds } from './lib.js';

export const options = {
  stages: [
    { duration: '30s', target: 50 },
    { duration: '1m', target: 50 },
    { duration: '30s', target: 0 },
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
