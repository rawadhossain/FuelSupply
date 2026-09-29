import { sleep } from 'k6';
import { hitTargets, thresholds } from './lib.js';

export const options = {
  stages: [
    { duration: '20s', target: 10 },
    { duration: '20s', target: 150 },
    { duration: '20s', target: 10 },
  ],
  thresholds,
};

export default function () {
  hitTargets();
  sleep(1);
}
