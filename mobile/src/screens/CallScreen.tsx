import React from 'react';
import { View, Text, TouchableOpacity, StyleSheet } from 'react-native';
import { RTCView } from 'react-native-webrtc';
import { useCall } from '../context/CallContext';

// Epic §23 network-quality indicator styling.
const QUALITY_LABEL: Record<string, string> = { good: 'Good connection', fair: 'Fair connection', poor: 'Poor connection' };
const QUALITY_COLOR: Record<string, { color: string }> = {
  good: { color: '#22c55e' },
  fair: { color: '#eab308' },
  poor: { color: '#f87171' },
};

export default function CallScreen() {
  const {
    status,
    remoteUserName,
    localStream,
    remoteStream,
    isMuted,
    isVideoOn,
    isRemoteVideoOn,
    isSpeakerOn,
    networkQuality,
    endCall,
    toggleMute,
    toggleSpeaker,
    flipCamera,
    switchToVideo,
    switchToVoice,
  } = useCall();

  const showRemoteVideo = remoteStream && isRemoteVideoOn;

  return (
    <View style={styles.container}>
      {showRemoteVideo ? (
        <RTCView streamURL={remoteStream!.toURL()} style={styles.remoteVideo} objectFit="cover" />
      ) : (
        <View style={[styles.remoteVideo, styles.placeholder]}>
          <View style={styles.avatar}>
            <Text style={styles.avatarText}>{remoteUserName?.[0]?.toUpperCase() ?? '?'}</Text>
          </View>
          <Text style={styles.placeholderText}>
            {status === 'connecting' ? 'Connecting…' : isRemoteVideoOn ? 'Waiting for video…' : 'Voice call'}
          </Text>
        </View>
      )}

      {localStream && isVideoOn && (
        <RTCView streamURL={localStream.toURL()} style={styles.localVideo} objectFit="cover" zOrder={1} />
      )}

      <View style={styles.topBar}>
        <Text style={styles.callerName}>{remoteUserName}</Text>
        <Text style={styles.callStatus}>{status === 'active' ? 'Connected' : status}</Text>
        {networkQuality && networkQuality !== 'unknown' && (
          <Text style={[styles.networkQuality, QUALITY_COLOR[networkQuality]]}>
            {QUALITY_LABEL[networkQuality]}
          </Text>
        )}
      </View>

      <View style={styles.controls}>
        <TouchableOpacity style={styles.controlButton} onPress={toggleMute}>
          <Text style={styles.controlIcon}>{isMuted ? 'Unmute' : 'Mute'}</Text>
        </TouchableOpacity>
        <TouchableOpacity style={styles.controlButton} onPress={toggleSpeaker}>
          <Text style={styles.controlIcon}>{isSpeakerOn ? 'Speaker' : 'Earpiece'}</Text>
        </TouchableOpacity>
        <TouchableOpacity style={[styles.controlButton, styles.endCall]} onPress={endCall}>
          <Text style={styles.controlIcon}>End</Text>
        </TouchableOpacity>
        <TouchableOpacity
          style={styles.controlButton}
          // switchToVoice takes an optional `auto` flag (epic §21 -- lets
          // the backend tell a manual tap apart from the automatic
          // poor-connection fallback). onPress hands its handler a
          // GestureResponderEvent as the first argument, which is truthy
          // and would otherwise get passed straight through as `auto`,
          // mislabeling every manual switch as automatic in the audit
          // trail -- wrap it so no arguments reach switchToVoice here.
          onPress={() => (isVideoOn ? switchToVoice() : switchToVideo())}
        >
          <Text style={styles.controlIcon}>{isVideoOn ? 'Switch to voice' : 'Switch to video'}</Text>
        </TouchableOpacity>
        {isVideoOn && (
          <TouchableOpacity style={styles.controlButton} onPress={flipCamera}>
            <Text style={styles.controlIcon}>Flip camera</Text>
          </TouchableOpacity>
        )}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: 'black' },
  remoteVideo: { flex: 1 },
  placeholder: { alignItems: 'center', justifyContent: 'center' },
  avatar: {
    width: 88,
    height: 88,
    borderRadius: 44,
    backgroundColor: '#2563eb',
    alignItems: 'center',
    justifyContent: 'center',
    marginBottom: 12,
  },
  avatarText: { color: 'white', fontSize: 32, fontWeight: '700' },
  placeholderText: { color: '#94a3b8', fontSize: 16 },
  localVideo: {
    position: 'absolute',
    width: 110,
    height: 150,
    top: 50,
    right: 16,
    borderRadius: 12,
    backgroundColor: '#1e293b',
  },
  topBar: { position: 'absolute', top: 50, left: 16 },
  callerName: { color: 'white', fontSize: 20, fontWeight: '700' },
  callStatus: { color: '#94a3b8', marginTop: 4, textTransform: 'capitalize' },
  networkQuality: { marginTop: 2, fontSize: 12, fontWeight: '600' },
  controls: {
    position: 'absolute',
    bottom: 48,
    left: 12,
    right: 12,
    flexDirection: 'row',
    flexWrap: 'wrap',
    justifyContent: 'center',
    gap: 10,
  },
  controlButton: {
    backgroundColor: 'rgba(255,255,255,0.15)',
    paddingHorizontal: 16,
    paddingVertical: 12,
    borderRadius: 30,
  },
  endCall: { backgroundColor: '#dc2626' },
  controlIcon: { color: 'white', fontWeight: '600' },
});
