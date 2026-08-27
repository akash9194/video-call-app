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

// -- Epic §23 adaptive video quality: same cross-client parity check,
// for getVideoEncodingConstraints (web-test-client/video-quality-
// adaptation.js vs mobile/src/services/videoQualityAdaptation.ts).
const { getVideoEncodingConstraints: webConstraints } = require(path.join(__dirname, '..', 'web-test-client', 'video-quality-adaptation.js'));

const vqaSource = fs.readFileSync(path.join(__dirname, '..', 'mobile', 'src', 'services', 'videoQualityAdaptation.ts'), 'utf8');
const vqaStripped = vqaSource
  .replace(/^import[^\n]*\n/m, '')
  .replace(/export interface VideoEncodingConstraints[^}]*}\n/, '')
  .replace(/export function getVideoEncodingConstraints\(quality: NetworkQuality\): VideoEncodingConstraints/, 'function getVideoEncodingConstraints(quality)')
  + '\nmodule.exports = { getVideoEncodingConstraints };\n';
const vqaSandbox = { module: { exports: {} } };
vm.createContext(vqaSandbox);
vm.runInContext(vqaStripped, vqaSandbox, { filename: 'videoQualityAdaptation.ts (stripped)' });
const mobileConstraints = vqaSandbox.module.exports.getVideoEncodingConstraints;

check('mobile videoQualityAdaptation.ts loaded and evaluated successfully', typeof mobileConstraints === 'function');

const qualityTiers = ['good', 'fair', 'poor', 'unknown'];
for (const quality of qualityTiers) {
  const webResult = webConstraints(quality);
  const mobileResult = mobileConstraints(quality);
  const same = webResult.maxBitrate === mobileResult.maxBitrate && webResult.scaleResolutionDownBy === mobileResult.scaleResolutionDownBy;
  check(`web and mobile agree on encoding constraints for quality='${quality}'`, same);
}
check('good quality applies no cap (maxBitrate null)', webConstraints('good').maxBitrate === null);
check('fair quality caps bitrate below good', webConstraints('fair').maxBitrate < 2_000_000 && webConstraints('fair').maxBitrate > 0);
check('poor quality caps bitrate lower than fair', webConstraints('poor').maxBitrate < webConstraints('fair').maxBitrate);
check('poor quality downscales resolution more than fair', webConstraints('poor').scaleResolutionDownBy > webConstraints('fair').scaleResolutionDownBy);
check('unknown quality does not throttle (treated like good)', webConstraints('unknown').maxBitrate === null);

console.log('\n' + '='.repeat(60));
console.log(`RESULT: ${results.pass.length} passed, ${results.fail.length} failed`);
if (results.fail.length) console.log('Failed:', results.fail);
console.log('='.repeat(60));
process.exit(results.fail.length ? 1 : 0);
