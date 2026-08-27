// Epic §8's "camera/mic pre-check" -- turns a raw getUserMedia rejection
// into a message a patient/doctor can actually act on. Mirrored
// byte-for-byte in mobile/src/services/mediaErrors.ts and asserted to
// match in scripts/test_media_errors.js. See that file's comment for the
// full rationale (this used to be an unhandled promise rejection in both
// clients).
function mapMediaError(error) {
  const name = (error && error.name) || '';
  switch (name) {
    case 'NotAllowedError':
    case 'PermissionDeniedError':
      return {
        code: 'permission_denied',
        message: 'Camera/microphone access was denied. Please allow access in your browser settings and try again.',
      };
    case 'NotFoundError':
    case 'DevicesNotFoundError':
      return {
        code: 'no_device',
        message: 'No camera or microphone was found on this device.',
      };
    case 'NotReadableError':
    case 'TrackStartError':
      return {
        code: 'device_in_use',
        message: 'Your camera or microphone is already in use by another app.',
      };
    case 'OverconstrainedError':
    case 'ConstraintNotSatisfiedError':
      return {
        code: 'constraints_not_satisfied',
        message: "Your camera doesn't support the requested video settings.",
      };
    case 'SecurityError':
      return {
        code: 'insecure_context',
        message: 'Camera/microphone access requires a secure connection (HTTPS).',
      };
    default:
      return {
        code: 'unknown',
        message: "Couldn't access your camera or microphone. Please check your device and try again.",
      };
  }
}

// Epic §8's "browser-compatibility check" -- feature-detects the two APIs
// this app cannot function without, so an unsupported browser gets a
// clear message up front instead of a confusing failure the first time
// it tries to place or answer a call.
function isWebRTCSupported() {
  return !!(
    typeof window !== 'undefined' &&
    window.RTCPeerConnection &&
    navigator.mediaDevices &&
    typeof navigator.mediaDevices.getUserMedia === 'function'
  );
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = { mapMediaError, isWebRTCSupported };
}
