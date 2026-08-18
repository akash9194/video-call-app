import React, { useEffect, useState, useCallback } from 'react';
import { View, Text, FlatList, TouchableOpacity, StyleSheet, RefreshControl } from 'react-native';
import { useAuth } from '../context/AuthContext';
import { useCall } from '../context/CallContext';
import { api } from '../services/api';
import { User, Appointment } from '../types';
import { NavProp } from '../navigation';

export default function HomeScreen({ navigation }: { navigation: NavProp }) {
  const { user, logout } = useAuth();
  const { startCall } = useCall();
  const [users, setUsers] = useState<User[]>([]);
  const [appointments, setAppointments] = useState<Appointment[]>([]);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    setRefreshing(true);
    try {
      const [u, a] = await Promise.all([api.listUsers(), api.listAppointments()]);
      setUsers(u);
      setAppointments(a);
    } finally {
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  // Doctor-only initiation: a patient never gets call buttons, regardless
  // of who they're looking at. This mirrors the server-side check in
  // ws_manager.py -- the UI restriction is just for a clean experience,
  // the real enforcement lives on the backend since a client can't be
  // trusted to police itself.
  const canInitiateCalls = user?.role === 'doctor';

  return (
    <View style={styles.container}>
      <View style={styles.header}>
        <Text style={styles.title}>Hi, {user?.name}</Text>
        <TouchableOpacity onPress={logout}>
          <Text style={styles.logout}>Log out</Text>
        </TouchableOpacity>
      </View>

      {!canInitiateCalls && (
        <Text style={styles.patientHint}>
          Your doctor will call you when it's time for your appointment -- you can't start a call yourself.
        </Text>
      )}

      <FlatList
        data={users}
        keyExtractor={(u) => u.id}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={load} />}
        renderItem={({ item }) => {
          // Doctors can only call patients (mirrors the backend check).
          const canCallThisUser = canInitiateCalls && item.role === 'patient';
          return (
            <View style={styles.row}>
              <View>
                <Text style={styles.name}>{item.name}</Text>
                <Text style={styles.status}>{item.is_online ? 'Online' : 'Offline'} · {item.role}</Text>
              </View>
              {canCallThisUser && (
                <View style={{ flexDirection: 'row', gap: 8 }}>
                  <TouchableOpacity
                    style={styles.scheduleButton}
                    onPress={() => navigation.navigate('Schedule', { patientId: item.id, patientName: item.name })}
                  >
                    <Text style={styles.scheduleButtonText}>Schedule</Text>
                  </TouchableOpacity>
                  <TouchableOpacity
                    style={[styles.callButton, styles.voiceButton, !item.is_online && styles.callButtonDisabled]}
                    disabled={!item.is_online}
                    onPress={() => startCall(item.id, item.name, 'audio')}
                  >
                    <Text style={styles.callButtonText}>Voice</Text>
                  </TouchableOpacity>
                  <TouchableOpacity
                    style={[styles.callButton, !item.is_online && styles.callButtonDisabled]}
                    disabled={!item.is_online}
                    onPress={() => startCall(item.id, item.name, 'video')}
                  >
                    <Text style={styles.callButtonText}>Video</Text>
                  </TouchableOpacity>
                </View>
              )}
            </View>
          );
        }}
        ListHeaderComponent={
          appointments.length > 0 ? (
            <View style={styles.apptSection}>
              <Text style={styles.apptHeading}>Your appointments</Text>
              {appointments.map((a) => (
                <View key={a.id} style={styles.apptRow}>
                  <Text style={styles.apptWith}>{user?.role === 'doctor' ? a.patient_name : a.doctor_name}</Text>
                  <Text style={styles.apptWhen}>{new Date(a.scheduled_time).toLocaleString()}</Text>
                  <Text style={[styles.apptStatus, a.status === 'scheduled' ? styles.apptStatusActive : styles.apptStatusInactive]}>
                    {a.status}
                  </Text>
                </View>
              ))}
            </View>
          ) : null
        }
        ListEmptyComponent={<Text style={styles.empty}>No other users yet.</Text>}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#0f172a', padding: 16 },
  header: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 },
  title: { fontSize: 22, fontWeight: '700', color: 'white' },
  logout: { color: '#93c5fd' },
  patientHint: { color: '#94a3b8', fontSize: 13, marginBottom: 12 },
  row: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    backgroundColor: '#1e293b',
    padding: 14,
    borderRadius: 10,
    marginBottom: 10,
  },
  name: { color: 'white', fontSize: 16, fontWeight: '600' },
  status: { color: '#94a3b8', marginTop: 2 },
  callButton: { backgroundColor: '#16a34a', paddingHorizontal: 14, paddingVertical: 8, borderRadius: 8 },
  voiceButton: { backgroundColor: '#2563eb' },
  callButtonDisabled: { backgroundColor: '#334155' },
  callButtonText: { color: 'white', fontWeight: '600' },
  scheduleButton: { backgroundColor: '#334155', paddingHorizontal: 10, paddingVertical: 8, borderRadius: 8 },
  scheduleButtonText: { color: 'white', fontSize: 12, fontWeight: '600' },
  empty: { color: '#94a3b8', textAlign: 'center', marginTop: 40 },
  apptSection: { marginBottom: 16 },
  apptHeading: { color: '#94a3b8', fontSize: 13, marginBottom: 8 },
  apptRow: {
    backgroundColor: '#1e293b',
    padding: 10,
    borderRadius: 8,
    marginBottom: 6,
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
  },
  apptWith: { color: 'white', fontSize: 13, fontWeight: '600', flex: 1 },
  apptWhen: { color: '#94a3b8', fontSize: 11, flex: 1 },
  apptStatus: { fontSize: 11, fontWeight: '600' },
  apptStatusActive: { color: '#22c55e' },
  apptStatusInactive: { color: '#94a3b8' },
});
