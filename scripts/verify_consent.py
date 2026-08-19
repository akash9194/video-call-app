"""
Validates the patient-consent gate added to ws_manager.py's call:accept
handler (risk register item: "No consent-to-telehealth / consent-to-record
capture exists today" -- P0 blocker):

  1. A patient trying to accept a call WITHOUT consent:true is rejected
     server-side with error code consent_required, and the call stays
     ringing (not silently killed) so the client can show the consent step
     and retry.
  2. The same patient accepting WITH consent:true succeeds normally.
  3. The persisted call record has consent_given=True and a consent_at
     timestamp -- the audit trail this exists for.

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
    def __init__(self, label, token):
        self.label = label
        self.token = token
        self.ws = None
        self.events = asyncio.Queue()

    async def connect(self):
        self.ws = await websockets.connect(f"{WS_BASE}/ws/signaling?token={self.token}&device_id={self.label}-device")
        asyncio.create_task(self._loop())

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
    print("Setup: doctor + patient, with a scheduled appointment between them")
    doc_acc = signup("Dr. Grey", "dr.grey.consent@example.com", "doctor")
    pat_acc = signup("Patient Yang", "patient.yang.consent@example.com", "patient")
    doctor_id = doc_acc["user"]["id"]
    patient_id = pat_acc["user"]["id"]

    appt_resp = requests.post(
        f"{BASE}/appointments",
        headers={"Authorization": f"Bearer {doc_acc['access_token']}"},
        json={"patient_id": patient_id, "scheduled_time": "2026-08-20T10:00:00Z", "notes": "Consent test"},
    )
    appt_resp.raise_for_status()

    doctor = Peer("Doctor", doc_acc["access_token"])
    patient = Peer("Patient", pat_acc["access_token"])
    await asyncio.gather(doctor.connect(), patient.connect())
    await asyncio.sleep(0.3)

    print("\nStep 1: doctor calls the patient")
    await doctor.send({"type": "call:invite", "to": patient_id, "media": "video"})
    incoming = await patient.wait_for("call:incoming")
    check("patient received call:incoming", incoming is not None)
    call_id = incoming["call_id"]

    print("\nStep 2: patient tries to accept WITHOUT consent -- must be rejected")
    await patient.send({"type": "call:accept", "call_id": call_id, "to": doctor_id})
    err = await patient.wait_for("error")
    check("accept without consent was rejected", err is not None)
    check("error code is consent_required", err is not None and err.get("code") == "consent_required")
    no_accept = await doctor.wait_for("call:accepted", timeout=1.0)
    check("doctor did NOT get call:accepted from the consent-less attempt", no_accept is None)

    hist_mid = requests.get(f"{BASE}/calls/history", headers={"Authorization": f"Bearer {doc_acc['access_token']}"}).json()
    record_mid = next((c for c in hist_mid if c["call_id"] == call_id), None)
    check("call record still shows status 'RINGING' after the rejected accept", record_mid is not None and record_mid["status"] == "RINGING")
    check("call record still shows consent_given False", record_mid is not None and record_mid["consent_given"] is False)
    check("rejected accept incremented permission_failures", record_mid is not None and record_mid.get("permission_failures") == 1)

    print("\nStep 3: patient retries with consent:true -- must succeed")
    await patient.send({"type": "call:accept", "call_id": call_id, "to": doctor_id, "consent": True})
    accepted = await doctor.wait_for("call:accepted")
    check("doctor received call:accepted after consent given", accepted is not None)

    print("\nStep 4: verify the persisted call record has consent_given + consent_at")
    hist = requests.get(f"{BASE}/calls/history", headers={"Authorization": f"Bearer {doc_acc['access_token']}"}).json()
    record = next((c for c in hist if c["call_id"] == call_id), None)
    check("call record found in history", record is not None)
    check("call record status is 'CONNECTED'", record is not None and record["status"] == "CONNECTED")
    check("call record consent_given is True", record is not None and record["consent_given"] is True)
    check("call record has a consent_at timestamp", record is not None and record.get("consent_at") is not None)
    check("call record has an answered_at timestamp", record is not None and record.get("answered_at") is not None)

    # Clean up.
    await doctor.send({"type": "call:end", "call_id": call_id, "to": patient_id})
    await asyncio.sleep(0.2)
    for p in (doctor, patient):
        await p.ws.close()

    print("\n" + "=" * 60)
    print(f"RESULT: {len(results['pass'])} passed, {len(results['fail'])} failed")
    if results["fail"]:
        print("Failed:", results["fail"])
    print("=" * 60)
    sys.exit(1 if results["fail"] else 0)


asyncio.run(main())
