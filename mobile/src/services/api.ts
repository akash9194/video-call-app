import AsyncStorage from '@react-native-async-storage/async-storage';
import { API_BASE_URL } from '../config';
import { User, IceServer, Appointment } from '../types';

const TOKEN_KEY = 'auth_token';

async function request(path: string, options: RequestInit = {}) {
  const token = await AsyncStorage.getItem(TOKEN_KEY);
  const res = await fetch(`${API_BASE_URL}${path}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(options.headers || {}),
    },
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `Request failed: ${res.status}`);
  }
  return res.json();
}

export const api = {
  async signup(name: string, email: string, password: string, role: 'doctor' | 'patient') {
    const data = await request('/auth/signup', {
      method: 'POST',
      body: JSON.stringify({ name, email, password, role }),
    });
    await AsyncStorage.setItem(TOKEN_KEY, data.access_token);
    return data as { access_token: string; user: User };
  },

  async login(email: string, password: string) {
    const data = await request('/auth/login', {
      method: 'POST',
      body: JSON.stringify({ email, password }),
    });
    await AsyncStorage.setItem(TOKEN_KEY, data.access_token);
    return data as { access_token: string; user: User };
  },

  async logout() {
    await AsyncStorage.removeItem(TOKEN_KEY);
  },

  async getToken() {
    return AsyncStorage.getItem(TOKEN_KEY);
  },

  async me() {
    return request('/users/me') as Promise<User>;
  },

  async listUsers() {
    return request('/users') as Promise<User[]>;
  },

  async getIceServers() {
    const data = await request('/calls/ice-servers');
    return data.ice_servers as IceServer[];
  },

  async callHistory() {
    return request('/calls/history');
  },

  // Doctor-only initiation requires an active appointment with the
  // patient before call:invite is allowed -- see backend/app/signaling/
  // ws_manager.py. These are the mobile equivalents of the web test
  // client's Schedule flow.
  async createAppointment(patientId: string, scheduledTimeIso: string, notes?: string) {
    return request('/appointments', {
      method: 'POST',
      body: JSON.stringify({ patient_id: patientId, scheduled_time: scheduledTimeIso, notes: notes || null }),
    }) as Promise<Appointment>;
  },

  async listAppointments() {
    return request('/appointments') as Promise<Appointment[]>;
  },
};
