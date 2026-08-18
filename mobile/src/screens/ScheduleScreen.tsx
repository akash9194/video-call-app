import React, { useState } from 'react';
import { View, Text, TextInput, TouchableOpacity, StyleSheet, Alert } from 'react-native';
import { api } from '../services/api';
import { NavProp } from '../navigation';
import { RouteProp, useRoute } from '@react-navigation/native';
import { RootStackParamList } from '../navigation';

// Doctor-only initiation means a call can't be placed until an
// appointment exists between the doctor and that specific patient (see
// ws_manager.py's call:invite handler). This screen is how a doctor
// creates one, entirely from the app -- no separate tool needed.
//
// Plain text date/time fields rather than a native date picker
// component on purpose: adding one means a new native dependency that
// needs linking and a rebuild, which isn't worth it for what is, for
// now, a testing/demo screen rather than the final scheduling UX.
export default function ScheduleScreen({ navigation }: { navigation: NavProp }) {
  const route = useRoute<RouteProp<RootStackParamList, 'Schedule'>>();
  const { patientId, patientName } = route.params;

  const today = new Date();
  const defaultDate = today.toISOString().slice(0, 10); // YYYY-MM-DD
  const [date, setDate] = useState(defaultDate);
  const [time, setTime] = useState('10:00');
  const [notes, setNotes] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const onSubmit = async () => {
    const parsed = new Date(`${date}T${time}:00`);
    if (isNaN(parsed.getTime())) {
      Alert.alert('Invalid date/time', 'Use YYYY-MM-DD for the date and HH:MM (24-hour) for the time.');
      return;
    }
    setSubmitting(true);
    try {
      await api.createAppointment(patientId, parsed.toISOString(), notes || undefined);
      Alert.alert('Appointment scheduled', `With ${patientName} on ${parsed.toLocaleString()}`, [
        { text: 'OK', onPress: () => navigation.navigate('Home') },
      ]);
    } catch (e: any) {
      Alert.alert('Could not schedule appointment', e.message);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <View style={styles.container}>
      <Text style={styles.title}>Schedule appointment</Text>
      <Text style={styles.subtitle}>with {patientName}</Text>

      <Text style={styles.label}>Date (YYYY-MM-DD)</Text>
      <TextInput style={styles.input} value={date} onChangeText={setDate} placeholder="2026-08-25" placeholderTextColor="#64748b" />

      <Text style={styles.label}>Time, 24-hour (HH:MM)</Text>
      <TextInput style={styles.input} value={time} onChangeText={setTime} placeholder="14:30" placeholderTextColor="#64748b" />

      <Text style={styles.label}>Notes (optional)</Text>
      <TextInput style={styles.input} value={notes} onChangeText={setNotes} placeholder="Reason for visit" placeholderTextColor="#64748b" />

      <TouchableOpacity style={styles.button} onPress={onSubmit} disabled={submitting}>
        <Text style={styles.buttonText}>{submitting ? 'Scheduling…' : 'Create appointment'}</Text>
      </TouchableOpacity>
      <TouchableOpacity onPress={() => navigation.goBack()}>
        <Text style={styles.link}>Cancel</Text>
      </TouchableOpacity>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, justifyContent: 'center', padding: 24, backgroundColor: '#0f172a' },
  title: { fontSize: 26, fontWeight: '700', color: 'white', marginBottom: 4 },
  subtitle: { fontSize: 14, color: '#94a3b8', marginBottom: 24 },
  label: { color: '#94a3b8', fontSize: 12, marginBottom: 6 },
  input: {
    backgroundColor: '#1e293b',
    color: 'white',
    borderRadius: 8,
    padding: 14,
    marginBottom: 16,
  },
  button: { backgroundColor: '#2563eb', borderRadius: 8, padding: 14, alignItems: 'center', marginTop: 8 },
  buttonText: { color: 'white', fontWeight: '600', fontSize: 16 },
  link: { color: '#93c5fd', textAlign: 'center', marginTop: 16 },
});
