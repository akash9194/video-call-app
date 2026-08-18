"""
Validates the voice<->video renegotiation flow: start a call as
audio-only (no video track added by either side), confirm it really is
audio-only, then have one side add a video track mid-call and
renegotiate (second offer/answer on the same connection), and confirm
video frames start flowing without breaking the existing audio.

Run against scripts/_mock_server.py (see scripts/verify_video_call.py
for the full one-command setup instructions).
"""
import asyncio
import json
import sys
import time

import requests
import websockets
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.mediastreams import AudioStreamTrack, VideoStreamTrack

import aioice.ice as _aioice_ice
_aioice_ice.get_host_addresses = lambda use_ipv4, use_ipv6: (["127.0.0.1"] if use_ipv4 else [])

BASE = "http://127.0.0.1:8123"
WS_BASE = "ws://127.0.0.1:8123"
results = {"pass": [], "fail": []}


def check(name, cond):
    (results["pass"] if cond else results["fail"]).append(name)
    print(("  [PASS] " if cond else "  [FAIL] ") + name)


def signup(name, email, role):
    r = requests.post(f"{BASE}/auth/signup", json={"name": name, "email": email, "password": "testpass123", "role": role})
    r.raise_for_status()
    return r.json()


class Peer:
    def __init__(self, label, token):
        self.label = label
        self.token = token
        self.ws = None
        self.pc = None
        self.call_id = None
        self.other_id = None
        self.events = asyncio.Queue()
        self.frames = {"audio": 0, "video": 0}
        self.connected = asyncio.Event()

    async def connect_ws(self):
        self.ws = await websockets.connect(f"{WS_BASE}/ws/signaling?token={self.token}")
        asyncio.create_task(self._loop())

    async def _loop(self):
        try:
            async for raw in self.ws:
                msg = json.loads(raw)
                print(f"    <{self.label}> {msg['type']}")
                await self.events.put(msg)
        except websockets.exceptions.ConnectionClosed:
            pass

    async def wait_for(self, t, timeout=10):
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                msg = await asyncio.wait_for(self.events.get(), timeout=max(0.1, deadline - time.time()))
            except asyncio.TimeoutError:
                break
            if msg["type"] == t:
                return msg
        return None

    async def send(self, msg):
        return await self.ws.send(json.dumps(msg))

    def ensure_pc(self):
        if self.pc:
            return self.pc
        pc = RTCPeerConnection()

        @pc.on("connectionstatechange")
        async def on_state():
            print(f"    [{self.label}] state -> {pc.connectionState}")
            if pc.connectionState == "connected":
                self.connected.set()

        @pc.on("track")
        def on_track(track):
            print(f"    [{self.label}] new remote {track.kind} track")
            asyncio.create_task(self._consume(track))

        self.pc = pc
        return pc

    async def _consume(self, track):
        try:
            while True:
                await track.recv()
                self.frames[track.kind] += 1
        except Exception:
            pass


async def main():
    print("Setup: doctor + patient, doctor creates an appointment, then places an audio-only (voice) call")
    a = signup("Dr. Renego", "renego.doctor@example.com", "doctor")
    b = signup("Renego Patient", "renego.patient@example.com", "patient")
    pa, pb = Peer("A/doctor", a["access_token"]), Peer("B/patient", b["access_token"])
    pa.other_id, pb.other_id = b["user"]["id"], a["user"]["id"]

    appt = requests.post(
        f"{BASE}/appointments",
        headers={"Authorization": f"Bearer {a['access_token']}"},
        json={"patient_id": pa.other_id, "scheduled_time": "2026-08-20T10:00:00Z"},
    )
    check("appointment created", appt.status_code == 201)

    await pa.connect_ws(); await pb.connect_ws()
    await asyncio.sleep(0.3)

    await pa.send({"type": "call:invite", "to": pa.other_id, "media": "audio"})
    incoming = await pb.wait_for("call:incoming")
    check("callee sees media type 'audio' on incoming call", incoming and incoming.get("media") == "audio")
    call_id = incoming["call_id"]
    pa.call_id = pb.call_id = call_id

    await pb.send({"type": "call:accept", "call_id": call_id, "to": pb.other_id})
    accepted = await pa.wait_for("call:accepted")
    check("caller received call:accepted", accepted is not None)

    pc_a = pa.ensure_pc()
    pc_a.addTrack(AudioStreamTrack())  # audio-only call: no video track added
    offer = await pc_a.createOffer()
    await pc_a.setLocalDescription(offer)
    while pc_a.iceGatheringState != "complete":
        await asyncio.sleep(0.05)
    await pa.send({"type": "webrtc:offer", "call_id": call_id, "to": pa.other_id, "sdp": {"sdp": pc_a.localDescription.sdp, "type": pc_a.localDescription.type}})

    offer_msg = await pb.wait_for("webrtc:offer")
    pc_b = pb.ensure_pc()
    pc_b.addTrack(AudioStreamTrack())
    await pc_b.setRemoteDescription(RTCSessionDescription(**offer_msg["sdp"]))
    answer = await pc_b.createAnswer()
    await pc_b.setLocalDescription(answer)
    while pc_b.iceGatheringState != "complete":
        await asyncio.sleep(0.05)
    await pb.send({"type": "webrtc:answer", "call_id": call_id, "to": pb.other_id, "sdp": {"sdp": pc_b.localDescription.sdp, "type": pc_b.localDescription.type}})

    answer_msg = await pa.wait_for("webrtc:answer")
    await pc_a.setRemoteDescription(RTCSessionDescription(**answer_msg["sdp"]))

    await asyncio.wait_for(asyncio.gather(pa.connected.wait(), pb.connected.wait()), timeout=15)
    check("initial audio-only connection established", True)

    await asyncio.sleep(1.5)
    check("A has NOT received any video frames yet (audio-only call)", pa.frames["video"] == 0)
    check("B has NOT received any video frames yet (audio-only call)", pb.frames["video"] == 0)
    check("A IS receiving audio frames", pa.frames["audio"] > 0)
    check("B IS receiving audio frames", pb.frames["audio"] > 0)

    print("\nSwitching: A adds video mid-call, sends call:media-switch + renegotiation offer")
    await pa.send({"type": "call:media-switch", "call_id": call_id, "to": pa.other_id, "media": "video"})
    switch_msg = await pb.wait_for("call:media-switch")
    check("B received call:media-switch notice", switch_msg is not None and switch_msg.get("media") == "video")

    pc_a.addTrack(VideoStreamTrack())
    reneg_offer = await pc_a.createOffer()
    await pc_a.setLocalDescription(reneg_offer)
    while pc_a.iceGatheringState != "complete":
        await asyncio.sleep(0.05)
    await pa.send({"type": "webrtc:offer", "call_id": call_id, "to": pa.other_id, "sdp": {"sdp": pc_a.localDescription.sdp, "type": pc_a.localDescription.type}})

    reneg_offer_msg = await pb.wait_for("webrtc:offer")
    check("B received the renegotiation offer", reneg_offer_msg is not None)
    # This is the key branch: pc_b ALREADY EXISTS (this is a renegotiation,
    # not a fresh call) -- must NOT create a new peer connection or re-add
    # tracks, just answer on the existing one.
    await pc_b.setRemoteDescription(RTCSessionDescription(**reneg_offer_msg["sdp"]))
    reneg_answer = await pc_b.createAnswer()
    await pc_b.setLocalDescription(reneg_answer)
    while pc_b.iceGatheringState != "complete":
        await asyncio.sleep(0.05)
    await pb.send({"type": "webrtc:answer", "call_id": call_id, "to": pb.other_id, "sdp": {"sdp": pc_b.localDescription.sdp, "type": pc_b.localDescription.type}})

    reneg_answer_msg = await pa.wait_for("webrtc:answer")
    check("A received the renegotiation answer", reneg_answer_msg is not None)
    await pc_a.setRemoteDescription(RTCSessionDescription(**reneg_answer_msg["sdp"]))

    print("\nWaiting to confirm video frames now flow, and audio kept working the whole time...")
    await asyncio.sleep(2.0)
    check("B is now receiving video frames from A (post-renegotiation)", pb.frames["video"] > 0)
    check("B is still receiving audio frames (renegotiation didn't break existing audio)", pb.frames["audio"] > 15)
    check("connection is still 'connected' after renegotiation (no ICE restart needed)", pc_a.connectionState == "connected" and pc_b.connectionState == "connected")
    print(f"    Final frame counts -- A: {pa.frames}  B: {pb.frames}")

    print("\nSwitching back: A disables its video track (no renegotiation -- matches the shipped design)")
    await pa.send({"type": "call:media-switch", "call_id": call_id, "to": pa.other_id, "media": "audio"})
    switch_back_msg = await pb.wait_for("call:media-switch")
    check("B received call:media-switch back to audio", switch_back_msg is not None and switch_back_msg.get("media") == "audio")
    # aiortc doesn't implement RTCPeerConnection.removeTrack (unlike real
    # browsers / react-native-webrtc), which is exactly why the shipped
    # client code never calls it: downgrading just disables the local
    # track (sender.track.enabled = False equivalent), a much simpler and
    # more broadly-supported operation than remove+renegotiate. No further
    # protocol exchange happens on the wire for a downgrade, so there's
    # nothing more to validate here against the server.

    await pc_a.close(); await pc_b.close()
    await pa.ws.close(); await pb.ws.close()

    print("\n" + "=" * 60)
    print(f"RESULT: {len(results['pass'])} passed, {len(results['fail'])} failed")
    if results["fail"]:
        print("Failed:", results["fail"])
    print("=" * 60)
    sys.exit(1 if results["fail"] else 0)


asyncio.run(main())
