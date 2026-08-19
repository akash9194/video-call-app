#!/usr/bin/env node
/**
 * Unit tests for the network-quality bucketing logic (epic §23) --
 * no browser, no React Native, no WebRTC stack needed, since the
 * bucketing decision itself is pure arithmetic. Checks:
 *
 *   1. web-test-client/network-quality.js produces the right bucket for
 *      each loss-ratio test case.
 *   2. mobile/src/services/networkQuality.ts's bucketNetworkQuality (type
 *      annotations stripped so plain Node can eval it -- this file has no
 *      React Native dependencies, so that's safe) produces IDENTICAL
 *      output to the web version for every case, so both clients agree on
 *      what "poor" means.
 *
 * Run: node scripts/test_network_quality.js
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const results = { pass: [], fail: [] };
function check(name, cond) {
  (cond ? results.pass : results.fail).push(name);
  console.log((cond ? '  [PASS] ' : '  [FAIL] ') + name);
}

// -- Load the web client's version (plain JS, CommonJS export) --
const { bucketNetworkQuality: webBucket } = require(path.join(__dirname, '..', 'web-test-client', 'network-quality.js'));

// -- Load the mobile version by stripping TS-only syntax and eval'ing it.
// This file has zero React Native imports (by design, specifically so it
// can be tested this way) -- it's pure TS type annotations on top of the
// same plain-JS logic as the web version.
const tsSource = fs.readFileSync(path.join(__dirname, '..', 'mobile', 'src', 'services', 'networkQuality.ts'), 'utf8');
const stripped = tsSource
  .replace(/export type NetworkQuality[^\n]*\n/, '')
  .replace(/export function bucketNetworkQuality\(lossRatio: number\): NetworkQuality/, 'function bucketNetworkQuality(lossRatio)')
  + '\nmodule.exports = { bucketNetworkQuality };\n';
const sandbox = { module: { exports: {} }, Number };
vm.createContext(sandbox);
vm.runInContext(stripped, sandbox, { filename: 'networkQuality.ts (stripped)' });
const mobileBucket = sandbox.module.exports.bucketNetworkQuality;

check('mobile networkQuality.ts loaded and evaluated successfully', typeof mobileBucket === 'function');

const cases = [
  { lossRatio: 0, expected: 'good' },
  { lossRatio: 0.01, expected: 'good' },
  { lossRatio: 0.019, expected: 'good' },
  { lossRatio: 0.02, expected: 'fair' },
  { lossRatio: 0.05, expected: 'fair' },
  { lossRatio: 0.079, expected: 'fair' },
  { lossRatio: 0.08, expected: 'poor' },
  { lossRatio: 0.5, expected: 'poor' },
  { lossRatio: 1, expected: 'poor' },
  { lossRatio: -1, expected: 'unknown' },
  { lossRatio: NaN, expected: 'unknown' },
  { lossRatio: 'not a number', expected: 'unknown' },
];

for (const { lossRatio, expected } of cases) {
  const webResult = webBucket(lossRatio);
  const mobileResult = mobileBucket(lossRatio);
  check(`web: bucketNetworkQuality(${lossRatio}) === '${expected}'`, webResult === expected);
  check(`mobile: bucketNetworkQuality(${lossRatio}) === '${expected}'`, mobileResult === expected);
  check(`web and mobile agree for lossRatio=${lossRatio}`, webResult === mobileResult);
}

console.log('\n' + '='.repeat(60));
console.log(`RESULT: ${results.pass.length} passed, ${results.fail.length} failed`);
if (results.fail.length) console.log('Failed:', results.fail);
console.log('='.repeat(60));
process.exit(results.fail.length ? 1 : 0);
