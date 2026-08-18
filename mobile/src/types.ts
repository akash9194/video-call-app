export interface User {
  id: string;
  name: string;
  email: string;
  role: 'doctor' | 'patient';
  is_online: boolean;
}

export interface IceServer {
  urls: string[] | string;
  username?: string;
  credential?: string;
}

export interface Appointment {
  id: string;
  doctor_id: string;
  doctor_name: string;
  patient_id: string;
  patient_name: string;
  scheduled_time: string; // ISO 8601
  status: 'scheduled' | 'completed' | 'cancelled';
  notes: string | null;
  created_at: string;
}

export type CallMedia = 'audio' | 'video';

export type SignalingMessage =
  | { type: 'call:incoming'; call_id: string; from: string; from_name: string; media: CallMedia }
  | { type: 'call:accepted'; call_id: string; from: string }
  | { type: 'call:rejected'; call_id: string; from: string }
  | { type: 'call:cancelled'; call_id: string; from: string; reason?: string }
  | { type: 'call:answered_elsewhere'; call_id: string; from: string }
  | { type: 'call:ended'; call_id: string; from: string; reason?: string }
  | { type: 'call:user-offline'; call_id: string }
  | { type: 'call:timeout'; call_id: string }
  | { type: 'call:peer-disconnected'; call_id: string }
  | { type: 'call:peer-reconnected'; call_id: string }
  | { type: 'webrtc:offer'; call_id: string; from: string; sdp: any }
  | { type: 'webrtc:answer'; call_id: string; from: string; sdp: any }
  | { type: 'webrtc:ice-candidate'; call_id: string; from: string; candidate: any }
  | { type: 'call:media-switch'; call_id: string; from: string; media: CallMedia }
  | { type: 'presence:update'; user_id: string; is_online: boolean }
  | { type: 'error'; message: string; code?: string };

export type CallStatus =
  | 'idle'
  | 'calling' // we invited, waiting for answer
  | 'incoming' // someone is calling us
  | 'connecting' // accepted, doing SDP/ICE exchange
  | 'active'
  | 'reconnecting' // active call, peer's connection just dropped -- grace period before it's treated as over
  | 'ended';
