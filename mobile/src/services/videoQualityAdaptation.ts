// Epic §23's "graduated video-quality adaptation" -- acting on the
// network-quality indicator (networkQuality.ts), not just displaying it.
// Pure, dependency-free mapping from a quality bucket to the WebRTC
// encoding constraints that should be applied to our OUTBOUND video
// track, mirrored byte-for-byte in web-test-client/video-quality-
// adaptation.js (same reasoning as networkQuality.ts/network-quality.js)
// and asserted to match in scripts/test_network_quality.js.
//
// Direction matters here: this reacts to what the PEER reports about
// THEIR inbound experience (the call:network-quality message we receive
// from them), not our own. Throttling what we send in response to their
// reported reception is the correct half of the loop -- see CallContext.
// tsx's call:network-quality handler for where this gets applied.
import { NetworkQuality } from './networkQuality';

export interface VideoEncodingConstraints {
  maxBitrate: number | null; // bits/sec, null = no cap
  scaleResolutionDownBy: number | null; // null = no downscale (1x)
}

// Deliberately conservative steps -- this is a blunt instrument (no
// simulcast, no per-network-condition tuning against real traffic), so
// the goal is "materially less congestion-inducing than uncapped" at
// each tier, not a precisely calibrated bitrate ladder. maxBitrate values
// are the kind of numbers commonly used for a single-layer H.264/VP8
// video call at these resolutions; revisit once this has been observed
// against real calls, not just this build's own synthetic tests.
export function getVideoEncodingConstraints(quality: NetworkQuality): VideoEncodingConstraints {
  switch (quality) {
    case 'poor':
      return { maxBitrate: 250_000, scaleResolutionDownBy: 2 };
    case 'fair':
      return { maxBitrate: 800_000, scaleResolutionDownBy: 1.5 };
    case 'good':
    case 'unknown':
    default:
      // 'unknown' intentionally gets full quality, not a throttled
      // tier -- there's not enough data yet to justify degrading the
      // call, and defaulting to "restricted" on every call's opening
      // seconds (before the first quality sample exists) would be worse
      // than just starting at full quality and reacting once real data
      // arrives.
      return { maxBitrate: null, scaleResolutionDownBy: null };
  }
}
