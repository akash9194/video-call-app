// Epic §23's "graduated video-quality adaptation" -- acting on the
// network-quality indicator (network-quality.js), not just displaying
// it. Pure, dependency-free mapping from a quality bucket to the WebRTC
// encoding constraints applied to our OUTBOUND video track, mirrored
// byte-for-byte in mobile/src/services/videoQualityAdaptation.ts (same
// reasoning as network-quality.js/networkQuality.ts) and asserted to
// match in scripts/test_network_quality.js.
//
// Direction matters here: this reacts to what the PEER reports about
// THEIR inbound experience (the call:network-quality message we
// receive from them), not our own -- see index.html's handler for where
// this gets applied to our RTCRtpSender.
function getVideoEncodingConstraints(quality) {
  switch (quality) {
    case 'poor':
      return { maxBitrate: 250000, scaleResolutionDownBy: 2 };
    case 'fair':
      return { maxBitrate: 800000, scaleResolutionDownBy: 1.5 };
    case 'good':
    case 'unknown':
    default:
      // 'unknown' intentionally gets full quality -- not enough data yet
      // to justify degrading the call.
      return { maxBitrate: null, scaleResolutionDownBy: null };
  }
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = { getVideoEncodingConstraints };
}
