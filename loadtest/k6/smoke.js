import { sleep } from 'k6';
import { fetchStateOnce, runIteration, thresholds } from './lib.js';

export const options = {
  vus: 1,
  duration: '30s',
  thresholds,
};

export function setup() {
  return { state: fetchStateOnce() };
}

export default function (data) {
  runIteration(data.state, __ITER);
  sleep(1);
}
