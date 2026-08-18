import AsyncStorage from '@react-native-async-storage/async-storage';
import { WS_BASE_URL } from '../config';
import { SignalingMessage } from '../types';

type Listener = (msg: SignalingMessage) => void;

const DEVICE_ID_KEY = 'device_id';

// A stable per-install device_id, persisted across app restarts, is what
// lets a dropped WebSocket reconnect as the SAME device server-side (see
// ws_manager.py's disconnect/reconnect grace-period handling) instead of
// looking like a brand-new device the server can no longer route an
// in-progress call to.
async function getDeviceId(): Promise<string> {
  let id = await AsyncStorage.getItem(DEVICE_ID_KEY);
  if (!id) {
    id = 'mobile-' + Math.random().toString(36).slice(2) + Date.now().toString(36);
    await AsyncStorage.setItem(DEVICE_ID_KEY, id);
  }
  return id;
}

class SignalingClient {
  private ws: WebSocket | null = null;
  private listeners = new Set<Listener>();
  private token: string | null = null;
  private deviceId: string | null = null;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private shouldReconnect = false;
  // Fired whenever a fresh connection opens (including reconnects), after
  // the device_id is resolved -- CallContext uses this to know when it's
  // safe to attempt an ICE restart on an active call's peer connection.
  private reconnectListeners = new Set<() => void>();

  connect(token: string) {
    this.token = token;
    this.shouldReconnect = true;
    this.open();
  }

  private async open() {
    if (!this.deviceId) this.deviceId = await getDeviceId();
    // token/shouldReconnect may have changed while we awaited AsyncStorage
    // (e.g. a fast logout) -- bail rather than opening a socket nobody wants.
    if (!this.shouldReconnect || !this.token) return;

    this.ws = new WebSocket(`${WS_BASE_URL}/ws/signaling?token=${this.token}&device_id=${this.deviceId}`);

    this.ws.onopen = () => {
      console.log('[signaling] connected');
      this.reconnectListeners.forEach((l) => l());
    };

    this.ws.onmessage = (event) => {
      try {
        const msg: SignalingMessage = JSON.parse(event.data);
        this.listeners.forEach((l) => l(msg));
      } catch (e) {
        console.warn('[signaling] failed to parse message', e);
      }
    };

    this.ws.onerror = (e) => {
      console.warn('[signaling] error', e);
    };

    this.ws.onclose = () => {
      console.log('[signaling] closed');
      if (this.shouldReconnect) {
        this.reconnectTimer = setTimeout(() => this.open(), 2000);
      }
    };
  }

  disconnect() {
    this.shouldReconnect = false;
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    this.ws?.close();
    this.ws = null;
  }

  send(message: Record<string, any>) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(message));
    } else {
      console.warn('[signaling] cannot send, socket not open', message);
    }
  }

  subscribe(listener: Listener) {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  }

  onReconnect(listener: () => void) {
    this.reconnectListeners.add(listener);
    return () => {
      this.reconnectListeners.delete(listener);
    };
  }
}

export const signalingClient = new SignalingClient();
