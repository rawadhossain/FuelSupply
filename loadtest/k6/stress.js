import { sleep } from 'k6';
import { hitTargets, thresholds } from './lib.js';

export const options = {
  stages: [
    { duration: '1m', target: 50 },
    { duration: '2m', target: 200 },
    { duration: '1m', target: 0 },
  ],
  thresholds,
};

export default function () {
  hitTargets();
  sleep(1);
}
