// Pure, dependency-free network-quality bucketing (epic §23's
// "network-quality indicator"). Kept in its own file specifically so it
// can be unit-tested with plain Node (see scripts/test_network_quality.js)
// without needing a browser or WebRTC stack -- getStats() itself can only
// be exercised for real in a browser, but the bucketing decision it feeds
// is pure arithmetic and should be tested as such.
//
// Thresholds deliberately match the existing audio-only-fallback trigger
// (0.08 sustained loss ratio -- see monitorCallQuality in index.html) so
// "poor" here means exactly "on the edge of triggering fallback if this
// keeps up", not an unrelated second opinion.
function bucketNetworkQuality(lossRatio) {
  if (typeof lossRatio !== 'number' || Number.isNaN(lossRatio) || lossRatio < 0) return 'unknown';
  if (lossRatio < 0.02) return 'good';
  if (lossRatio < 0.08) return 'fair';
  return 'poor';
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = { bucketNetworkQuality };
}
