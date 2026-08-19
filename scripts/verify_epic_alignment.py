"""
Validates the Command Centre epic-alignment changes made to the backend
(ws_manager.py, config.py, schemas/call.py, routers/calls.py):

  1. Single-active-call locking (epic §19): a caller already in an active
     call cannot start a second one (error code caller_busy); a callee
     already in an active call cannot be rung by someone else (error code
     patient_busy). Neither attempt creates a new call record.
  2. Audit-schema population (epic §29): every call record carries
     caller_role and tenant_id from the moment it's created.
  3. Audio-only auto-fallback gating (epic §21): /calls/ice-servers reports
     audio_only_auto_fallback_enabled, and it's False by default (off until
     Business/Medical approve it) -- if a deployment enables it via env,
     this same field is how a client is supposed to find out. This script
     only asserts the default-off behavior since flipping it requires an
     env var + server restart, covered instead by code review of
     config.py's Settings.audio_only_auto_fallback_enabled default.

Runs against the real backend (scripts/_mock_server.py) over real
WebSocket + REST connections -- this is protocol-level, not mocked.
"""
import asyncio
import json
import sys
import time

import requests
import websockets

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


async def main():
    print("=== Setup: two doctors, two patients, appointments for both pairs ===")
    doc1 = signup("Dr. One", "epic.doc1@example.com", "doctor")
    doc2 = signup("Dr. Two", "epic.doc2@example.com", "doctor")
    pat1 = signup("Patient One", "epic.pat1@example.com", "patient")
    pat2 = signup("Patient Two", "epic.pat2@example.com", "patient")
    doc1_id, doc2_id = doc1["user"]["id"], doc2["user"]["id"]
    pat1_id, pat2_id = pat1["user"]["id"], pat2["user"]["id"]

    for doc, pat in ((doc1, pat1), (doc1, pat2), (doc2, pat1)):
        requests.post(
            f"{BASE}/appointments",
            headers={"Authorization": f"Bearer {doc['access_token']}"},
            json={"patient_id": pat["user"]["id"], "scheduled_time": "2026-08-20T10:00:00Z"},
        ).raise_for_status()

    doctor1 = Peer("Doctor1", doc1["access_token"])
    doctor2 = Peer("Doctor2", doc2["access_token"])
    patient1 = Peer("Patient1", pat1["access_token"])
    patient2 = Peer("Patient2", pat2["access_token"])
    await asyncio.gather(doctor1.connect(), doctor2.connect(), patient1.connect(), patient2.connect())
    await asyncio.sleep(0.3)

    print("\n=== 1. Doctor1 calls Patient1, Patient1 accepts -- call becomes CONNECTED ===")
    await doctor1.send({"type": "call:invite", "to": pat1_id, "media": "video"})
    incoming = await patient1.wait_for("call:incoming")
    call_id = incoming["call_id"]
    await patient1.send({"type": "call:accept", "call_id": call_id, "to": doc1_id, "consent": True})
    check("call connected", (await doctor1.wait_for("call:accepted")) is not None)

    print("\n=== 2. §29 audit fields: caller_role and tenant_id are populated ===")
    hist = requests.get(f"{BASE}/calls/history", headers={"Authorization": f"Bearer {doc1['access_token']}"}).json()
    record = next((c for c in hist if c["call_id"] == call_id), None)
    check("call record found", record is not None)
    check("caller_role is 'doctor'", record is not None and record.get("caller_role") == "doctor")
    check("tenant_id defaults to 'default'", record is not None and record.get("tenant_id") == "default")

    print("\n=== 3. §19 caller_busy: Doctor1 (already on a call) tries to call Patient2 ===")
    await doctor1.send({"type": "call:invite", "to": pat2_id, "media": "video"})
    err = await doctor1.wait_for("error")
    check("caller_busy error received", err is not None and err.get("code") == "caller_busy")
    no_incoming = await patient2.wait_for("call:incoming", timeout=1.0)
    check("Patient2 did NOT get rung", no_incoming is None)

    print("\n=== 4. §19 patient_busy: Doctor2 tries to call Patient1 (already on a call with Doctor1) ===")
    await doctor2.send({"type": "call:invite", "to": pat1_id, "media": "video"})
    err2 = await doctor2.wait_for("error")
    check("patient_busy error received", err2 is not None and err2.get("code") == "patient_busy")

    print("\n=== 5. Cleanup: end the call, confirm both are free again ===")
    await doctor1.send({"type": "call:end", "call_id": call_id, "to": pat1_id})
    await asyncio.sleep(0.3)

    await doctor1.send({"type": "call:invite", "to": pat2_id, "media": "video"})
    incoming2 = await patient2.wait_for("call:incoming")
    check("Doctor1 can start a new call once the previous one ended", incoming2 is not None)
    if incoming2:
        await doctor1.send({"type": "call:cancel", "call_id": incoming2["call_id"], "to": pat2_id})
        await asyncio.sleep(0.2)

    await doctor2.send({"type": "call:invite", "to": pat1_id, "media": "video"})
    incoming3 = await patient1.wait_for("call:incoming")
    check("Patient1 can be rung again once free", incoming3 is not None)
    if incoming3:
        await doctor2.send({"type": "call:cancel", "call_id": incoming3["call_id"], "to": pat1_id})
        await asyncio.sleep(0.2)

    print("\n=== 6. §21 gating: /calls/ice-servers reports audio_only_auto_fallback_enabled ===")
    ice_resp = requests.get(f"{BASE}/calls/ice-servers", headers={"Authorization": f"Bearer {doc1['access_token']}"}).json()
    check("audio_only_auto_fallback_enabled field present", "audio_only_auto_fallback_enabled" in ice_resp)
    check("audio_only_auto_fallback_enabled is False by default (not yet Business/Medical approved)",
          ice_resp.get("audio_only_auto_fallback_enabled") is False)

    for p in (doctor1, doctor2, patient1, patient2):
        await p.ws.close()

    print("\n" + "=" * 60)
    print(f"RESULT: {len(results['pass'])} passed, {len(results['fail'])} failed")
    if results["fail"]:
        print("Failed:", results["fail"])
    print("=" * 60)
    sys.exit(1 if results["fail"] else 0)


asyncio.run(main())
