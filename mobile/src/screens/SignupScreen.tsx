import React, { useState } from 'react';
import { View, Text, TextInput, TouchableOpacity, StyleSheet, Alert } from 'react-native';
import { useAuth } from '../context/AuthContext';
import { NavProp } from '../navigation';

export default function SignupScreen({ navigation }: { navigation: NavProp }) {
  const { signup } = useAuth();
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [role, setRole] = useState<'doctor' | 'patient'>('patient');
  const [submitting, setSubmitting] = useState(false);

  const onSubmit = async () => {
    setSubmitting(true);
    try {
      await signup(name, email, password, role);
    } catch (e: any) {
      Alert.alert('Signup failed', e.message);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <View style={styles.container}>
      <Text style={styles.title}>Create account</Text>
      <TextInput style={styles.input} placeholder="Name" value={name} onChangeText={setName} />
      <TextInput
        style={styles.input}
        placeholder="Email"
        autoCapitalize="none"
        keyboardType="email-address"
        value={email}
        onChangeText={setEmail}
      />
      <TextInput
        style={styles.input}
        placeholder="Password"
        secureTextEntry
        value={password}
        onChangeText={setPassword}
      />
      <Text style={styles.roleLabel}>I am a...</Text>
      <View style={styles.roleRow}>
        {(['patient', 'doctor'] as const).map((r) => (
          <TouchableOpacity
            key={r}
            style={[styles.roleButton, role === r && styles.roleButtonActive]}
            onPress={() => setRole(r)}
          >
            <Text style={{ color: 'white' }}>{r === 'doctor' ? 'Doctor' : 'Patient'}</Text>
          </TouchableOpacity>
        ))}
      </View>
      {role === 'patient' && (
        <Text style={styles.hint}>
          As a patient, your doctor will call you -- you won't be able to start calls yourself.
        </Text>
      )}
      <TouchableOpacity style={styles.button} onPress={onSubmit} disabled={submitting}>
        <Text style={styles.buttonText}>{submitting ? 'Creating…' : 'Sign up'}</Text>
      </TouchableOpacity>
      <TouchableOpacity onPress={() => navigation.navigate('Login')}>
        <Text style={styles.link}>Already have an account? Log in</Text>
      </TouchableOpacity>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, justifyContent: 'center', padding: 24, backgroundColor: '#0f172a' },
  title: { fontSize: 28, fontWeight: '700', color: 'white', marginBottom: 24 },
  input: {
    backgroundColor: '#1e293b',
    color: 'white',
    borderRadius: 8,
    padding: 14,
    marginBottom: 12,
  },
  roleLabel: { color: '#94a3b8', marginBottom: 8 },
  roleRow: { flexDirection: 'row', gap: 12, marginBottom: 8 },
  roleButton: { flex: 1, padding: 12, borderRadius: 8, backgroundColor: '#1e293b', alignItems: 'center' },
  roleButtonActive: { backgroundColor: '#2563eb' },
  hint: { color: '#94a3b8', fontSize: 12, marginBottom: 12 },
  button: { backgroundColor: '#2563eb', borderRadius: 8, padding: 14, alignItems: 'center', marginTop: 8 },
  buttonText: { color: 'white', fontWeight: '600', fontSize: 16 },
  link: { color: '#93c5fd', textAlign: 'center', marginTop: 16 },
});
