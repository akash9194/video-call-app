"""
Validates epic §11/§3's "iLive Care Team"-only identity option
(Settings.mask_clinician_identity_from_patient): off by default (patient
sees the real clinician name, unchanged behavior), and when turned on via
env, the callee's call:incoming carries "iLive Care Team" instead of the
doctor's real name -- while the underlying call record still keeps the
real caller_id (this masks a display string sent to the patient, not the
audit trail).

Runs against scripts/_mock_server.py twice, in two separate subprocesses
(one per env var value) since Settings is read once at process start --
the caller passes MOCK_SERVER_PORT and MASK_CLINICIAN_IDENTITY_FROM_PATIENT
via env before each subprocess launch.
"""
import asyncio
import json
import os
import sys

import requests
import websockets

results = {"pass": [], "fail": []}


def check(name, cond):
    (results["pass"] if cond else results["fail"]).append(name)
    print(("  [PASS] " if cond else "  [FAIL] ") + name)


def signup(base, name, email, role):
    r = requests.post(f"{base}/auth/signup", json={"name": name, "email": email, "password": "testpass123", "role": role})
    r.raise_for_status()
    return r.json()


async def run_scenario(port, expect_masked, label):
    base = f"http://127.0.0.1:{port}"
    ws_base = f"ws://127.0.0.1:{port}"

    doc = signup(base, "Dr. Identity", f"identity.doc.{port}@example.com", "doctor")
    pat = signup(base, "Patient Identity", f"identity.pat.{port}@example.com", "patient")
    doc_id, pat_id = doc["user"]["id"], pat["user"]["id"]
    requests.post(
        f"{base}/appointments",
        headers={"Authorization": f"Bearer {doc['access_token']}"},
        json={"patient_id": pat_id, "scheduled_time": "2026-09-01T10:00:00Z"},
    ).raise_for_status()

    doctor_ws = await websockets.connect(f"{ws_base}/ws/signaling?token={doc['access_token']}&device_id=identity-doc")
    patient_ws = await websockets.connect(f"{ws_base}/ws/signaling?token={pat['access_token']}&device_id=identity-pat")
    await asyncio.sleep(0.3)

    await doctor_ws.send(json.dumps({"type": "call:invite", "to": pat_id, "media": "video"}))

    incoming = None
    try:
        for _ in range(5):
            raw = await asyncio.wait_for(patient_ws.recv(), timeout=3)
            msg = json.loads(raw)
            if msg["type"] == "call:incoming":
                incoming = msg
                break
    except asyncio.TimeoutError:
        pass

    check(f"[{label}] call:incoming received", incoming is not None)
    if incoming:
        if expect_masked:
            check(f"[{label}] from_name is masked to 'iLive Care Team'", incoming["from_name"] == "iLive Care Team")
        else:
            check(f"[{label}] from_name is the real doctor name (default, unmasked)", incoming["from_name"] == "Dr. Identity")
        check(f"[{label}] 'from' (the actual caller id) is never masked, only the display name", incoming["from"] == doc_id)

        # Regardless of masking, the persisted call record must still hold
        # the real caller_id -- this is a display-layer masking only, not
        # a redaction of the audit trail.
        call_id = incoming["call_id"]
        history = requests.get(f"{base}/calls/history", headers={"Authorization": f"Bearer {doc['access_token']}"}).json()
        this_call = next((c for c in history if c["call_id"] == call_id), None)
        check(f"[{label}] the call record's caller_id is the real doctor id, unmasked", this_call is not None and this_call.get("caller_id") == doc_id)

    await doctor_ws.close()
    await patient_ws.close()


async def main():
    port = sys.argv[1]
    expect_masked = sys.argv[2] == "true"
    label = "masked" if expect_masked else "default/unmasked"
    print(f"=== Scenario: {label} (port {port}) ===")
    await run_scenario(port, expect_masked, label)

    print("\n" + "=" * 60)
    print(f"RESULT: {len(results['pass'])} passed, {len(results['fail'])} failed")
    if results["fail"]:
        print("Failed:", results["fail"])
    print("=" * 60)
    sys.exit(1 if results["fail"] else 0)


asyncio.run(main())
