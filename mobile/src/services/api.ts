import AsyncStorage from '@react-native-async-storage/async-storage';
import { API_BASE_URL } from '../config';
import { User, IceServer } from '../types';

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
  async signup(name: string, email: string, password: string, role: 'client' | 'freelancer') {
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
};
