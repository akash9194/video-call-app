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
  acceptCall: () => Promise<void>;
  rejectCall: () => void;
  endCall: () => void;
  toggleMute: () => void;
  switchToVideo: () => Promise<void>;
  switchToVoice: () => void;
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

  const resetCallState = () => {
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
    const iceServers = await api.getIceServers();
    const pc = createPeerConnection(iceServers);
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

  const acceptCall = async () => {
    if (!callId || !remoteUserId) return;
    setStatus('connecting');
    InCallManager.start({ media: 'video' });
    await setupPeerConnection(remoteUserId, callId, initialMediaRef.current);
    signalingClient.send({ type: 'call:accept', call_id: callId, to: remoteUserId });
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
  const switchToVoice = () => {
    if (!callId || !remoteUserId) return;
    localStream?.getVideoTracks().forEach((t: any) => (t.enabled = false));
    setIsVideoOn(false);
    signalingClient.send({ type: 'call:media-switch', call_id: callId, to: remoteUserId, media: 'audio' });
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

        case 'call:rejected':
        case 'call:cancelled':
        case 'call:ended':
        case 'call:user-offline':
        case 'call:answered_elsewhere': {
          // call:answered_elsewhere means this same account accepted the
          // call on a different device (phone/tablet/web) -- this device
          // was one of several that rang, and lost. Just dismiss quietly,
          // same as any other "this call isn't happening here" case.
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
