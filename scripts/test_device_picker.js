#!/usr/bin/env node
/**
 * Unit tests for the web-only camera/mic device picker (epic §10):
 * web-test-client/index.html's populateDevicePickers(), fillDeviceSelect(),
 * switchCameraDevice(), and switchMicDevice().
 *
 * These functions are DOM- and WebRTC-API-coupled (document.getElementById,
 * navigator.mediaDevices, RTCRtpSender.replaceTrack), so unlike the pure
 * bucketing/mapping logic tested elsewhere in this repo (network-quality,
 * media-errors), there's no standalone .js module to require() -- the real
 * source lives inline in index.html. This test extracts that exact source
 * (not a reimplementation) and runs it inside a vm sandbox with minimal
 * fake DOM elements and fake MediaDevices/RTCPeerConnection APIs standing
 * in for the browser, asserting on real, observable side effects (option
 * lists built, show/hide, replaceTrack calls, mute-state preservation,
 * error fallback).
 *
 * Honest scope note: this proves the picker's *logic* is correct given
 * whatever the browser's MediaDevices API reports. It does NOT prove real
 * multi-camera/multi-mic hardware enumerates and switches correctly in an
 * actual browser -- that needs a real machine with 2+ physical devices,
 * which this sandbox doesn't have. Flagging that honestly rather than
 * claiming more coverage than this gives.
 *
 * Run: node scripts/test_device_picker.js
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const results = { pass: [], fail: [] };
function check(name, cond) {
  (cond ? results.pass : results.fail).push(name);
  console.log((cond ? '  [PASS] ' : '  [FAIL] ') + name);
}

const html = fs.readFileSync(path.join(__dirname, '..', 'web-test-client', 'index.html'), 'utf8');

const startMarker = '// ---- Epic §10: camera/mic device picker';
const endMarker = "if (navigator.mediaDevices && navigator.mediaDevices.addEventListener)";
const startIdx = html.indexOf(startMarker);
const endIdx = html.indexOf(endMarker);
if (startIdx === -1 || endIdx === -1) {
  console.log('  [FAIL] could not locate device-picker source block in index.html (markers moved?)');
  process.exit(1);
}
const deviceSrc = html.slice(startIdx, endIdx);

// --- Minimal fake DOM element ---
function makeEl() {
  return {
    _options: [],
    style: {},
    value: '',
    innerHTML: '',
    set innerHTML(v) { this._options = []; },
    get innerHTML() { return ''; },
    appendChild(opt) { this._options.push(opt); if (opt.selected === undefined) opt.selected = false; },
  };
}
function makeOption() {
  return { value: '', textContent: '' };
}

function buildSandbox({ cameras, mics, speakers = [], getUserMediaImpl, setSinkIdImpl, speakerSupported = true }) {
  const els = {
    'device-picker': makeEl(),
    'camera-select': makeEl(),
    'mic-select': makeEl(),
    'speaker-select': makeEl(),
    'local-video': { srcObject: null },
    'remote-video': { setSinkId: setSinkIdImpl || (async () => {}) },
  };

  const fakeDocument = {
    createElement: (tag) => (tag === 'option' ? makeOption() : makeEl()),
    getElementById: (id) => els[id],
  };

  const alerts = [];
  const warnings = [];

  const sandbox = {
    document: fakeDocument,
    navigator: {
      mediaDevices: {
        enumerateDevices: async () => [...cameras, ...mics, ...speakers],
        getUserMedia: getUserMediaImpl,
      },
    },
    alert: (msg) => alerts.push(msg),
    console: { warn: (...a) => warnings.push(a.map(String).join(' ')) },
    mapMediaError: null, // filled in below from the real media-errors.js
    // module-scope state the extracted functions read/write directly
    pc: null,
    localStream: null,
    isVideoOn: false,
    isMuted: false,
    currentCameraDeviceId: null,
    currentMicDeviceId: null,
    // §22: normally computed once from `typeof HTMLMediaElement...` at
    // module load; seeded directly here since the sandbox has no real
    // HTMLMediaElement to detect against. speakerSupported: false lets a
    // test prove the picker stays inert on browsers without setSinkId.
    SPEAKER_OUTPUT_SUPPORTED: speakerSupported,
    currentSpeakerDeviceId: null,
  };
  sandbox.mapMediaError = require(path.join(__dirname, '..', 'web-test-client', 'media-errors.js')).mapMediaError;
  vm.createContext(sandbox);
  vm.runInContext(deviceSrc, sandbox, { filename: 'index.html device-picker block' });
  sandbox.__els = els;
  sandbox.__alerts = alerts;
  sandbox.__warnings = warnings;
  return sandbox;
}

function fakeTrack(kind, deviceId) {
  let enabled = true;
  return {
    kind,
    stopped: false,
    stop() { this.stopped = true; },
    getSettings: () => ({ deviceId }),
    get enabled() { return enabled; },
    set enabled(v) { enabled = v; },
  };
}
function fakeStream(tracks) {
  return {
    _tracks: [...tracks],
    getVideoTracks() { return this._tracks.filter(t => t.kind === 'video'); },
    getAudioTracks() { return this._tracks.filter(t => t.kind === 'audio'); },
    getTracks() { return this._tracks; },
    removeTrack(t) { this._tracks = this._tracks.filter(x => x !== t); },
    addTrack(t) { this._tracks.push(t); },
  };
}

async function main() {
  const cameras = [
    { kind: 'videoinput', deviceId: 'cam-1', label: 'Built-in Webcam' },
    { kind: 'videoinput', deviceId: 'cam-2', label: 'USB Camera' },
  ];
  const mics = [
    { kind: 'audioinput', deviceId: 'mic-1', label: 'Built-in Mic' },
  ];

  // ---------------------------------------------------------------
  // 1. populateDevicePickers: option lists + show/hide rules.
  // ---------------------------------------------------------------
  console.log('\n=== 1. populateDevicePickers: options + visibility ===');
  {
    const sb = buildSandbox({ cameras, mics, getUserMediaImpl: async () => { throw new Error('not used in this case'); } });
    sb.pc = {};
    sb.localStream = fakeStream([fakeTrack('video', 'cam-1'), fakeTrack('audio', 'mic-1')]);
    sb.isVideoOn = true;
    sb.currentCameraDeviceId = 'cam-1';
    sb.currentMicDeviceId = 'mic-1';

    await vm.runInContext('populateDevicePickers()', sb);
    await new Promise(r => setTimeout(r, 0)); // let the async fn resolve

    const camSelect = sb.__els['camera-select'];
    const micSelect = sb.__els['mic-select'];
    check('camera select gets 2 options (2 cameras enumerated)', camSelect._options.length === 2);
    check('camera option labels come from device.label', camSelect._options.map(o => o.textContent).includes('USB Camera'));
    check('camera dropdown shown (2 cameras, video on)', camSelect.style.display !== 'none');
    check('mic dropdown hidden (only 1 mic -- nothing to pick)', micSelect.style.display === 'none');
    check('device-picker container shown (camera dropdown visible)', sb.__els['device-picker'].style.display === 'flex');
  }

  // ---------------------------------------------------------------
  // 2. populateDevicePickers: hides camera picker on an audio-only call.
  // ---------------------------------------------------------------
  console.log('\n=== 2. populateDevicePickers: audio-only call ===');
  {
    const sb = buildSandbox({ cameras, mics: [...mics, { kind: 'audioinput', deviceId: 'mic-2', label: 'Headset Mic' }], getUserMediaImpl: async () => { throw new Error('unused'); } });
    sb.pc = {};
    sb.localStream = fakeStream([fakeTrack('audio', 'mic-1')]);
    sb.isVideoOn = false; // audio-only call
    sb.currentMicDeviceId = 'mic-1';

    await vm.runInContext('populateDevicePickers()', sb);
    await new Promise(r => setTimeout(r, 0));

    check('camera dropdown hidden on an audio-only call even with 2 cameras available', sb.__els['camera-select'].style.display === 'none');
    check('mic dropdown still shown (2 mics available)', sb.__els['mic-select'].style.display !== 'none');
    check('device-picker container still shown (mic dropdown visible)', sb.__els['device-picker'].style.display === 'flex');
  }

  // ---------------------------------------------------------------
  // 3. populateDevicePickers: no-op guard when call already ended.
  // ---------------------------------------------------------------
  console.log('\n=== 3. populateDevicePickers: stale call guard ===');
  {
    const sb = buildSandbox({ cameras, mics, getUserMediaImpl: async () => { throw new Error('unused'); } });
    sb.pc = null; // call already torn down
    sb.localStream = null;
    await vm.runInContext('populateDevicePickers()', sb);
    await new Promise(r => setTimeout(r, 0));
    check('populateDevicePickers no-ops when pc/localStream are null (call already ended)', sb.__els['camera-select']._options.length === 0);
  }

  // ---------------------------------------------------------------
  // 4. switchCameraDevice: no-op when picking the already-active device.
  // ---------------------------------------------------------------
  console.log('\n=== 4. switchCameraDevice: same-device no-op ===');
  {
    let gumCalls = 0;
    const sb = buildSandbox({ cameras, mics, getUserMediaImpl: async () => { gumCalls++; return fakeStream([fakeTrack('video', 'cam-1')]); } });
    const oldTrack = fakeTrack('video', 'cam-1');
    const sender = { track: oldTrack, replaceTrack: async () => {} };
    sb.pc = { getSenders: () => [sender] };
    sb.localStream = fakeStream([oldTrack]);
    sb.currentCameraDeviceId = 'cam-1';

    await vm.runInContext("switchCameraDevice('cam-1')", sb);
    await new Promise(r => setTimeout(r, 0));
    check('switchCameraDevice does not call getUserMedia when the target device is already active', gumCalls === 0);
  }

  // ---------------------------------------------------------------
  // 5. switchCameraDevice: real switch replaces the track and updates state.
  // ---------------------------------------------------------------
  console.log('\n=== 5. switchCameraDevice: real switch ===');
  {
    const newTrack = fakeTrack('video', 'cam-2');
    let replaceTrackCalledWith = null;
    const oldTrack = fakeTrack('video', 'cam-1');
    const sender = { track: oldTrack, replaceTrack: async (t) => { replaceTrackCalledWith = t; } };
    const sb = buildSandbox({ cameras, mics, getUserMediaImpl: async (constraints) => {
      check('switchCameraDevice requests getUserMedia with an exact deviceId video constraint', constraints.video.deviceId.exact === 'cam-2');
      return fakeStream([newTrack]);
    } });
    sb.pc = { getSenders: () => [sender] };
    sb.localStream = fakeStream([oldTrack]);
    sb.currentCameraDeviceId = 'cam-1';

    await vm.runInContext("switchCameraDevice('cam-2')", sb);
    await new Promise(r => setTimeout(r, 0));

    check('switchCameraDevice calls sender.replaceTrack with the new track', replaceTrackCalledWith === newTrack);
    check('switchCameraDevice stops the old track', oldTrack.stopped === true);
    check('switchCameraDevice adds the new track to localStream', sb.localStream.getVideoTracks().includes(newTrack));
    check('switchCameraDevice updates currentCameraDeviceId to the new device', sb.currentCameraDeviceId === 'cam-2');
  }

  // ---------------------------------------------------------------
  // 6. switchMicDevice: preserves the current mute state on the new track.
  // ---------------------------------------------------------------
  console.log('\n=== 6. switchMicDevice: preserves mute state ===');
  {
    const newTrack = fakeTrack('audio', 'mic-2');
    const oldTrack = fakeTrack('audio', 'mic-1');
    const sender = { track: oldTrack, replaceTrack: async () => {} };
    const sb = buildSandbox({ cameras, mics, getUserMediaImpl: async () => fakeStream([newTrack]) });
    sb.pc = { getSenders: () => [sender] };
    sb.localStream = fakeStream([oldTrack]);
    sb.currentMicDeviceId = 'mic-1';
    sb.isMuted = true; // caller was muted before switching mics

    await vm.runInContext("switchMicDevice('mic-2')", sb);
    await new Promise(r => setTimeout(r, 0));

    check('switchMicDevice carries the muted state onto the newly-selected mic track', newTrack.enabled === false);
    check('switchMicDevice updates currentMicDeviceId', sb.currentMicDeviceId === 'mic-2');
  }

  // ---------------------------------------------------------------
  // 7. switchCameraDevice: getUserMedia failure is mapped and surfaced,
  //    not left as an unhandled rejection (same discipline as epic §8).
  // ---------------------------------------------------------------
  console.log('\n=== 7. switchCameraDevice: error path ===');
  {
    const oldTrack = fakeTrack('video', 'cam-1');
    const sender = { track: oldTrack, replaceTrack: async () => { throw new Error('should not be reached'); } };
    const sb = buildSandbox({
      cameras, mics,
      getUserMediaImpl: async () => { const e = new Error('in use'); e.name = 'NotReadableError'; throw e; },
    });
    sb.pc = { getSenders: () => [sender] };
    sb.localStream = fakeStream([oldTrack]);
    sb.currentCameraDeviceId = 'cam-1';
    sb.isVideoOn = true;

    await vm.runInContext("switchCameraDevice('cam-2')", sb);
    await new Promise(r => setTimeout(r, 0));
    await new Promise(r => setTimeout(r, 0)); // populateDevicePickers() re-run inside the catch is also async

    check('switchCameraDevice does not throw an unhandled rejection on getUserMedia failure', true);
    check('switchCameraDevice surfaces a friendly alert mentioning the mapped error message', sb.__alerts.some(a => a.includes('Could not switch camera') && a.length > 'Could not switch camera: '.length));
    check('the old (still-working) camera track was left alone, not stopped, on failure', oldTrack.stopped === false);
    check('currentCameraDeviceId is unchanged on failure (still cam-1, not silently advanced to cam-2)', sb.currentCameraDeviceId === 'cam-1');
  }

  const speakers = [
    { kind: 'audiooutput', deviceId: 'spk-1', label: 'Built-in Speakers' },
    { kind: 'audiooutput', deviceId: 'spk-2', label: 'USB Headset' },
  ];

  // ---------------------------------------------------------------
  // 8. populateDevicePickers: speaker dropdown shown when supported + choice exists.
  // ---------------------------------------------------------------
  console.log('\n=== 8. populateDevicePickers: speaker picker shown ===');
  {
    const sb = buildSandbox({ cameras, mics, speakers, getUserMediaImpl: async () => { throw new Error('unused'); } });
    sb.pc = {};
    sb.localStream = fakeStream([fakeTrack('audio', 'mic-1')]);
    sb.currentSpeakerDeviceId = 'spk-1';

    await vm.runInContext('populateDevicePickers()', sb);
    await new Promise(r => setTimeout(r, 0));

    const speakerSelect = sb.__els['speaker-select'];
    check('speaker select gets 2 options (2 speakers enumerated)', speakerSelect._options.length === 2);
    check('speaker option labels come from device.label', speakerSelect._options.map(o => o.textContent).includes('USB Headset'));
    check('speaker dropdown shown (2 speakers, browser supports setSinkId)', speakerSelect.style.display !== 'none');
    check('device-picker container shown (speaker dropdown visible, even though this call has no video)', sb.__els['device-picker'].style.display === 'flex');
  }

  // ---------------------------------------------------------------
  // 9. populateDevicePickers: speaker dropdown hidden when the browser
  //    doesn't support setSinkId at all (Firefox/Safari as of writing).
  // ---------------------------------------------------------------
  console.log('\n=== 9. populateDevicePickers: unsupported browser ===');
  {
    const sb = buildSandbox({ cameras, mics, speakers, speakerSupported: false, getUserMediaImpl: async () => { throw new Error('unused'); } });
    sb.pc = {};
    sb.localStream = fakeStream([fakeTrack('audio', 'mic-1')]);

    await vm.runInContext('populateDevicePickers()', sb);
    await new Promise(r => setTimeout(r, 0));

    const speakerSelect = sb.__els['speaker-select'];
    check('speaker dropdown stays hidden when SPEAKER_OUTPUT_SUPPORTED is false, even with 2 speakers available', speakerSelect.style.display === 'none');
    check('speaker options are not even built on an unsupported browser (no wasted enumeration work)', speakerSelect._options.length === 0);
  }

  // ---------------------------------------------------------------
  // 10. switchSpeakerDevice: calls setSinkId on the remote-video element,
  //     not getUserMedia/replaceTrack (no track/negotiation involved).
  // ---------------------------------------------------------------
  console.log('\n=== 10. switchSpeakerDevice: real switch ===');
  {
    let sinkIdCalledWith = null;
    let gumCalls = 0;
    const sb = buildSandbox({
      cameras, mics, speakers,
      getUserMediaImpl: async () => { gumCalls++; throw new Error('switchSpeakerDevice must never call getUserMedia'); },
      setSinkIdImpl: async (id) => { sinkIdCalledWith = id; },
    });
    sb.currentSpeakerDeviceId = 'spk-1';

    await vm.runInContext("switchSpeakerDevice('spk-2')", sb);
    await new Promise(r => setTimeout(r, 0));

    check('switchSpeakerDevice calls remote-video.setSinkId with the new device id', sinkIdCalledWith === 'spk-2');
    check('switchSpeakerDevice never touches getUserMedia (no track/renegotiation needed for output routing)', gumCalls === 0);
    check('switchSpeakerDevice updates currentSpeakerDeviceId', sb.currentSpeakerDeviceId === 'spk-2');
  }

  // ---------------------------------------------------------------
  // 11. switchSpeakerDevice: same-device no-op, unsupported-browser no-op,
  //     and a setSinkId failure is surfaced without throwing.
  // ---------------------------------------------------------------
  console.log('\n=== 11. switchSpeakerDevice: guards + error path ===');
  {
    let calls = 0;
    const sb = buildSandbox({ cameras, mics, speakers, getUserMediaImpl: async () => { throw new Error('unused'); }, setSinkIdImpl: async () => { calls++; } });
    sb.currentSpeakerDeviceId = 'spk-1';
    await vm.runInContext("switchSpeakerDevice('spk-1')", sb);
    await new Promise(r => setTimeout(r, 0));
    check('switchSpeakerDevice no-ops when the target speaker is already active', calls === 0);

    const sbUnsupported = buildSandbox({ cameras, mics, speakers, speakerSupported: false, getUserMediaImpl: async () => { throw new Error('unused'); }, setSinkIdImpl: async () => { calls++; } });
    await vm.runInContext("switchSpeakerDevice('spk-2')", sbUnsupported);
    await new Promise(r => setTimeout(r, 0));
    check('switchSpeakerDevice no-ops on a browser without setSinkId support, even if called directly', calls === 0);

    const sbFail = buildSandbox({
      cameras, mics, speakers,
      getUserMediaImpl: async () => { throw new Error('unused'); },
      setSinkIdImpl: async () => { const e = new Error('no such device'); e.name = 'NotFoundError'; throw e; },
    });
    sbFail.currentSpeakerDeviceId = 'spk-1';
    await vm.runInContext("switchSpeakerDevice('spk-2')", sbFail);
    await new Promise(r => setTimeout(r, 0));
    check('a setSinkId failure does not throw an unhandled rejection', true);
    check('a setSinkId failure surfaces a friendly alert', sbFail.__alerts.some(a => a.includes('Could not switch speaker')));
    check('currentSpeakerDeviceId is unchanged on a setSinkId failure', sbFail.currentSpeakerDeviceId === 'spk-1');
  }

  console.log('\n' + '='.repeat(60));
  console.log(`RESULT: ${results.pass.length} passed, ${results.fail.length} failed`);
  if (results.fail.length) console.log('Failed:', results.fail);
  console.log('='.repeat(60));
  process.exit(results.fail.length ? 1 : 0);
}

main();
