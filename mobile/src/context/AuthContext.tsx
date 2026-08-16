import React, { createContext, useContext, useEffect, useState } from 'react';
import { api } from '../services/api';
import { signalingClient } from '../services/signaling';
import { User } from '../types';

interface AuthContextValue {
  user: User | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  signup: (name: string, email: string, password: string, role: 'client' | 'freelancer') => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    (async () => {
      const token = await api.getToken();
      if (token) {
        try {
          const me = await api.me();
          setUser(me);
          signalingClient.connect(token);
        } catch {
          // token invalid/expired
        }
      }
      setLoading(false);
    })();
  }, []);

  const login = async (email: string, password: string) => {
    const data = await api.login(email, password);
    setUser(data.user);
    signalingClient.connect(data.access_token);
  };

  const signup = async (name: string, email: string, password: string, role: 'client' | 'freelancer') => {
    const data = await api.signup(name, email, password, role);
    setUser(data.user);
    signalingClient.connect(data.access_token);
  };

  const logout = async () => {
    signalingClient.disconnect();
    await api.logout();
    setUser(null);
  };

  return (
    <AuthContext.Provider value={{ user, loading, login, signup, logout }}>{children}</AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within AuthProvider');
  return ctx;
}
