#!/usr/bin/env node
/**
 * Unit tests for resolveAudioRoute (epic §22 Bluetooth audio route) --
 * mobile/src/services/audioRoute.ts's one piece of pure decision logic.
 *
 * Honest scope note: this is the ONLY part of the Bluetooth-route feature
 * this sandbox can verify. Everything else -- whether
 * InCallManager.chooseAudioRoute('BLUETOOTH') actually succeeds, whether
 * a Bluetooth headset is connected, whether the Android native module
 * behaves as its source suggests -- needs real Android hardware with a
 * paired Bluetooth audio device, which isn't available here. That native
 * behavior was instead verified by reading the actual library source in
 * node_modules/react-native-incall-manager (both the JS wrapper and the
 * Android/iOS native implementations) before writing CallContext.tsx's
 * Platform.OS guard, not by running it.
 *
 * Run: node scripts/test_audio_route.js
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const results = { pass: [], fail: [] };
function check(name, cond) {
  (cond ? results.pass : results.fail).push(name);
  console.log((cond ? '  [PASS] ' : '  [FAIL] ') + name);
}

const tsSource = fs.readFileSync(path.join(__dirname, '..', 'mobile', 'src', 'services', 'audioRoute.ts'), 'utf8');
const stripped = tsSource
  .replace(/export type AudioRoute[^\n]*\n/, '')
  .replace(/export function resolveAudioRoute\(turningBluetoothOn: boolean, isSpeakerOn: boolean\): AudioRoute/, 'function resolveAudioRoute(turningBluetoothOn, isSpeakerOn)')
  + '\nmodule.exports = { resolveAudioRoute };\n';
const sandbox = { module: { exports: {} } };
vm.createContext(sandbox);
vm.runInContext(stripped, sandbox, { filename: 'audioRoute.ts (stripped)' });
const resolveAudioRoute = sandbox.module.exports.resolveAudioRoute;

check('audioRoute.ts loaded and evaluated successfully', typeof resolveAudioRoute === 'function');

check('turning Bluetooth ON always routes to BLUETOOTH, regardless of speaker state (was on speaker)', resolveAudioRoute(true, true) === 'BLUETOOTH');
check('turning Bluetooth ON always routes to BLUETOOTH, regardless of speaker state (was on earpiece)', resolveAudioRoute(true, false) === 'BLUETOOTH');
check('turning Bluetooth OFF while speaker was on routes back to SPEAKER_PHONE, not EARPIECE', resolveAudioRoute(false, true) === 'SPEAKER_PHONE');
check('turning Bluetooth OFF while speaker was off routes back to EARPIECE, not SPEAKER_PHONE', resolveAudioRoute(false, false) === 'EARPIECE');

console.log('\n' + '='.repeat(60));
console.log(`RESULT: ${results.pass.length} passed, ${results.fail.length} failed`);
if (results.fail.length) console.log('Failed:', results.fail);
console.log('='.repeat(60));
process.exit(results.fail.length ? 1 : 0);
