#!/usr/bin/env node
/**
 * Unit tests for the camera/mic error-mapping logic (epic §8) -- no
 * browser, no React Native, no WebRTC stack needed, since the mapping
 * itself is pure. Checks that web-test-client/media-errors.js and
 * mobile/src/services/mediaErrors.ts agree on every DOMException name a
 * real getUserMedia() call can reject with.
 *
 * Run: node scripts/test_media_errors.js
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const results = { pass: [], fail: [] };
function check(name, cond) {
  (cond ? results.pass : results.fail).push(name);
  console.log((cond ? '  [PASS] ' : '  [FAIL] ') + name);
}

const { mapMediaError: webMap, isWebRTCSupported } = require(path.join(__dirname, '..', 'web-test-client', 'media-errors.js'));

const tsSource = fs.readFileSync(path.join(__dirname, '..', 'mobile', 'src', 'services', 'mediaErrors.ts'), 'utf8');
const stripped = tsSource
  .replace(/export interface MediaErrorInfo[^}]*}\n/, '')
  .replace(/export function mapMediaError\(error: unknown\): MediaErrorInfo/, 'function mapMediaError(error)')
  .replace(/const name = \(error as \{ name\?: string \} \| null\)\?\.name \?\? '';/, "const name = (error && error.name) || '';")
  + '\nmodule.exports = { mapMediaError };\n';
const sandbox = { module: { exports: {} } };
vm.createContext(sandbox);
vm.runInContext(stripped, sandbox, { filename: 'mediaErrors.ts (stripped)' });
const mobileMap = sandbox.module.exports.mapMediaError;

check('mobile mediaErrors.ts loaded and evaluated successfully', typeof mobileMap === 'function');

const errorNames = [
  'NotAllowedError',
  'PermissionDeniedError',
  'NotFoundError',
  'DevicesNotFoundError',
  'NotReadableError',
  'TrackStartError',
  'OverconstrainedError',
  'ConstraintNotSatisfiedError',
  'SecurityError',
  'SomeUnrecognizedError',
  '',
];

for (const name of errorNames) {
  const err = name ? { name } : null;
  const webResult = webMap(err);
  const mobileResult = mobileMap(err);
  // The `code` is the cross-client contract (what a test or future
  // analytics event would key off of) and must match exactly. The
  // `message` is user-facing copy and is allowed to differ in wording
  // between platforms (e.g. "browser settings" vs "device settings") --
  // both must just be non-empty.
  const codesMatch = webResult.code === mobileResult.code;
  const bothHaveMessages = webResult.message.length > 0 && mobileResult.message.length > 0;
  check(`web and mobile agree on the error code for mapMediaError(name='${name || '(null)'}')`, codesMatch);
  check(`both platforms produce a non-empty message for name='${name || '(null)'}'`, bothHaveMessages);
}

check('NotAllowedError maps to permission_denied', webMap({ name: 'NotAllowedError' }).code === 'permission_denied');
check('NotFoundError maps to no_device', webMap({ name: 'NotFoundError' }).code === 'no_device');
check('NotReadableError maps to device_in_use', webMap({ name: 'NotReadableError' }).code === 'device_in_use');
check('unrecognized error name falls back to a generic, non-empty message', webMap({ name: 'Bogus' }).code === 'unknown' && webMap({ name: 'Bogus' }).message.length > 0);
check('null/undefined error does not throw and falls back to unknown', (() => { try { return webMap(null).code === 'unknown' && webMap(undefined).code === 'unknown'; } catch { return false; } })());

check('isWebRTCSupported is a function (web-only check, no mobile equivalent needed)', typeof isWebRTCSupported === 'function');

console.log('\n' + '='.repeat(60));
console.log(`RESULT: ${results.pass.length} passed, ${results.fail.length} failed`);
if (results.fail.length) console.log('Failed:', results.fail);
console.log('='.repeat(60));
process.exit(results.fail.length ? 1 : 0);
