import {
  RTCPeerConnection,
  RTCIceCandidate,
  RTCSessionDescription,
  mediaDevices,
  MediaStream,
} from 'react-native-webrtc';
import { IceServer } from '../types';

export async function getLocalStream(withVideo: boolean = true): Promise<MediaStream> {
  // front camera, mic on. Flip `facingMode` to 'environment' for back camera.
  // withVideo=false for voice calls -- skips the camera permission prompt
  // entirely and never touches the camera hardware.
  const stream = await mediaDevices.getUserMedia({
    audio: true,
    video: withVideo
      ? {
          facingMode: 'user',
          width: { ideal: 1280 },
          height: { ideal: 720 },
        }
      : false,
  });
  return stream as unknown as MediaStream;
}

export function createPeerConnection(iceServers: IceServer[]): RTCPeerConnection {
  return new RTCPeerConnection({
    iceServers: iceServers as any,
  });
}

// Epic §22 front/rear camera switch. react-native-webrtc's _switchCamera()
// flips facingMode on the existing local video track in place -- no
// renegotiation needed, the remote side never sees anything change beyond
// the picture itself (same reasoning as toggling a track's `enabled` flag
// for mute/video-off, just for the camera instead). Deprecated upstream in
// favor of applyConstraints(), but still the documented approach and the
// simplest one that doesn't risk a mid-call renegotiation glitch -- fine
// for this app's scope.
export function switchCamera(stream: MediaStream | null): boolean {
  const track: any = stream?.getVideoTracks()[0];
  if (!track || typeof track._switchCamera !== 'function') return false;
  track._switchCamera();
  return true;
}

export { RTCIceCandidate, RTCSessionDescription };
