import React, { createContext, useContext, useEffect, useRef, useState } from 'react';
import { Alert, Platform } from 'react-native';
import InCallManager from 'react-native-incall-manager';
import { MediaStream } from 'react-native-webrtc';

import { signalingClient } from '../services/signaling';
import { api } from '../services/api';
import { createPeerConnection, getLocalStream, switchCamera, RTCIceCandidate, RTCSessionDescription } from '../services/webrtc';
import { bucketNetworkQuality, NetworkQuality } from '../services/networkQuality';
import { getVideoEncodingConstraints } from '../services/videoQualityAdaptation';
import { mapMediaError } from '../services/mediaErrors';
import { resolveAudioRoute } from '../services/audioRoute';
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
  isSpeakerOn: boolean; // epic §22 -- speaker vs earpiece routing
  isBluetoothOn: boolean; // epic §22 -- Bluetooth audio route (Android only, see toggleBluetoothRoute)
  networkQuality: NetworkQuality | null; // epic §23 -- our own outbound-facing view of the call's quality
  callingElapsedSeconds: number; // epic §10 -- seconds spent ringing so far (0 unless status === 'calling')
  startCall: (calleeId: string, calleeName: string, media: CallMedia) => Promise<void>;
  acceptCall: (consent: boolean) => Promise<void>;
  rejectCall: () => void;
  endCall: () => void;
  toggleMute: () => void;
  toggleSpeaker: () => void;
  toggleBluetoothRoute: () => Promise<void>;
  flipCamera: () => void;
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
  // Epic §22 -- speaker/earpiece routing. Defaults to speaker-on for video
  // calls (holding a phone to your ear while looking at video makes no
  // sense) and speaker-off (earpiece) for audio calls, same convention as
  // every phone dialer. InCallManager.start({media}) already nudges the OS
  // this way, but doesn't expose a way to read the state back or let the
  // user override it -- this state + toggle is that explicit control.
  const [isSpeakerOn, setIsSpeakerOn] = useState(false);
  // Epic §22 -- Bluetooth audio route, independent of the speaker/earpiece
  // toggle above. Android only: confirmed by reading react-native-
  // incall-manager's own source (node_modules, not guessed) that its
  // Android native module really implements chooseAudioRoute('BLUETOOTH')
  // (InCallManagerModule.java's AudioDevice enum), while the iOS native
  // module (RNInCallManager.m) never exports a chooseAudioRoute method at
  // all -- calling it there would reject. The library also doesn't expose
  // any JS-level way to detect whether a Bluetooth headset is actually
  // connected before offering this, so it's offered unconditionally on
  // Android and fails gracefully (see toggleBluetoothRoute) if there's
  // nothing to route to.
  const [isBluetoothOn, setIsBluetoothOn] = useState(false);
  // Epic §10 ringing-duration timer -- seconds elapsed since we started
  // ringing (status === 'calling'), reset once we leave that state.
  const [callingElapsedSeconds, setCallingElapsedSeconds] = useState(0);

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
  // Epic §23 network-quality indicator -- independent of the fallback flag
  // above, so it polls on every call regardless.
  const lastAudioStatsRef = useRef<{ packetsLost: number; packetsReceived: number } | null>(null);
  const lastReportedQualityRef = useRef<NetworkQuality | null>(null);
  const [networkQuality, setNetworkQuality] = useState<NetworkQuality | null>(null);
  // Epic §30: whether THIS call ever actually connected (reached 'active'),
  // as opposed to ringing out, being declined, or the caller cancelling
  // first. Post-call notes only make sense for a call that happened --
  // read by finishCall() below, reset alongside everything else in
  // resetCallState().
  const everConnectedRef = useRef(false);
  // Epic §23: the last quality tier we actually applied to our outbound
  // video sender, so a repeated identical call:network-quality report
  // (the peer polls every 5s, same as we do) doesn't call
  // getParameters/setParameters on every single report -- only on an
  // actual tier change.
  const lastAppliedOutboundQualityRef = useRef<NetworkQuality | null>(null);
  const callIdRef = useRef<string | null>(null);
  const remoteUserIdRef = useRef<string | null>(null);
  const isVideoOnRef = useRef(false);
  useEffect(() => { callIdRef.current = callId; }, [callId]);
  useEffect(() => { remoteUserIdRef.current = remoteUserId; }, [remoteUserId]);
  useEffect(() => { isVideoOnRef.current = isVideoOn; }, [isVideoOn]);
  useEffect(() => { if (status === 'active') everConnectedRef.current = true; }, [status]);

  // Epic §10 ringing-duration timer. Ticks only while we're the caller
  // waiting for an answer; resets to 0 the instant we leave that state
  // (accepted, declined, timed out, cancelled -- doesn't matter which).
  useEffect(() => {
    if (status !== 'calling') {
      setCallingElapsedSeconds(0);
      return;
    }
    setCallingElapsedSeconds(0);
    const startedAt = Date.now();
    const timer = setInterval(() => {
      setCallingElapsedSeconds(Math.floor((Date.now() - startedAt) / 1000));
    }, 1000);
    return () => clearInterval(timer);
  }, [status]);

  const stopStatsMonitor = () => {
    if (statsTimerRef.current) clearInterval(statsTimerRef.current);
    statsTimerRef.current = null;
    poorVideoStreakRef.current = 0;
    lastVideoStatsRef.current = null;
    lastAudioStatsRef.current = null;
    lastReportedQualityRef.current = null;
    setNetworkQuality(null);
  };

  // Polls WebRTC stats every 5s for the lifetime of a call. Two independent
  // things happen off the same poll:
  //   1. Network-quality indicator (epic §23, always on): bucketed from the
  //      audio track's packet-loss ratio, since audio exists on every call
  //      (video is optional) -- relayed to the peer via call:network-quality
  //      and reflected locally via `networkQuality`.
  //   2. Automatic audio-only fallback (epic §21, gated behind the backend
  //      flag): sustained heavy loss on the VIDEO track for 3 consecutive
  //      polls (~15s) triggers switchToVoice(true).
  const startStatsMonitor = () => {
    stopStatsMonitor();
    statsTimerRef.current = setInterval(async () => {
      const pc = pcRef.current;
      if (!pc) {
        poorVideoStreakRef.current = 0;
        lastVideoStatsRef.current = null;
        lastAudioStatsRef.current = null;
        return;
      }
      try {
        const stats = await pc.getStats();
        let videoReport: any = null;
        let audioReport: any = null;
        stats.forEach((r: any) => {
          if (r.type === 'inbound-rtp' && r.kind === 'video') videoReport = r;
          if (r.type === 'inbound-rtp' && r.kind === 'audio') audioReport = r;
        });

        if (audioReport) {
          const nowA = { packetsLost: audioReport.packetsLost || 0, packetsReceived: audioReport.packetsReceived || 0 };
          const lastA = lastAudioStatsRef.current;
          if (lastA) {
            const dLost = Math.max(0, nowA.packetsLost - lastA.packetsLost);
            const dRecv = Math.max(0, nowA.packetsReceived - lastA.packetsReceived);
            const total = dLost + dRecv;
            const lossRatio = total > 0 ? dLost / total : 0;
            const quality = bucketNetworkQuality(lossRatio);
            setNetworkQuality(quality);
            if (quality !== lastReportedQualityRef.current && callIdRef.current && remoteUserIdRef.current) {
              lastReportedQualityRef.current = quality;
              signalingClient.send({
                type: 'call:network-quality',
                call_id: callIdRef.current,
                to: remoteUserIdRef.current,
                quality,
              });
            }
          }
          lastAudioStatsRef.current = nowA;
        }

        if (!isVideoOnRef.current || !videoReport) {
          poorVideoStreakRef.current = 0;
          lastVideoStatsRef.current = null;
          return;
        }
        const now = { packetsLost: videoReport.packetsLost || 0, packetsReceived: videoReport.packetsReceived || 0 };
        const last = lastVideoStatsRef.current;
        if (last) {
          const dLost = Math.max(0, now.packetsLost - last.packetsLost);
          const dRecv = Math.max(0, now.packetsReceived - last.packetsReceived);
          const total = dLost + dRecv;
          const lossRatio = total > 0 ? dLost / total : 0;
          poorVideoStreakRef.current = lossRatio > 0.08 ? poorVideoStreakRef.current + 1 : 0;
          if (poorVideoStreakRef.current >= 3 && audioOnlyAutoFallbackEnabledRef.current) {
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
    setIsSpeakerOn(false);
    setIsBluetoothOn(false);
    everConnectedRef.current = false;
    lastAppliedOutboundQualityRef.current = null;
    InCallManager.stop();
  };

  // Epic §23: apply the peer-reported quality tier to our own outbound
  // video encoding -- see videoQualityAdaptation.ts for the constraint
  // table and why this reacts to the PEER's report (call:network-
  // quality received from them) rather than our own outbound-facing
  // view of the call. No-ops cleanly if there's no live video sender
  // (audio-only call, or video not yet added) -- nothing to throttle.
  const applyOutboundVideoConstraints = async (quality: NetworkQuality) => {
    if (lastAppliedOutboundQualityRef.current === quality) return;
    const pc = pcRef.current;
    if (!pc) return;
    const sender = pc.getSenders().find((s: any) => s.track && s.track.kind === 'video');
    if (!sender) return;
    try {
      const params = sender.getParameters();
      if (!params.encodings || params.encodings.length === 0) {
        params.encodings = [{ active: true }];
      }
      const constraints = getVideoEncodingConstraints(quality);
      params.encodings[0].maxBitrate = constraints.maxBitrate;
      params.encodings[0].scaleResolutionDownBy = constraints.scaleResolutionDownBy;
      await sender.setParameters(params);
      lastAppliedOutboundQualityRef.current = quality;
    } catch (e) {
      // Best-effort -- a failure here degrades to "video stays at
      // whatever quality it already was," never breaks the call itself.
      console.warn('[call] failed to apply adaptive video quality', e);
    }
  };

  // Epic §30: the single place every "this call is over" path routes
  // through to decide where to send the user next. Reads callId/
  // remoteUserName/everConnectedRef BEFORE resetCallState() clears them --
  // order matters here.
  //
  // Only a doctor who was actually connected (not just ringing) gets
  // routed to the notes screen: notes/outcome are a clinical record of
  // what happened on the call, so they don't make sense for a call that
  // never connected, and today's OUTCOMES vocabulary (RESOLVED, REFERRED,
  // ESCALATED, ...) is clinician framing, not something a patient should
  // be prompted to fill in about their own visit. A patient can still see
  // any notes added via call history -- there just isn't a prompt for one
  // here.
  const finishCall = () => {
    const finishedCallId = callId;
    const finishedRemoteName = remoteUserName;
    const shouldPromptNotes = everConnectedRef.current && !!finishedCallId && user?.role === 'doctor';
    resetCallState();
    if (shouldPromptNotes) {
      navigate('PostCallNotes', { callId: finishedCallId as string, remoteUserName: finishedRemoteName || '' });
    } else {
      navigate('Home');
    }
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
    signalingClient.send({ type: 'call:invite', to: calleeId, media, platform: Platform.OS });
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
    // Epic §22 default: speaker for video, earpiece for audio-only.
    const defaultSpeaker = initialMediaRef.current === 'video';
    InCallManager.setSpeakerphoneOn(defaultSpeaker);
    setIsSpeakerOn(defaultSpeaker);
    try {
      await setupPeerConnection(remoteUserId, callId, initialMediaRef.current);
    } catch (e) {
      // Epic §8: previously an unhandled rejection here (camera/mic
      // permission denied, no device, already in use by another app)
      // left this device frozen on "Connecting..." AND, worse, the
      // caller was never told anything -- call:accept was never sent, so
      // they'd just sit there ringing until their own timeout. Reject
      // cleanly instead so the caller finds out immediately, and tell
      // this user what actually went wrong.
      const info = mapMediaError(e);
      console.warn('[call] acceptCall failed to acquire camera/mic', info.code, e);
      // call:reject doesn't currently distinguish *why* the callee didn't
      // join (it always records PATIENT_DECLINED) -- teaching the backend
      // to trust a client-supplied reason is a separate, bigger change
      // with its own trust implications, out of scope here. What matters
      // most is fixed: the caller gets told immediately instead of
      // ringing into a void.
      signalingClient.send({ type: 'call:reject', call_id: callId, to: remoteUserId });
      resetCallState();
      navigate('Home');
      Alert.alert('Could not join call', info.message);
      return;
    }
    signalingClient.send({ type: 'call:accept', call_id: callId, to: remoteUserId, consent: true, platform: Platform.OS });
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
    // Previously this only reset local state and never navigated anywhere,
    // leaving whoever tapped "End" stranded on the (now-blank) Call screen
    // until they backgrounded and reopened the app. finishCall() (epic
    // §30) fixes that as a side effect of adding the notes-screen routing.
    finishCall();
  };

  const toggleMute = () => {
    localStream?.getAudioTracks().forEach((t: any) => (t.enabled = isMuted));
    setIsMuted(!isMuted);
  };

  const toggleSpeaker = () => {
    const next = !isSpeakerOn;
    InCallManager.setSpeakerphoneOn(next);
    setIsSpeakerOn(next);
  };

  // Epic §22 Bluetooth audio route. See the isBluetoothOn declaration
  // above for why this is Android-only and can't detect device
  // availability up front. Turning it OFF routes back to whatever the
  // speaker/earpiece toggle currently reflects, rather than a hardcoded
  // choice, so the two controls compose the way a real phone's audio
  // routing does (Bluetooth is an override on top of speaker/earpiece,
  // not a third independent state that has to be reasoned about
  // separately). A rejection here (most commonly: no Bluetooth audio
  // device is actually paired/connected right now) is handled the same
  // way flipCamera() handles "only one camera" -- logged, state left
  // unchanged, no crash, no scary alert for something this routine.
  const toggleBluetoothRoute = async () => {
    if (Platform.OS !== 'android') {
      Alert.alert('Not available', 'Bluetooth audio routing from this screen is only supported on Android right now.');
      return;
    }
    const next = !isBluetoothOn;
    try {
      await InCallManager.chooseAudioRoute(resolveAudioRoute(next, isSpeakerOn));
      setIsBluetoothOn(next);
    } catch (e) {
      console.warn('[call] chooseAudioRoute failed (likely no Bluetooth audio device connected):', e);
    }
  };

  // Epic §22 front/rear camera flip. Only meaningful while a live local
  // video track exists -- no-ops (returns false, logged) on an audio-only
  // call, same guard the underlying _switchCamera() itself enforces.
  const flipCamera = () => {
    if (!switchCamera(localStream)) {
      console.warn('[call] flipCamera: no live video track to switch');
    }
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
    let cameraStream: MediaStream;
    try {
      cameraStream = await getLocalStream(true);
    } catch (e) {
      // Epic §8: a failed mid-call camera grab (permission revoked,
      // camera taken by another app since the call started) previously
      // threw here uncaught -- the call itself must survive this, just
      // stay audio-only and tell the user why the switch didn't happen.
      const info = mapMediaError(e);
      console.warn('[call] switchToVideo failed to acquire camera', info.code, e);
      Alert.alert('Could not switch to video', info.message);
      return;
    }
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
          // react-native-incall-manager's own .d.ts marks vibrate_pattern/
          // ios_category/seconds as required, but the library's actual JS
          // implementation treats all three as optional (defaults: no
          // vibration, "default" iOS category, infinite looping on
          // Android) -- calling with just the ringtone name is correct
          // and has always been the intended usage here; this is purely
          // an upstream type-stub inaccuracy, not a real missing-args bug.
          (InCallManager.startRingtone as (ringtone: string) => void)('_DEFAULT_');
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
          // Epic §22 default: speaker for video, earpiece for audio-only.
          const defaultSpeaker = initialMediaRef.current === 'video';
          InCallManager.setSpeakerphoneOn(defaultSpeaker);
          setIsSpeakerOn(defaultSpeaker);
          setStatus('connecting');
          try {
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
          } catch (e) {
            // Epic §8: the callee already accepted (their side is live
            // and waiting), so if OUR camera/mic grab fails here we can't
            // just go quiet -- previously this was an unhandled
            // rejection that left both sides stuck. End the call so the
            // callee isn't left hanging, and tell this side why.
            const info = mapMediaError(e);
            console.warn('[call] call:accepted handler failed to acquire camera/mic', info.code, e);
            signalingClient.send({ type: 'call:end', call_id: acceptedCallId, to: remoteUserId });
            resetCallState();
            navigate('Home');
            Alert.alert('Could not start call', info.message);
          }
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

        case 'call:network-quality': {
          // The peer's report of how THEY'RE experiencing the call (epic
          // §23). UPDATED: now actually acted on, not just logged --
          // throttles our own outbound video encoding when they report
          // fair/poor reception, restores full quality when they report
          // good. See applyOutboundVideoConstraints above.
          console.log('[call] peer reports network quality:', msg.quality);
          applyOutboundVideoConstraints(msg.quality);
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
          const calledName = remoteUserName;
          resetCallState();
          navigate('Home');
          Alert.alert('No answer', calledName ? `${calledName} didn't answer. You can try again anytime.` : "No answer.");
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

        case 'call:cancelled': {
          if (callId !== null && msg.call_id && msg.call_id !== callId) break;
          // Epic §31: if we were the callee (ringing) and this is the call
          // timing out rather than the caller manually cancelling, show a
          // friendly, non-alarming "you missed a call" notice -- before
          // this, the incoming-call screen just disappeared with no
          // explanation at all.
          const wasRinging = status === 'incoming';
          const missedFrom = remoteUserName;
          InCallManager.stopRingtone();
          resetCallState();
          navigate('Home');
          if (wasRinging && msg.reason === 'timeout') {
            Alert.alert('Missed call', missedFrom ? `You missed a video call from ${missedFrom}.` : 'You missed a call.');
          }
          break;
        }

        case 'call:rejected':
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
          // finishCall() (epic §30) routes a doctor to the post-call notes
          // screen instead of Home when the call actually connected --
          // 'call:ended' (the peer hung up) is the realistic case that can
          // hit that branch; rejected/user-offline/answered-elsewhere never
          // reach 'active' so they always just go Home, same as before.
          finishCall();
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
        isSpeakerOn,
        isBluetoothOn,
        networkQuality,
        callingElapsedSeconds,
        startCall,
        acceptCall,
        rejectCall,
        endCall,
        toggleMute,
        toggleSpeaker,
        toggleBluetoothRoute,
        flipCamera,
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
