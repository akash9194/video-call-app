// Epic §8's "camera/mic pre-check" -- turns a raw getUserMedia rejection
// into a message a patient/doctor can actually act on, instead of a
// generic "Something went wrong" or (worse) a silently frozen
// "Connecting..." screen. Mirrored byte-for-byte in web-test-client/
// media-errors.js and asserted to match in scripts/test_media_errors.js,
// same cross-client pattern as networkQuality.ts/network-quality.js.
//
// Before this, a getUserMedia failure (permission denied, no camera, mic
// already in use by another app) was an unhandled promise rejection in
// both clients -- the caller/callee got stuck on "Connecting..." forever,
// AND the other side never learned the call wasn't going to happen (see
// CallContext.tsx's acceptCall and the call:accepted handler for where
// this is now caught and turned into a clean call:reject/call:end
// instead of leaving the peer hanging).
export interface MediaErrorInfo {
  code: string;
  message: string;
}

export function mapMediaError(error: unknown): MediaErrorInfo {
  const name = (error as { name?: string } | null)?.name ?? '';
  switch (name) {
    case 'NotAllowedError':
    case 'PermissionDeniedError':
      return {
        code: 'permission_denied',
        message: 'Camera/microphone access was denied. Please allow access in your device settings and try again.',
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
        message: 'Camera/microphone access requires a secure connection.',
      };
    default:
      return {
        code: 'unknown',
        message: "Couldn't access your camera or microphone. Please check your device and try again.",
      };
  }
}
