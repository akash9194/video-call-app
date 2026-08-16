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

export { RTCIceCandidate, RTCSessionDescription };
