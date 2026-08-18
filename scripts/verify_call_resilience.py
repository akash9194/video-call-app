"""
Validates the network-resilience + stale-message hardening added to
ws_manager.py:

  1. Ringing timeout: a call nobody answers auto-expires -- caller gets
     call:timeout, callee's ringing UI is told to dismiss (call:cancelled,
     reason=timeout), and the call record is marked 'missed' rather than
     staying 'ringing' forever.
  2. Stale/replayed messages: a signaling message (call:accept, webrtc:*)
     for a call_id that's already ended is ignored rather than acted on.
  3. Reconnection: if the active callee's WebSocket drops mid-call, the
     caller is told (call:peer-disconnected) but the call is NOT ended
     immediately. If that device reconnects with the SAME device_id within
     the grace period, the caller is told call:peer-reconnected and
     signaling for the call still routes correctly to the reconnected
     device (proving the server re-associated it, not just tolerated it).
  4. If the disconnected device does NOT come back within the grace
     period, the call is cleanly ended (call:ended, reason=peer_disconnected)
     instead of hanging forever on the other side.

Needs RINGING_TIMEOUT_SECONDS and DISCONNECT_GRACE_SECONDS set low in the
server's environment (see the shell command used to run this) -- otherwise
this would have to sleep 45+30 real seconds per the production defaults.

Runs against the real backend (scripts/_mock_server.py) over real
WebSocket + REST connections -- this is protocol-level, not mocked.
"""
import asyncio
import json
import os
import sys
import time

import requests
import websockets

BASE = "http://127.0.0.1:8123"
WS_BASE = "ws://127.0.0.1:8123"
RINGING_TIMEOUT = int(os.environ.get("RINGING_TIMEOUT_SECONDS", "45"))
DISCONNECT_GRACE = int(os.environ.get("DISCONNECT_GRACE_SECONDS", "30"))
results = {"pass": [], "fail": []}


def check(name, cond):
    (results["pass"] if cond else results["fail"]).append(name)
    print(("  [PASS] " if cond else "  [FAIL] ") + name)


def signup(name, email, role):
    r = requests.post(f"{BASE}/auth/signup", json={"name": name, "email": email, "password": "testpass123", "role": role})
    r.raise_for_status()
    return r.json()


class Peer:
    def __init__(self, label, token, device_id=None):
        self.label = label
        self.token = token
        self.device_id = device_id or f"{label}-device"
        self.ws = None
        self.events = asyncio.Queue()
        self._task = None

    async def connect(self):
        self.ws = await websockets.connect(f"{WS_BASE}/ws/signaling?token={self.token}&device_id={self.device_id}")
        self._task = asyncio.create_task(self._loop())

    async def _loop(self):
        try:
            async for raw in self.ws:
                msg = json.loads(raw)
                print(f"    <{self.label}> {msg['type']} {msg}")
                await self.events.put(msg)
        except websockets.exceptions.ConnectionClosed:
            pass

    async def wait_for(self, t, timeout=5):
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
        await self.ws.send(json.dumps(msg))

    async def drop(self):
        """Simulate a network drop -- close without a clean call:end, same
        as a wifi cutout or the app being killed."""
        if self._task:
            self._task.cancel()
        await self.ws.close()


async def main():
    print(f"Using RINGING_TIMEOUT_SECONDS={RINGING_TIMEOUT}, DISCONNECT_GRACE_SECONDS={DISCONNECT_GRACE} (set low for this test run)")

    doc_acc = signup("Dr. Resilience", "dr.resilience@example.com", "doctor")
    pat_acc = signup("Patient Steady", "patient.steady@example.com", "patient")
    doctor_id = doc_acc["user"]["id"]
    patient_id = pat_acc["user"]["id"]
    requests.post(
        f"{BASE}/appointments",
        headers={"Authorization": f"Bearer {doc_acc['access_token']}"},
        json={"patient_id": patient_id, "scheduled_time": "2026-08-20T10:00:00Z"},
    ).raise_for_status()

    # ---------------------------------------------------------------
    # 1. Ringing timeout -- patient never answers.
    # ---------------------------------------------------------------
    print("\n=== 1. Ringing timeout (nobody answers) ===")
    doctor = Peer("Doctor", doc_acc["access_token"])
    patient = Peer("Patient", pat_acc["access_token"])
    await asyncio.gather(doctor.connect(), patient.connect())
    await asyncio.sleep(0.3)

    await doctor.send({"type": "call:invite", "to": patient_id, "media": "video"})
    incoming = await patient.wait_for("call:incoming")
    check("patient received call:incoming", incoming is not None)
    call_id = incoming["call_id"] if incoming else None

    timeout_msg = await doctor.wait_for("call:timeout", timeout=RINGING_TIMEOUT + 5)
    check("caller received call:timeout", timeout_msg is not None)
    check("call:timeout has the right call_id", timeout_msg is not None and timeout_msg.get("call_id") == call_id)

    cancelled_msg = await patient.wait_for("call:cancelled", timeout=2)
    check("callee received call:cancelled for the expired call", cancelled_msg is not None)
    check("call:cancelled reason is 'timeout'", cancelled_msg is not None and cancelled_msg.get("reason") == "timeout")

    hist = requests.get(f"{BASE}/calls/history", headers={"Authorization": f"Bearer {doc_acc['access_token']}"}).json()
    record = next((c for c in hist if c["call_id"] == call_id), None)
    check("expired call record status is 'missed'", record is not None and record["status"] == "missed")

    # ---------------------------------------------------------------
    # 2. Stale/replayed message for a call that's already over.
    # ---------------------------------------------------------------
    print("\n=== 2. Stale/replayed message rejection ===")
    # The call above is fully cleared server-side now (timed out). Replay a
    # call:accept and a webrtc:offer for that dead call_id -- both must be
    # silently ignored: no message should reach the other side, and the
    # call record must NOT flip back to 'active'.
    await patient.send({"type": "call:accept", "call_id": call_id, "to": doctor_id, "consent": True})
    stray_accepted = await doctor.wait_for("call:accepted", timeout=1.5)
    check("stale call:accept for a dead call_id produced no call:accepted", stray_accepted is None)

    await doctor.send({"type": "webrtc:offer", "call_id": call_id, "to": patient_id, "sdp": {"type": "offer", "sdp": "stale"}})
    stray_offer = await patient.wait_for("webrtc:offer", timeout=1.5)
    check("stale webrtc:offer for a dead call_id was not relayed", stray_offer is None)

    hist2 = requests.get(f"{BASE}/calls/history", headers={"Authorization": f"Bearer {doc_acc['access_token']}"}).json()
    record2 = next((c for c in hist2 if c["call_id"] == call_id), None)
    check("dead call record is still 'missed', not resurrected to 'active'", record2 is not None and record2["status"] == "missed")

    await doctor.ws.close()
    await patient.ws.close()

    # ---------------------------------------------------------------
    # 3. Reconnection: callee's device drops mid-call, comes back in time.
    # ---------------------------------------------------------------
    print("\n=== 3. Reconnect within the grace period ===")
    doctor2 = Peer("Doctor2", doc_acc["access_token"])
    patient2 = Peer("Patient2", pat_acc["access_token"], device_id="patient-phone")
    await asyncio.gather(doctor2.connect(), patient2.connect())
    await asyncio.sleep(0.3)

    await doctor2.send({"type": "call:invite", "to": patient_id, "media": "video"})
    incoming2 = await patient2.wait_for("call:incoming")
    call_id2 = incoming2["call_id"]
    await patient2.send({"type": "call:accept", "call_id": call_id2, "to": doctor_id, "consent": True})
    accepted2 = await doctor2.wait_for("call:accepted")
    check("call became active", accepted2 is not None)

    print("  -- simulating the patient's connection dropping (no clean call:end) --")
    await patient2.drop()
    peer_disconnected = await doctor2.wait_for("call:peer-disconnected", timeout=3)
    check("caller was told call:peer-disconnected", peer_disconnected is not None)
    check("call:peer-disconnected has the right call_id", peer_disconnected is not None and peer_disconnected.get("call_id") == call_id2)

    print("  -- patient reconnects with the SAME device_id, inside the grace period --")
    patient2b = Peer("Patient2-reconnected", pat_acc["access_token"], device_id="patient-phone")
    await patient2b.connect()
    await asyncio.sleep(0.3)

    peer_reconnected = await doctor2.wait_for("call:peer-reconnected", timeout=DISCONNECT_GRACE + 3)
    check("caller was told call:peer-reconnected", peer_reconnected is not None)

    # Prove the call is genuinely still routable to the reconnected device,
    # not just that a notification fired -- send a real signaling message
    # and confirm it reaches the reconnected connection.
    await doctor2.send({"type": "call:media-switch", "call_id": call_id2, "to": patient_id, "media": "audio"})
    switch_msg = await patient2b.wait_for("call:media-switch", timeout=3)
    check("signaling still routes to the reconnected device after recovery", switch_msg is not None)

    hist3 = requests.get(f"{BASE}/calls/history", headers={"Authorization": f"Bearer {doc_acc['access_token']}"}).json()
    record3 = next((c for c in hist3 if c["call_id"] == call_id2), None)
    check("recovered call is still 'active', not ended by the drop", record3 is not None and record3["status"] == "active")

    await doctor2.send({"type": "call:end", "call_id": call_id2, "to": patient_id})
    await asyncio.sleep(0.2)
    await doctor2.ws.close()
    await patient2b.ws.close()

    # ---------------------------------------------------------------
    # 4. Disconnect WITHOUT reconnecting -- call must cleanly auto-end.
    # ---------------------------------------------------------------
    print("\n=== 4. Disconnect that never recovers ===")
    doctor3 = Peer("Doctor3", doc_acc["access_token"])
    patient3 = Peer("Patient3", pat_acc["access_token"], device_id="patient-tablet")
    await asyncio.gather(doctor3.connect(), patient3.connect())
    await asyncio.sleep(0.3)

    await doctor3.send({"type": "call:invite", "to": patient_id, "media": "audio"})
    incoming3 = await patient3.wait_for("call:incoming")
    call_id3 = incoming3["call_id"]
    await patient3.send({"type": "call:accept", "call_id": call_id3, "to": doctor_id, "consent": True})
    await doctor3.wait_for("call:accepted")

    print("  -- doctor's connection drops and never comes back --")
    await doctor3.drop()
    patient_saw_disconnect = await patient3.wait_for("call:peer-disconnected", timeout=3)
    check("callee was told call:peer-disconnected", patient_saw_disconnect is not None)

    ended_msg = await patient3.wait_for("call:ended", timeout=DISCONNECT_GRACE + 5)
    check("call was auto-ended after the grace period expired", ended_msg is not None)
    check("call:ended reason is 'peer_disconnected'", ended_msg is not None and ended_msg.get("reason") == "peer_disconnected")

    hist4 = requests.get(f"{BASE}/calls/history", headers={"Authorization": f"Bearer {pat_acc['access_token']}"}).json()
    record4 = next((c for c in hist4 if c["call_id"] == call_id3), None)
    check("abandoned call record status is 'ended'", record4 is not None and record4["status"] == "ended")

    await patient3.ws.close()

    print("\n" + "=" * 60)
    print(f"RESULT: {len(results['pass'])} passed, {len(results['fail'])} failed")
    if results["fail"]:
        print("Failed:", results["fail"])
    print("=" * 60)
    sys.exit(1 if results["fail"] else 0)


asyncio.run(main())
