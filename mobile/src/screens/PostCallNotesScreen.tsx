import React, { useState } from 'react';
import { View, Text, TextInput, TouchableOpacity, StyleSheet, Alert, ActivityIndicator, ScrollView, Switch } from 'react-native';
import { api } from '../services/api';
import { RootStackParamList, navigate } from '../navigation';
import { CALL_OUTCOMES } from '../types';

type Props = {
  route: { params: RootStackParamList['PostCallNotes'] };
};

// Epic §30: shown right after a call that actually connected ends
// (routing decided in CallContext's finishCall -- doctor side only, see
// that function's comment for why). Backend already enforces everything
// that matters here (participant-only, call must be terminal -- see
// routers/calls.py's PATCH /calls/{call_id}/notes) -- this screen is just
// the missing UI on top of an endpoint that's existed and been verified
// against the real backend since earlier this project (see
// scripts/verify_epic_batch2.py).
export default function PostCallNotesScreen({ route }: Props) {
  const { callId, remoteUserName } = route.params;
  const [notes, setNotes] = useState('');
  const [outcome, setOutcome] = useState<string | null>(null);
  const [followUpRequired, setFollowUpRequired] = useState(false);
  const [saving, setSaving] = useState(false);

  const skip = () => navigate('Home');

  const save = async () => {
    setSaving(true);
    try {
      await api.addCallNotes(callId, {
        notes: notes.trim() || null,
        outcome,
        follow_up_required: followUpRequired,
      });
      navigate('Home');
    } catch (e: any) {
      // The two realistic failures here are both benign from the user's
      // point of view: the call somehow isn't terminal yet (409 -- racy,
      // shouldn't normally happen since this screen only shows up after
      // the call already ended) or the network request itself failed.
      // Either way, let them retry rather than silently discarding what
      // they typed.
      Alert.alert('Could not save notes', e?.message || 'Something went wrong. You can try again.');
    } finally {
      setSaving(false);
    }
  };

  return (
    <ScrollView style={styles.container} contentContainerStyle={styles.content}>
      <Text style={styles.title}>Call with {remoteUserName || 'patient'} ended</Text>
      <Text style={styles.subtitle}>Add an outcome and any notes for the record (optional).</Text>

      <Text style={styles.label}>Outcome</Text>
      <View style={styles.outcomeGrid}>
        {CALL_OUTCOMES.map((o) => {
          const selected = outcome === o.value;
          return (
            <TouchableOpacity
              key={o.value}
              style={[styles.outcomeChip, selected && styles.outcomeChipSelected]}
              onPress={() => setOutcome(selected ? null : o.value)}
            >
              <Text style={[styles.outcomeChipText, selected && styles.outcomeChipTextSelected]}>{o.label}</Text>
            </TouchableOpacity>
          );
        })}
      </View>

      <Text style={styles.label}>Notes</Text>
      <TextInput
        style={styles.notesInput}
        value={notes}
        onChangeText={setNotes}
        placeholder="What was discussed, next steps, anything worth recording..."
        placeholderTextColor="#64748b"
        multiline
        numberOfLines={5}
        textAlignVertical="top"
      />

      <View style={styles.followUpRow}>
        <Text style={styles.followUpLabel}>Follow-up required</Text>
        <Switch
          value={followUpRequired}
          onValueChange={setFollowUpRequired}
          trackColor={{ false: '#334155', true: '#16a34a' }}
          thumbColor="white"
        />
      </View>

      <View style={styles.actions}>
        <TouchableOpacity style={[styles.button, styles.skipButton]} onPress={skip} disabled={saving}>
          <Text style={styles.buttonText}>Skip</Text>
        </TouchableOpacity>
        <TouchableOpacity style={[styles.button, styles.saveButton]} onPress={save} disabled={saving}>
          {saving ? <ActivityIndicator color="white" /> : <Text style={styles.buttonText}>Save</Text>}
        </TouchableOpacity>
      </View>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#0f172a' },
  content: { padding: 20, paddingBottom: 40 },
  title: { color: 'white', fontSize: 20, fontWeight: '700' },
  subtitle: { color: '#94a3b8', marginTop: 6, marginBottom: 24, fontSize: 13 },
  label: { color: '#cbd5e1', fontSize: 13, fontWeight: '600', marginBottom: 10 },
  outcomeGrid: { flexDirection: 'row', flexWrap: 'wrap', gap: 8, marginBottom: 24 },
  outcomeChip: {
    paddingHorizontal: 14,
    paddingVertical: 10,
    borderRadius: 20,
    backgroundColor: '#1e293b',
    borderWidth: 1,
    borderColor: '#334155',
  },
  outcomeChipSelected: { backgroundColor: '#2563eb', borderColor: '#2563eb' },
  outcomeChipText: { color: '#cbd5e1', fontSize: 13, fontWeight: '600' },
  outcomeChipTextSelected: { color: 'white' },
  notesInput: {
    backgroundColor: '#1e293b',
    borderRadius: 10,
    borderWidth: 1,
    borderColor: '#334155',
    color: 'white',
    padding: 12,
    minHeight: 110,
    fontSize: 14,
    marginBottom: 24,
  },
  followUpRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    backgroundColor: '#1e293b',
    borderRadius: 10,
    padding: 14,
    marginBottom: 32,
  },
  followUpLabel: { color: 'white', fontSize: 14, fontWeight: '600' },
  actions: { flexDirection: 'row', gap: 12 },
  button: { flex: 1, paddingVertical: 14, borderRadius: 10, alignItems: 'center' },
  skipButton: { backgroundColor: '#334155' },
  saveButton: { backgroundColor: '#16a34a' },
  buttonText: { color: 'white', fontWeight: '700' },
});
