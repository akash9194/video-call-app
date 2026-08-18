import React, { useState } from 'react';
import { View, Text, TouchableOpacity, StyleSheet } from 'react-native';
import { useCall } from '../context/CallContext';
import { RootStackParamList } from '../navigation';

type Props = {
  route: { params: RootStackParamList['IncomingCall'] };
};

export default function IncomingCallScreen({ route }: Props) {
  const { remoteUserName, acceptCall, rejectCall } = useCall();
  const isVideoCall = route.params?.media !== 'audio';
  const [consent, setConsent] = useState(false);

  return (
    <View style={styles.container}>
      <View style={styles.avatar}>
        <Text style={styles.avatarText}>{remoteUserName?.[0]?.toUpperCase() ?? '?'}</Text>
      </View>
      <Text style={styles.name}>{remoteUserName}</Text>
      <Text style={styles.subtitle}>Incoming {isVideoCall ? 'video' : 'voice'} call…</Text>

      <TouchableOpacity style={styles.consentRow} onPress={() => setConsent((c) => !c)}>
        <View style={[styles.checkbox, consent && styles.checkboxChecked]}>
          {consent && <Text style={styles.checkmark}>✓</Text>}
        </View>
        <Text style={styles.consentText}>
          I consent to this telehealth video/voice consultation with my doctor.
        </Text>
      </TouchableOpacity>

      <View style={styles.actions}>
        <TouchableOpacity style={[styles.button, styles.reject]} onPress={rejectCall}>
          <Text style={styles.buttonText}>Decline</Text>
        </TouchableOpacity>
        <TouchableOpacity
          style={[styles.button, styles.accept, !consent && styles.acceptDisabled]}
          disabled={!consent}
          onPress={() => acceptCall(consent)}
        >
          <Text style={styles.buttonText}>Accept</Text>
        </TouchableOpacity>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#0f172a', alignItems: 'center', justifyContent: 'center' },
  avatar: {
    width: 96,
    height: 96,
    borderRadius: 48,
    backgroundColor: '#2563eb',
    alignItems: 'center',
    justifyContent: 'center',
    marginBottom: 16,
  },
  avatarText: { color: 'white', fontSize: 36, fontWeight: '700' },
  name: { color: 'white', fontSize: 24, fontWeight: '700' },
  subtitle: { color: '#94a3b8', marginTop: 8, marginBottom: 24 },
  consentRow: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: 10,
    maxWidth: 280,
    marginBottom: 32,
  },
  checkbox: {
    width: 20,
    height: 20,
    borderRadius: 4,
    borderWidth: 2,
    borderColor: '#64748b',
    alignItems: 'center',
    justifyContent: 'center',
    marginTop: 1,
  },
  checkboxChecked: { backgroundColor: '#16a34a', borderColor: '#16a34a' },
  checkmark: { color: 'white', fontSize: 13, fontWeight: '700' },
  consentText: { color: '#cbd5e1', fontSize: 12, flex: 1 },
  actions: { flexDirection: 'row', gap: 24 },
  button: { width: 100, paddingVertical: 14, borderRadius: 30, alignItems: 'center' },
  reject: { backgroundColor: '#dc2626' },
  accept: { backgroundColor: '#16a34a' },
  acceptDisabled: { backgroundColor: '#334155' },
  buttonText: { color: 'white', fontWeight: '600' },
});
