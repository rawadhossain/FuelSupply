import { sleep } from 'k6';
import { hitTargets, thresholds } from './lib.js';

export const options = {
  stages: [
    { duration: '30s', target: 10 },
    { duration: '2m', target: 50 },
    { duration: '30s', target: 0 },
  ],
  thresholds,
};

export default function () {
  hitTargets();
  sleep(1);
}
