import { WS_BASE_URL } from '../config';
import { SignalingMessage } from '../types';

type Listener = (msg: SignalingMessage) => void;

class SignalingClient {
  private ws: WebSocket | null = null;
  private listeners = new Set<Listener>();
  private token: string | null = null;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private shouldReconnect = false;

  connect(token: string) {
    this.token = token;
    this.shouldReconnect = true;
    this.open();
  }

  private open() {
    this.ws = new WebSocket(`${WS_BASE_URL}/ws/signaling?token=${this.token}`);

    this.ws.onopen = () => {
      console.log('[signaling] connected');
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
    return () => this.listeners.delete(listener);
  }
}

export const signalingClient = new SignalingClient();
