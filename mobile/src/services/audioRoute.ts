// Epic §22 Bluetooth audio-route toggle -- the one piece of
// toggleBluetoothRoute (CallContext.tsx) that's pure decision logic
// rather than a native-module call, pulled out so it can actually be
// unit-tested (see scripts/test_audio_route.js). Everything else about
// this feature -- whether chooseAudioRoute succeeds, whether a Bluetooth
// device is even connected -- can only be observed on real Android
// hardware with a paired headset, which this sandbox doesn't have; this
// function is deliberately the boundary between "logic we can verify
// here" and "behavior that needs a real device."
export type AudioRoute = 'BLUETOOTH' | 'SPEAKER_PHONE' | 'EARPIECE';

/**
 * What InCallManager.chooseAudioRoute() should be called with when the
 * user toggles Bluetooth routing on or off. Turning it ON always routes
 * to BLUETOOTH. Turning it OFF routes back to whatever the independent
 * speaker/earpiece toggle currently reflects, rather than a hardcoded
 * choice -- Bluetooth is an override on top of that existing state, not
 * a third state that has to be reasoned about on its own.
 */
export function resolveAudioRoute(turningBluetoothOn: boolean, isSpeakerOn: boolean): AudioRoute {
  if (turningBluetoothOn) return 'BLUETOOTH';
  return isSpeakerOn ? 'SPEAKER_PHONE' : 'EARPIECE';
}
