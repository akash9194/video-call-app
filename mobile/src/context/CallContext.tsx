import React, { createContext, useContext, useEffect, useRef, useState } from 'react';
import { Alert } from 'react-native';
import InCallManager from 'react-native-incall-manager';
import { MediaStream } from 'react-native-webrtc';

import { signalingClient } from '../services/signaling';
import { api } from '../services/api';
import { createPeerConnection, getLocalStream, RTCIceCandidate, RTCSessionDescription } from '../services/webrtc';
import { CallStatus, CallMedia, SignalingMessage } from '../types';
import { navigate } from '../navigation';
import { useAuth } from './AuthContext';

interface CallContextValue {
  status: CallStatus;
  callId: string | null;
  remoteUserId: string | null;
  remoteUserName: string | null;
  localStream: MediaStream | null;
  remoteStream: MediaStream | null;
  isMuted: boolean;
  isVideoOn: boolean; // do we currently have a live local video track in this call?
  isRemoteVideoOn: boolean; // is the OTHER side currently sending video?
  startCall: (calleeId: string, calleeName: string, media: CallMedia) => Promise<void>;
  acceptCall: (consent: boolean) => Promise<void>;
  rejectCall: () => void;
  endCall: () => void;
  toggleMute: () => void;
  switchToVideo: () => Promise<void>;
  switchToVoice: (auto?: boolean) => void;
}

const CallContext = createContext<CallContextValue | undefined>(undefined);

// Pending ICE candidates that arrive before the remote description is set.
let queuedCandidates: any[] = [];

export function CallProvider({ children }: { children: React.ReactNode }) {
  const { user } = useAuth();
  const [status, setStatus] = useState<CallStatus>('idle');
  const [callId, setCallId] = useState<string | null>(null);
  const [remoteUserId, setRemoteUserId] = useState<string | null>(null);
  const [remoteUserName, setRemoteUserName] = useState<string | null>(null);
  const [localStream, setLocalStream] = useState<MediaStream | null>(null);
  const [remoteStream, setRemoteStream] = useState<MediaStream | null>(null);
  const [isMuted, setIsMuted] = useState(false);
  const [isVideoOn, setIsVideoOn] = useState(false);
  const [isRemoteVideoOn, setIsRemoteVideoOn] = useState(false);

  const pcRef = useRef<any>(null);
  // How THIS call started ('audio' or 'video') -- set by startCall (caller)
  // or by the call:incoming message (callee). Read when the peer
  // connection is actually created (call:accepted for the caller,
  // acceptCall for the callee) to decide whether to request a camera.
  const initialMediaRef = useRef<CallMedia>('video');

  // Kept in refs (not state) since they're read/written from a setInterval
  // callback and a signalingClient.onReconnect callback, both of which
  // close over stale state otherwise.
  const statsTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  // Epic §21: the automatic audio-only fallback loop must be off unless the
  // backend explicitly says it's approved (Settings.audio_only_auto_
  // fallback_enabled). Fetched fresh per-call in setupPeerConnection, same
  // as ICE servers, since it's config that can change without an app update.
  const audioOnlyAutoFallbackEnabledRef = useRef(false);
  const poorVideoStreakRef = useRef(0);
  const lastVideoStatsRef = useRef<{ packetsLost: number; packetsReceived: number } | null>(null);
  const callIdRef = useRef<string | null>(null);
  const remoteUserIdRef = useRef<string | null>(null);
  const isVideoOnRef = useRef(false);
  useEffect(() => { callIdRef.current = callId; }, [callId]);
  useEffect(() => { remoteUserIdRef.current = remoteUserId; }, [remoteUserId]);
  useEffect(() => { isVideoOnRef.current = isVideoOn; }, [isVideoOn]);

  const stopStatsMonitor = () => {
    if (statsTimerRef.current) clearInterval(statsTimerRef.current);
    statsTimerRef.current = null;
    poorVideoStreakRef.current = 0;
    lastVideoStatsRef.current = null;
  };

  // Polls WebRTC stats every 5s while a call has live video, and if the
  // receiving side sees sustained heavy packet loss on the video track (3
  // consecutive bad polls, ~15s) auto-switches to audio-only rather than
  // leaving both sides stuck with a frozen/stuttering picture -- a clean
  // fallback beats an unpredictable one.
  const startStatsMonitor = () => {
    stopStatsMonitor();
    if (!audioOnlyAutoFallbackEnabledRef.current) return;
    statsTimerRef.current = setInterval(async () => {
      const pc = pcRef.current;
      if (!pc || !isVideoOnRef.current) {
        poorVideoStreakRef.current = 0;
        lastVideoStatsRef.current = null;
        return;
      }
      try {
        const stats = await pc.getStats();
        let report: any = null;
        stats.forEach((r: any) => {
          if (r.type === 'inbound-rtp' && r.kind === 'video') report = r;
        });
        if (!report) return;
        const now = { packetsLost: report.packetsLost || 0, packetsReceived: report.packetsReceived || 0 };
        const last = lastVideoStatsRef.current;
        if (last) {
          const dLost = Math.max(0, now.packetsLost - last.packetsLost);
          const dRecv = Math.max(0, now.packetsReceived - last.packetsReceived);
          const total = dLost + dRecv;
          const lossRatio = total > 0 ? dLost / total : 0;
          poorVideoStreakRef.current = lossRatio > 0.08 ? poorVideoStreakRef.current + 1 : 0;
          if (poorVideoStreakRef.current >= 3) {
            poorVideoStreakRef.current = 0;
            console.warn('[call] sustained poor video conditions -- auto-falling back to audio-only');
            switchToVoice(true);
          }
        }
        lastVideoStatsRef.current = now;
      } catch (e) {
        console.warn('[call] getStats failed', e);
      }
    }, 5000);
  };

  const resetCallState = () => {
    stopStatsMonitor();
    pcRef.current?.close();
    pcRef.current = null;
    localStream?.getTracks().forEach((t) => t.stop());
    queuedCandidates = [];
    setStatus('idle');
    setCallId(null);
    setRemoteUserId(null);
    setRemoteUserName(null);
    setLocalStream(null);
    setRemoteStream(null);
    setIsMuted(false);
    setIsVideoOn(false);
    setIsRemoteVideoOn(false);
    InCallManager.stop();
  };

  const setupPeerConnection = async (targetUserId: string, currentCallId: string, media: CallMedia) => {
    const iceConfig = await api.getIceServersConfig();
    audioOnlyAutoFallbackEnabledRef.current = !!iceConfig.audio_only_auto_fallback_enabled;
    const pc = createPeerConnection(iceConfig.ice_servers);
    pcRef.current = pc;

    const stream = await getLocalStream(media === 'video');
    setLocalStream(stream);
    stream.getTracks().forEach((track: any) => pc.addTrack(track, stream));
    setIsVideoOn(media === 'video');

    const remote = new MediaStream(undefined as any);
    setRemoteStream(remote);

    pc.ontrack = (event: any) => {
      event.streams[0]?.getTracks().forEach((track: any) => {
        remote.addTrack(track);
        if (track.kind === 'video') setIsRemoteVideoOn(true);
      });
    };

    pc.onicecandidate = (event: any) => {
      if (event.candidate) {
        signalingClient.send({
          type: 'webrtc:ice-candidate',
          call_id: currentCallId,
          to: targetUserId,
          candidate: event.candidate,
        });
      }
    };

    startStatsMonitor();
    return pc;
  };

  const startCall = async (calleeId: string, calleeName: string, media: CallMedia) => {
    initialMediaRef.current = media;
    setStatus('calling');
    setRemoteUserId(calleeId);
    setRemoteUserName(calleeName);
    signalingClient.send({ type: 'call:invite', to: calleeId, media });
    // call_id + offer creation happens once the callee accepts (see call:accepted below)
  };

  const acceptCall = async (consent: boolean) => {
    if (!callId || !remoteUserId) return;
    // Belt-and-suspenders: IncomingCallScreen disables Accept until this is
    // checked, but the real gate is server-side (ws_manager.py rejects an
    // accept with no consent:true, code consent_required) since a
    // client-side check alone could be bypassed the same way doctor-only
    // initiation could.
    if (!consent) return;
    setStatus('connecting');
    InCallManager.start({ media: 'video' });
    await setupPeerConnection(remoteUserId, callId, initialMediaRef.current);
    signalingClient.send({ type: 'call:accept', call_id: callId, to: remoteUserId, consent: true });
    // Offer will arrive from the caller next; we answer it in the message handler.
  };

  const rejectCall = () => {
    if (callId && remoteUserId) {
      signalingClient.send({ type: 'call:reject', call_id: callId, to: remoteUserId });
    }
    resetCallState();
  };

  const endCall = () => {
    if (callId && remoteUserId) {
      signalingClient.send({ type: 'call:end', call_id: callId, to: remoteUserId });
    }
    resetCallState();
  };

  const toggleMute = () => {
    localStream?.getAudioTracks().forEach((t: any) => (t.enabled = isMuted));
    setIsMuted(!isMuted);
  };

  // Switching video on/off mid-call. If a local video track already
  // exists (this call started as video, or we already switched to video
  // once before), this is just enabling/disabling it -- cheap, no
  // renegotiation, works everywhere. If NO local video track exists yet
  // (this call started as audio-only and video has never been added),
  // switching to video needs a real camera + a WebRTC renegotiation (a
  // second offer/answer on the SAME connection) since there was never a
  // video "slot" in the peer connection to enable.
  const switchToVideo = async () => {
    const pc = pcRef.current;
    if (!pc || !callId || !remoteUserId) return;

    const existingVideoTrack = localStream?.getVideoTracks()[0];
    if (existingVideoTrack) {
      existingVideoTrack.enabled = true;
      setIsVideoOn(true);
      signalingClient.send({ type: 'call:media-switch', call_id: callId, to: remoteUserId, media: 'video' });
      return;
    }

    // No video track yet -- get the camera and add it, then renegotiate.
    const cameraStream = await getLocalStream(true);
    const videoTrack = cameraStream.getVideoTracks()[0];
    if (!videoTrack) return;
    pc.addTrack(videoTrack, cameraStream);
    setLocalStream((prev) => {
      if (!prev) return cameraStream;
      prev.addTrack(videoTrack);
      return prev;
    });
    setIsVideoOn(true);

    signalingClient.send({ type: 'call:media-switch', call_id: callId, to: remoteUserId, media: 'video' });

    const offer = await pc.createOffer({});
    await pc.setLocalDescription(offer);
    signalingClient.send({ type: 'webrtc:offer', call_id: callId, to: remoteUserId, sdp: offer });
    // The answer comes back through the normal 'webrtc:answer' handler below.
  };

  // Turning video off never removes the track or renegotiates -- just
  // disables it. This is simpler, faster, and works identically on every
  // platform; the track stays reserved in the connection so switching
  // back to video later (switchToVideo above) is instant.
  const switchToVoice = (auto?: boolean) => {
    if (!callId || !remoteUserId) return;
    localStream?.getVideoTracks().forEach((t: any) => (t.enabled = false));
    setIsVideoOn(false);
    signalingClient.send({
      type: 'call:media-switch',
      call_id: callId,
      to: remoteUserId,
      media: 'audio',
      ...(auto ? { auto: true } : {}),
    });
  };

  useEffect(() => {
    const unsubscribe = signalingClient.subscribe(async (msg: SignalingMessage) => {
      switch (msg.type) {
        case 'call:incoming': {
          initialMediaRef.current = msg.media;
          setCallId(msg.call_id);
          setRemoteUserId(msg.from);
          setRemoteUserName(msg.from_name);
          setStatus('incoming');
          InCallManager.startRingtone('_DEFAULT_');
          navigate('IncomingCall', {
            callId: msg.call_id,
            fromUserId: msg.from,
            fromUserName: msg.from_name,
            media: msg.media,
          });
          break;
        }

        case 'call:accepted': {
          // We are the caller; callee accepted. The server generated the
          // call_id back at call:invite time and only now tells us what it
          // was -- our own `callId` state was never set (call:invite fires
          // before we know it), so we MUST use msg.call_id here, not the
          // (still-null) callId from state.
          const acceptedCallId = msg.call_id;
          if (!remoteUserId) break;
          setCallId(acceptedCallId);
          InCallManager.stopRingtone();
          InCallManager.start({ media: 'video' });
          setStatus('connecting');
          const pc = await setupPeerConnection(remoteUserId, acceptedCallId, initialMediaRef.current);
          const offer = await pc.createOffer({});
          await pc.setLocalDescription(offer);
          signalingClient.send({ type: 'webrtc:offer', call_id: acceptedCallId, to: remoteUserId, sdp: offer });
          navigate('Call', {
            callId: acceptedCallId,
            remoteUserId,
            remoteUserName: remoteUserName || '',
            isCaller: true,
          });
          break;
        }

        case 'webrtc:offer': {
          // Handles BOTH cases with the same code: the callee's very first
          // offer for this call (pc was already created in acceptCall,
          // just above), AND a later renegotiation offer sent mid-call by
          // switchToVideo when the peer adds a video track. Either way we
          // just answer on the existing connection -- never recreate it or
          // touch our own local tracks here.
          const pc = pcRef.current;
          if (!pc) break;
          await pc.setRemoteDescription(new RTCSessionDescription(msg.sdp));
          for (const c of queuedCandidates) await pc.addIceCandidate(new RTCIceCandidate(c));
          queuedCandidates = [];
          const answer = await pc.createAnswer();
          await pc.setLocalDescription(answer);
          signalingClient.send({ type: 'webrtc:answer', call_id: msg.call_id, to: msg.from, sdp: answer });
          setStatus('active');
          navigate('Call', {
            callId: msg.call_id,
            remoteUserId: msg.from,
            remoteUserName: remoteUserName || '',
            isCaller: false,
          });
          break;
        }

        case 'webrtc:answer': {
          // Also handles both the initial answer and any later
          // renegotiation answer -- setRemoteDescription is symmetric.
          const pc = pcRef.current;
          if (!pc) break;
          await pc.setRemoteDescription(new RTCSessionDescription(msg.sdp));
          for (const c of queuedCandidates) await pc.addIceCandidate(new RTCIceCandidate(c));
          queuedCandidates = [];
          setStatus('active');
          break;
        }

        case 'webrtc:ice-candidate': {
          const pc = pcRef.current;
          if (pc && pc.remoteDescription) {
            await pc.addIceCandidate(new RTCIceCandidate(msg.candidate));
          } else {
            queuedCandidates.push(msg.candidate);
          }
          break;
        }

        case 'call:media-switch': {
          // Advance notice from the peer that their outgoing video just
          // turned on/off -- lets the UI react (show their video vs an
          // avatar) without waiting for the renegotiation round-trip (if
          // any) to land.
          setIsRemoteVideoOn(msg.media === 'video');
          break;
        }

        case 'error': {
          if (msg.code === 'consent_required') {
            // Shouldn't normally happen -- IncomingCallScreen disables
            // Accept until consent is checked -- but the server is the
            // real gate, so handle a rejection here too rather than
            // leaving the UI stuck on "Connecting...".
            Alert.alert('Could not join', msg.message);
            InCallManager.stopRingtone();
            resetCallState();
            navigate('Home');
            break;
          }
          // Most relevant here: call:invite was rejected server-side (not
          // authorized to call, invalid callee, or no active appointment)
          // -- the client was never told a call_id, so there's nothing to
          // clean up beyond resetting the "Calling..." state.
          if (status === 'calling') {
            Alert.alert('Call not started', msg.message);
            resetCallState();
            navigate('Home');
          }
          break;
        }

        case 'call:timeout': {
          // We were the caller and nobody answered in time.
          Alert.alert('No answer', 'The call timed out.');
          resetCallState();
          navigate('Home');
          break;
        }

        case 'call:peer-disconnected': {
          // The other side's connection just dropped (wifi hiccup,
          // backgrounded app, ...) -- the call isn't over yet, the server
          // gives them a grace period to reconnect. Show that instead of
          // looking frozen or dead.
          if (msg.call_id === callId) setStatus('reconnecting');
          break;
        }

        case 'call:peer-reconnected': {
          if (msg.call_id === callId) setStatus('active');
          break;
        }

        case 'call:rejected':
        case 'call:cancelled':
        case 'call:ended':
        case 'call:user-offline':
        case 'call:answered_elsewhere': {
          // Ignore a message for a call_id that isn't the one we're
          // currently in -- e.g. a late/stale message for a call that
          // already ended and was replaced by a new one. Only applies
          // once we actually know our own call_id (callId is still null
          // for a caller who hasn't been accepted yet -- there's nothing
          // to compare against at that point, so let those through same
          // as before). answered_elsewhere means this same account
          // accepted the call on a different device (phone/tablet/web) --
          // this device was one of several that rang, and lost. Just
          // dismiss quietly, same as any other "this call isn't happening
          // here" case.
          if (callId !== null && msg.call_id && msg.call_id !== callId) break;
          InCallManager.stopRingtone();
          resetCallState();
          navigate('Home');
          break;
        }
      }
    });

    return unsubscribe;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [callId, remoteUserId, remoteUserName]);

  // If the signaling socket drops and reconnects mid-call (see
  // signaling.ts's auto-reconnect, which reuses the same stable
  // device_id), the underlying peer connection may also have lost its ICE
  // path even though it's technically still open. Nudge it back to life
  // with an ICE restart now that signaling is up again to carry the
  // renegotiation. Registered once; reads current state via refs since
  // this effect intentionally has no deps.
  useEffect(() => {
    return signalingClient.onReconnect(async () => {
      const pc = pcRef.current;
      const currentCallId = callIdRef.current;
      const target = remoteUserIdRef.current;
      if (!pc || !currentCallId || !target) return;
      if (pc.iceConnectionState !== 'disconnected' && pc.iceConnectionState !== 'failed') return;
      try {
        const offer = await pc.createOffer({ iceRestart: true });
        await pc.setLocalDescription(offer);
        signalingClient.send({ type: 'webrtc:offer', call_id: currentCallId, to: target, sdp: offer });
      } catch (e) {
        console.warn('[call] ICE restart failed', e);
      }
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <CallContext.Provider
      value={{
        status,
        callId,
        remoteUserId,
        remoteUserName,
        localStream,
        remoteStream,
        isMuted,
        isVideoOn,
        isRemoteVideoOn,
        startCall,
        acceptCall,
        rejectCall,
        endCall,
        toggleMute,
        switchToVideo,
        switchToVoice,
      }}
    >
      {children}
    </CallContext.Provider>
  );
}

export function useCall() {
  const ctx = useContext(CallContext);
  if (!ctx) throw new Error('useCall must be used within CallProvider');
  return ctx;
}
