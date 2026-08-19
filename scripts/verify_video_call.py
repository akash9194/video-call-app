"""
Self-contained local check for the video calling feature.

Run this on your own machine (no MongoDB, no Docker, no phone/emulator
required):

    cd video-call-app
    python -m venv venv
    venv\\Scripts\\activate        (Windows)  |  source venv/bin/activate  (macOS/Linux)
    pip install -r scripts/requirements-selfcheck.txt
    python scripts/verify_video_call.py

What it does: starts the real backend/app code (MongoDB swapped for an
in-memory mock), then simulates two app instances -- a doctor (caller) and
a patient (callee) -- doing everything the real mobile app does: signup,
an appointment being scheduled (required before a call can be placed --
see the doctor-only-initiation rule enforced in ws_manager.py), login,
WebSocket signaling (call:invite/accept), a real WebRTC SDP offer/answer +
ICE negotiation (using aiortc, a real WebRTC stack), and then confirms
actual audio + video frames get decoded on both ends. Ends by hanging up
and checking the call record was saved correctly.

If you see "RESULT: 16 passed, 0 failed" at the end, the video calling
feature works on your machine.

Not covered by this check: the React Native UI/native modules, a real
device camera/mic, and TURN relay behavior (this all happens over
loopback, so only the direct peer-to-peer path is exercised).
"""
import asyncio
import json
import os
import subprocess
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE = "http://127.0.0.1:8123"
WS_BASE = "ws://127.0.0.1:8123"

results = {"pass": [], "fail": []}


def check(name, cond):
    if cond:
        results["pass"].append(name)
        print(f"  [PASS] {name}")
    else:
        results["fail"].append(name)
        print(f"  [FAIL] {name}")


def wait_for_server(timeout=20):
    import requests
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = requests.get(f"{BASE}/health", timeout=1)
            if r.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


async def run_call_simulation():
    import requests
    import websockets
    from aiortc import RTCPeerConnection, RTCSessionDescription
    from aiortc.mediastreams import AudioStreamTrack, VideoStreamTrack

    # Force ICE host candidates onto 127.0.0.1 so this check works
    # deterministically regardless of local network config (VPNs, no
    # active adapter, firewalled interfaces, etc). Both simulated peers
    # run on this same machine, so loopback is a valid path for them to
    # reach each other.
    import aioice.ice as _aioice_ice
    _aioice_ice.get_host_addresses = lambda use_ipv4, use_ipv6: (["127.0.0.1"] if use_ipv4 else [])

    def signup(name, email, role):
        r = requests.post(f"{BASE}/auth/signup", json={
            "name": name, "email": email, "password": "testpass123", "role": role
        })
        r.raise_for_status()
        return r.json()

    class Peer:
        def __init__(self, label, user, token):
            self.label = label
            self.user = user
            self.token = token
            self.ws = None
            self.pc = None
            self.call_id = None
            self.other_id = None
            self.events = asyncio.Queue()
            self.frames_received = {"audio": 0, "video": 0}
            self.connected_event = asyncio.Event()

        async def connect_ws(self):
            self.ws = await websockets.connect(f"{WS_BASE}/ws/signaling?token={self.token}")
            asyncio.create_task(self._recv_loop())

        async def _recv_loop(self):
            try:
                async for raw in self.ws:
                    msg = json.loads(raw)
                    print(f"    <{self.label} recv> {msg['type']}")
                    await self.events.put(msg)
            except websockets.exceptions.ConnectionClosed:
                pass

        async def wait_for(self, msg_type, timeout=10):
            deadline = time.time() + timeout
            while time.time() < deadline:
                try:
                    msg = await asyncio.wait_for(self.events.get(), timeout=max(0.1, deadline - time.time()))
                except asyncio.TimeoutError:
                    break
                if msg["type"] == msg_type:
                    return msg
            return None

        def send(self, message):
            return self.ws.send(json.dumps(message))

        def make_pc(self):
            pc = RTCPeerConnection()
            pc.addTrack(VideoStreamTrack())
            pc.addTrack(AudioStreamTrack())

            @pc.on("connectionstatechange")
            async def on_state():
                print(f"    [{self.label}] connectionState -> {pc.connectionState}")
                if pc.connectionState == "connected":
                    self.connected_event.set()

            @pc.on("track")
            def on_track(track):
                print(f"    [{self.label}] receiving remote {track.kind} track")
                asyncio.create_task(self._consume_track(track))

            self.pc = pc
            return pc

        async def _consume_track(self, track):
            try:
                for _ in range(15):
                    await track.recv()
                    self.frames_received[track.kind] += 1
            except Exception as e:
                print(f"    [{self.label}] track consume ended: {e}")

    print("1. Signing up a doctor and a patient (real /auth/signup, real password hashing + JWT)")
    a = signup("Dr. Local Check", "local.doctor@example.com", "doctor")
    b = signup("Local Check Patient", "local.patient@example.com", "patient")
    check("signup A (doctor)", "access_token" in a)
    check("signup B (patient)", "access_token" in b)

    peer_a = Peer("A/caller", a["user"], a["access_token"])
    peer_b = Peer("B/callee", b["user"], b["access_token"])
    peer_a.other_id = b["user"]["id"]
    peer_b.other_id = a["user"]["id"]

    print("\n1b. Doctor creates an appointment with the patient (required before call:invite is allowed)")
    appt = requests.post(
        f"{BASE}/appointments",
        headers={"Authorization": f"Bearer {a['access_token']}"},
        json={"patient_id": peer_a.other_id, "scheduled_time": "2026-08-20T10:00:00Z"},
    )
    check("appointment created", appt.status_code == 201)

    print("\n2. Opening WebSocket signaling connections for both users")
    await peer_a.connect_ws()
    await peer_b.connect_ws()
    await asyncio.sleep(0.5)

    print("\n3. A invites B to a call (call:invite)")
    await peer_a.send({"type": "call:invite", "to": peer_a.other_id})
    incoming = await peer_b.wait_for("call:incoming")
    check("B received call:incoming", incoming is not None)
    call_id = incoming["call_id"]
    peer_a.call_id = call_id
    peer_b.call_id = call_id

    print("\n4. B accepts the call (call:accept)")
    await peer_b.send({"type": "call:accept", "call_id": call_id, "to": peer_b.other_id, "consent": True})
    accepted = await peer_a.wait_for("call:accepted")
    check("A received call:accepted", accepted is not None)

    print("\n5. Real WebRTC negotiation: A creates offer with live audio+video tracks")
    pc_a = peer_a.make_pc()
    offer = await pc_a.createOffer()
    await pc_a.setLocalDescription(offer)
    while pc_a.iceGatheringState != "complete":
        await asyncio.sleep(0.05)
    await peer_a.send({
        "type": "webrtc:offer", "call_id": call_id, "to": peer_a.other_id,
        "sdp": {"sdp": pc_a.localDescription.sdp, "type": pc_a.localDescription.type},
    })
    offer_msg = await peer_b.wait_for("webrtc:offer")
    check("B received webrtc:offer", offer_msg is not None)

    print("\n6. B answers with its own live audio+video tracks")
    pc_b = peer_b.make_pc()
    await pc_b.setRemoteDescription(RTCSessionDescription(**offer_msg["sdp"]))
    answer = await pc_b.createAnswer()
    await pc_b.setLocalDescription(answer)
    while pc_b.iceGatheringState != "complete":
        await asyncio.sleep(0.05)
    await peer_b.send({
        "type": "webrtc:answer", "call_id": call_id, "to": peer_b.other_id,
        "sdp": {"sdp": pc_b.localDescription.sdp, "type": pc_b.localDescription.type},
    })
    answer_msg = await peer_a.wait_for("webrtc:answer")
    check("A received webrtc:answer", answer_msg is not None)
    await pc_a.setRemoteDescription(RTCSessionDescription(**answer_msg["sdp"]))

    print("\n7. Waiting for ICE/DTLS to actually connect both peer connections...")
    try:
        await asyncio.wait_for(
            asyncio.gather(peer_a.connected_event.wait(), peer_b.connected_event.wait()),
            timeout=15,
        )
        check("Both peer connections reached 'connected' state", True)
    except asyncio.TimeoutError:
        check("Both peer connections reached 'connected' state", False)

    print("\n8. Waiting to confirm real audio/video frames are being decoded on both ends...")
    await asyncio.sleep(2.0)
    check("A decoded video frames from B", peer_a.frames_received["video"] > 0)
    check("A decoded audio frames from B", peer_a.frames_received["audio"] > 0)
    check("B decoded video frames from A", peer_b.frames_received["video"] > 0)
    check("B decoded audio frames from A", peer_b.frames_received["audio"] > 0)
    print(f"    A received: {peer_a.frames_received}")
    print(f"    B received: {peer_b.frames_received}")

    print("\n9. A ends the call (call:end)")
    await peer_a.send({"type": "call:end", "call_id": call_id, "to": peer_a.other_id})
    ended = await peer_b.wait_for("call:ended")
    check("B received call:ended", ended is not None)

    print("\n10. Checking the call record was persisted correctly")
    hist = requests.get(f"{BASE}/calls/history", headers={"Authorization": f"Bearer {a['access_token']}"}).json()
    call_record = next((c for c in hist if c["call_id"] == call_id), None)
    check("Call record exists in history", call_record is not None)
    check("Call record status is 'ENDED'", call_record and call_record["status"] == "ENDED")
    check("Call record end_reason is 'CLINICIAN_ENDED' (caller hung up)", call_record and call_record["end_reason"] == "CLINICIAN_ENDED")
    check("Call record has a duration_seconds value", call_record and call_record["duration_seconds"] is not None)

    if pc_a:
        await pc_a.close()
    if pc_b:
        await pc_b.close()
    await peer_a.ws.close()
    await peer_b.ws.close()


def main():
    print("Starting backend (MongoDB mocked in-memory for this check)...")
    server = subprocess.Popen(
        [sys.executable, os.path.join(SCRIPT_DIR, "_mock_server.py")],
        stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
    )
    try:
        if not wait_for_server():
            print("Server did not start in time. Do you have the deps installed?")
            print("  pip install -r scripts/requirements-selfcheck.txt")
            sys.exit(2)
        print("Server is up.\n")
        asyncio.run(run_call_simulation())
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()

    print("\n" + "=" * 60)
    print(f"RESULT: {len(results['pass'])} passed, {len(results['fail'])} failed")
    if results["fail"]:
        print("Failed checks:", results["fail"])
    print("=" * 60)
    sys.exit(1 if results["fail"] else 0)


if __name__ == "__main__":
    main()
