"""
Validates epic §6/§28's tenant access-rule enforcement: users only see
other users in their own tenant (GET /users), a doctor can't schedule an
appointment with a patient in a different tenant (POST /appointments),
and call:invite independently refuses a cross-tenant callee even if a
bad appointment record somehow already links them (defense-in-depth, not
just relying on the appointment gate) -- see ws_manager.py's call:invite
handler for why that second check exists.

Also confirms the change is backward-compatible: a user who signs up
without specifying a tenant_id lands in "default", same as every
single-tenant deployment before this feature existed, and a normal same-
tenant call flow still works end to end (the enforcement shouldn't have
broken anything that used to work).

Runs against the real backend (scripts/_mock_server.py), which exposes a
test-only /_test/seed-cross-tenant-appointment endpoint for the defense-
in-depth check -- see that file's comment for why the normal API can't
produce the bad state being tested against.
"""
import asyncio
import json
import os
import sys

import requests
import websockets

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(SCRIPT_DIR, "..", "backend"))

BASE = "http://127.0.0.1:8123"
WS_BASE = "ws://127.0.0.1:8123"
results = {"pass": [], "fail": []}


def check(name, cond):
    (results["pass"] if cond else results["fail"]).append(name)
    print(("  [PASS] " if cond else "  [FAIL] ") + name)


def signup(name, email, role, tenant_id=None):
    payload = {"name": name, "email": email, "password": "testpass123", "role": role}
    if tenant_id is not None:
        payload["tenant_id"] = tenant_id
    r = requests.post(f"{BASE}/auth/signup", json=payload)
    r.raise_for_status()
    return r.json()


def auth_header(acc):
    return {"Authorization": f"Bearer {acc['access_token']}"}


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

    async def wait_for(self, wanted_type, timeout=5):
        import time
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                msg = await asyncio.wait_for(self.events.get(), timeout=max(0.1, deadline - time.time()))
            except asyncio.TimeoutError:
                break
            if msg["type"] == wanted_type:
                return msg
        return None

    async def send(self, msg):
        await self.ws.send(json.dumps(msg))


async def main():
    print("=== 1. Signup across three tenants (clinic-a, clinic-b, and unspecified/default) ===")
    doc_a = signup("Dr. A", "tenant.doc.a@example.com", "doctor", tenant_id="clinic-a")
    pat_a = signup("Patient A", "tenant.pat.a@example.com", "patient", tenant_id="clinic-a")
    doc_b = signup("Dr. B", "tenant.doc.b@example.com", "doctor", tenant_id="clinic-b")
    pat_b = signup("Patient B", "tenant.pat.b@example.com", "patient", tenant_id="clinic-b")
    doc_default = signup("Dr. Default", "tenant.doc.default@example.com", "doctor")  # no tenant_id -> "default"
    pat_default = signup("Patient Default", "tenant.pat.default@example.com", "patient")

    check("doctor A landed in clinic-a", doc_a["user"]["tenant_id"] == "clinic-a")
    check("patient A landed in clinic-a", pat_a["user"]["tenant_id"] == "clinic-a")
    check("doctor B landed in clinic-b", doc_b["user"]["tenant_id"] == "clinic-b")
    check("unspecified tenant_id defaults to 'default'", doc_default["user"]["tenant_id"] == "default")
    check("unspecified tenant_id defaults to 'default' (patient too)", pat_default["user"]["tenant_id"] == "default")

    print("\n=== 2. GET /users is scoped to the caller's own tenant ===")
    users_seen_by_doc_a = requests.get(f"{BASE}/users", headers=auth_header(doc_a)).json()
    names_seen = {u["name"] for u in users_seen_by_doc_a}
    check("doctor A sees patient A (same tenant)", "Patient A" in names_seen)
    check("doctor A does NOT see patient B (different tenant)", "Patient B" not in names_seen)
    check("doctor A does NOT see doctor B (different tenant)", "Dr. B" not in names_seen)
    check("doctor A does NOT see the default-tenant users", "Patient Default" not in names_seen and "Dr. Default" not in names_seen)

    users_seen_by_default_doc = requests.get(f"{BASE}/users", headers=auth_header(doc_default)).json()
    names_seen_default = {u["name"] for u in users_seen_by_default_doc}
    check("default-tenant doctor sees the default-tenant patient", "Patient Default" in names_seen_default)
    check("default-tenant doctor does NOT see clinic-a/b users", "Patient A" not in names_seen_default and "Patient B" not in names_seen_default)

    print("\n=== 3. POST /appointments refuses a cross-tenant patient ===")
    cross_tenant_resp = requests.post(
        f"{BASE}/appointments",
        headers=auth_header(doc_a),
        json={"patient_id": pat_b["user"]["id"], "scheduled_time": "2026-09-01T10:00:00Z"},
    )
    check("cross-tenant appointment (doctor A -> patient B) rejected with 404", cross_tenant_resp.status_code == 404)

    same_tenant_resp = requests.post(
        f"{BASE}/appointments",
        headers=auth_header(doc_a),
        json={"patient_id": pat_a["user"]["id"], "scheduled_time": "2026-09-01T10:00:00Z"},
    )
    check("same-tenant appointment (doctor A -> patient A) succeeds", same_tenant_resp.status_code == 201)
    check("the created appointment carries the doctor's tenant_id", same_tenant_resp.json().get("tenant_id") == "clinic-a")

    print("\n=== 4. call:invite works normally within a tenant, and stamps the real tenant_id on the call ===")
    doctor_a_peer = Peer("DoctorA", doc_a["access_token"])
    patient_a_peer = Peer("PatientA", pat_a["access_token"])
    await asyncio.gather(doctor_a_peer.connect(), patient_a_peer.connect())
    await asyncio.sleep(0.3)

    await doctor_a_peer.send({"type": "call:invite", "to": pat_a["user"]["id"], "media": "video"})
    incoming = await patient_a_peer.wait_for("call:incoming")
    check("same-tenant call:invite reaches the callee normally", incoming is not None)

    if incoming:
        call_id = incoming["call_id"]
        history = requests.get(f"{BASE}/calls/history", headers=auth_header(doc_a)).json()
        this_call = next((c for c in history if c["call_id"] == call_id), None)
        check("the call record's tenant_id is the caller's real tenant, not a hardcoded default", this_call is not None and this_call.get("tenant_id") == "clinic-a")
        await doctor_a_peer.send({"type": "call:cancel", "call_id": call_id, "to": pat_a["user"]["id"]})
        await asyncio.sleep(0.2)

    print("\n=== 5. Defense-in-depth: call:invite rejects a cross-tenant callee even with a (bad) appointment record already linking them ===")
    seed_resp = requests.post(
        f"{BASE}/_test/seed-cross-tenant-appointment",
        json={
            "doctor_id": doc_a["user"]["id"],
            "patient_id": pat_b["user"]["id"],
            "doctor_name": "Dr. A",
            "patient_name": "Patient B",
            "tenant_id": "clinic-a",  # the appointment claims clinic-a, but patient B is actually clinic-b
        },
    )
    check("test-only cross-tenant appointment seeded", seed_resp.status_code == 200)

    await doctor_a_peer.send({"type": "call:invite", "to": pat_b["user"]["id"], "media": "video"})
    error_msg = None
    try:
        for _ in range(5):
            msg = await asyncio.wait_for(doctor_a_peer.events.get(), timeout=3)
            if msg["type"] == "error":
                error_msg = msg
                break
    except asyncio.TimeoutError:
        pass
    check("cross-tenant call:invite rejected despite the seeded appointment record", error_msg is not None and error_msg.get("code") == "invalid_callee")

    await doctor_a_peer.ws.close()
    await patient_a_peer.ws.close()

    print("\n" + "=" * 60)
    print(f"RESULT: {len(results['pass'])} passed, {len(results['fail'])} failed")
    if results["fail"]:
        print("Failed:", results["fail"])
    print("=" * 60)
    sys.exit(1 if results["fail"] else 0)


asyncio.run(main())
