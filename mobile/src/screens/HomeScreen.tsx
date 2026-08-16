import React, { useEffect, useState, useCallback } from 'react';
import { View, Text, FlatList, TouchableOpacity, StyleSheet, RefreshControl } from 'react-native';
import { useAuth } from '../context/AuthContext';
import { useCall } from '../context/CallContext';
import { api } from '../services/api';
import { User } from '../types';

export default function HomeScreen() {
  const { user, logout } = useAuth();
  const { startCall } = useCall();
  const [users, setUsers] = useState<User[]>([]);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    setRefreshing(true);
    try {
      setUsers(await api.listUsers());
    } finally {
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <View style={styles.container}>
      <View style={styles.header}>
        <Text style={styles.title}>Hi, {user?.name}</Text>
        <TouchableOpacity onPress={logout}>
          <Text style={styles.logout}>Log out</Text>
        </TouchableOpacity>
      </View>

      <FlatList
        data={users}
        keyExtractor={(u) => u.id}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={load} />}
        renderItem={({ item }) => (
          <View style={styles.row}>
            <View>
              <Text style={styles.name}>{item.name}</Text>
              <Text style={styles.status}>{item.is_online ? 'Online' : 'Offline'} · {item.role}</Text>
            </View>
            <View style={{ flexDirection: 'row', gap: 8 }}>
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
          </View>
        )}
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
  empty: { color: '#94a3b8', textAlign: 'center', marginTop: 40 },
});
