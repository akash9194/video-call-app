"""
Validates the doctor-only-initiation + appointment-linkage enforcement
added to ws_manager.py's call:invite handler:

  1. A patient trying to call anyone is rejected server-side (not just
     hidden by the UI).
  2. A doctor calling a patient they have NO active appointment with is
     rejected.
  3. A doctor calling another doctor (invalid callee role) is rejected.
  4. Once a real appointment exists (created via the new /appointments
     API), the SAME doctor calling the SAME patient succeeds and
     call:incoming is delivered normally.

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

    def send(self, msg):
        return self.ws.send(json.dumps(msg))


async def main():
    print("Setup: one doctor, one patient, one second doctor (for the invalid-callee case)")
    doc_acc = signup("Dr. House", "dr.house@example.com", "doctor")
    pat_acc = signup("Patient Wilson", "patient.wilson@example.com", "patient")
    doc2_acc = signup("Dr. Cuddy", "dr.cuddy@example.com", "doctor")

    doctor_id = doc_acc["user"]["id"]
    patient_id = pat_acc["user"]["id"]
    doctor2_id = doc2_acc["user"]["id"]

    doctor = Peer("Doctor", doc_acc["access_token"])
    patient = Peer("Patient", pat_acc["access_token"])
    doctor2 = Peer("Doctor2", doc2_acc["access_token"])
    await asyncio.gather(doctor.connect(), patient.connect(), doctor2.connect())
    await asyncio.sleep(0.3)

    # --- 1. Patient tries to initiate a call -- must be rejected server-side ---
    print("\nStep 1: patient tries to call the doctor directly over the WebSocket (bypassing any UI restriction)")
    await patient.send({"type": "call:invite", "to": doctor_id, "media": "video"})
    err = await patient.wait_for("error")
    check("patient's call attempt was rejected with an error", err is not None)
    check("error code is not_authorized_to_call", err is not None and err.get("code") == "not_authorized_to_call")
    no_incoming = await doctor.wait_for("call:incoming", timeout=1.0)
    check("doctor did NOT receive call:incoming from the patient's attempt", no_incoming is None)

    # --- 2. Doctor calls patient with NO appointment yet ---
    print("\nStep 2: doctor calls the patient, but no appointment exists yet")
    await doctor.send({"type": "call:invite", "to": patient_id, "media": "video"})
    err2 = await doctor.wait_for("error")
    check("doctor's call was rejected (no appointment)", err2 is not None)
    check("error code is no_active_appointment", err2 is not None and err2.get("code") == "no_active_appointment")
    no_incoming2 = await patient.wait_for("call:incoming", timeout=1.0)
    check("patient did NOT receive call:incoming (no appointment)", no_incoming2 is None)

    # --- 3. Doctor calls another doctor (invalid callee role) ---
    print("\nStep 3: doctor tries to call another doctor")
    await doctor.send({"type": "call:invite", "to": doctor2_id, "media": "video"})
    err3 = await doctor.wait_for("error")
    check("calling another doctor was rejected", err3 is not None)
    check("error code is invalid_callee", err3 is not None and err3.get("code") == "invalid_callee")

    # --- 4. Create a real appointment via the new REST API, then retry the call ---
    print("\nStep 4: doctor creates an appointment with the patient via POST /appointments")
    appt_resp = requests.post(
        f"{BASE}/appointments",
        headers={"Authorization": f"Bearer {doc_acc['access_token']}"},
        json={"patient_id": patient_id, "scheduled_time": "2026-08-20T10:00:00Z", "notes": "Follow-up"},
    )
    check("appointment created (201)", appt_resp.status_code == 201)
    appt = appt_resp.json()
    check("appointment links the right doctor and patient", appt.get("doctor_id") == doctor_id and appt.get("patient_id") == patient_id)
    check("appointment status is 'scheduled'", appt.get("status") == "scheduled")

    # Sanity: a patient must NOT be able to create an appointment.
    forbidden_resp = requests.post(
        f"{BASE}/appointments",
        headers={"Authorization": f"Bearer {pat_acc['access_token']}"},
        json={"patient_id": patient_id, "scheduled_time": "2026-08-20T10:00:00Z"},
    )
    check("patient creating an appointment is forbidden (403)", forbidden_resp.status_code == 403)

    print("\nStep 5: doctor calls the patient again -- should succeed now")
    await doctor.send({"type": "call:invite", "to": patient_id, "media": "video"})
    incoming = await patient.wait_for("call:incoming")
    check("patient received call:incoming this time", incoming is not None)
    check("call media is video", incoming is not None and incoming.get("media") == "video")
    stray_err = await doctor.wait_for("error", timeout=1.0)
    check("no error sent for the successful invite", stray_err is None)

    # Clean up: reject so we don't leave a ringing call hanging.
    if incoming:
        await patient.send({"type": "call:reject", "call_id": incoming["call_id"], "to": doctor_id})

    for p in (doctor, patient, doctor2):
        await p.ws.close()

    print("\n" + "=" * 60)
    print(f"RESULT: {len(results['pass'])} passed, {len(results['fail'])} failed")
    if results["fail"]:
        print("Failed:", results["fail"])
    print("=" * 60)
    sys.exit(1 if results["fail"] else 0)


asyncio.run(main())
