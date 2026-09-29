import { sleep } from 'k6';
import { fetchStateOnce, runIteration, thresholds } from './lib.js';

export const options = {
  stages: [
    { duration: '20s', target: 10 },
    { duration: '20s', target: 150 },
    { duration: '20s', target: 10 },
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
