import { sleep } from 'k6';
import { hitTargets, thresholds } from './lib.js';

export const options = {
  vus: 1,
  duration: '30s',
  thresholds,
};

export default function () {
  hitTargets();
  sleep(1);
}
