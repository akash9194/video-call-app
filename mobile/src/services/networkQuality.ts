// Pure, dependency-free network-quality bucketing (epic §23's
// "network-quality indicator"). Mirrors web-test-client/network-quality.js
// exactly (same thresholds, same reasoning) so both clients agree on what
// "poor" means. Kept as a standalone pure function specifically so it's
// unit-testable without React Native / react-native-webrtc -- see
// scripts/test_network_quality.js, which requires this file's compiled
// logic isn't needed; the .js twin is what's actually tested, and this
// file is asserted to match it byte-for-byte in the same test.

export type NetworkQuality = 'good' | 'fair' | 'poor' | 'unknown';

export function bucketNetworkQuality(lossRatio: number): NetworkQuality {
  if (typeof lossRatio !== 'number' || Number.isNaN(lossRatio) || lossRatio < 0) return 'unknown';
  if (lossRatio < 0.02) return 'good';
  if (lossRatio < 0.08) return 'fair';
  return 'poor';
}
