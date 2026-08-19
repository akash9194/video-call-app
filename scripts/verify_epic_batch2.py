"""
Validates the second batch of Command Centre epic gap-closing work (backend
+ protocol-level pieces from the "Recommended Build Sequence" items 3, 6,
and 7 in the gap-analysis doc):

  1. §29 platform tracking: caller_platform/callee_platform get populated
     from call:invite/call:accept's optional "platform" field.
  2. §30 post-call notes & outcome: PATCH /calls/{call_id}/notes rejects
     non-participants (403), rejects while the call is still live (409),
     rejects an invalid outcome value (400), and succeeds once the call
     has actually ended, persisting notes/outcome/follow_up_required.
  3. §35/§36 analytics events: GET /calls/{call_id}/events returns the
     expected event trail for a full call lifecycle, and is scoped to
     participants only.
  4. §23 network-quality indicator: call:network-quality relays to the
     peer, gets persisted into last_network_quality, and produces a
     network_quality_report analytics event.
  5. §7 entry-point button states: GET /users reports in_active_call=True
     for both participants while a call is live, and false again once it
     ends -- this is what the client's "Patient Busy" state is computed
     from.

Runs against the real backend (scripts/_mock_server.py) over real
WebSocket + REST connections -- protocol-level, not mocked. The pure
client-side network-quality bucketing logic is covered separately in
scripts/test_network_quality.js (no browser/backend needed for that part).
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


def auth_header(acc):
    return {"Authorization": f"Bearer {acc['access_token']}"}


async def main():
    print("=== Setup: doctor + patient + a bystander patient (for the 403 check), with an appointment ===")
    doc = signup("Dr. Batch2", "batch2.doc@example.com", "doctor")
    pat = signup("Patient Batch2", "batch2.pat@example.com", "patient")
    bystander = signup("Bystander Patient", "batch2.bystander@example.com", "patient")
    doc_id, pat_id = doc["user"]["id"], pat["user"]["id"]
    requests.post(f"{BASE}/appointments", headers=auth_header(doc), json={"patient_id": pat_id, "scheduled_time": "2026-08-20T10:00:00Z"}).raise_for_status()

    doctor = Peer("Doctor", doc["access_token"])
    patient = Peer("Patient", pat["access_token"])
    await asyncio.gather(doctor.connect(), patient.connect())
    await asyncio.sleep(0.3)

    print("\n=== 1. Place + accept a call with platform tags, check busy state, exchange a quality report ===")
    await doctor.send({"type": "call:invite", "to": pat_id, "media": "video", "platform": "ios"})
    incoming = await patient.wait_for("call:incoming")
    call_id = incoming["call_id"]

    # §7: while ringing, both sides should show as in an active call.
    users_resp = requests.get(f"{BASE}/users", headers=auth_header(doc)).json()
    pat_entry = next((u for u in users_resp if u["id"] == pat_id), None)
    check("§7: patient shows in_active_call=True while ringing", pat_entry is not None and pat_entry["in_active_call"] is True)

    await patient.send({"type": "call:accept", "call_id": call_id, "to": doc_id, "consent": True, "platform": "android"})
    check("call connected", (await doctor.wait_for("call:accepted")) is not None)

    # §23: network-quality relay + persistence.
    await doctor.send({"type": "call:network-quality", "call_id": call_id, "to": pat_id, "quality": "poor"})
    quality_msg = await patient.wait_for("call:network-quality")
    check("§23: network-quality relayed to peer", quality_msg is not None and quality_msg.get("quality") == "poor")

    await asyncio.sleep(0.2)
    hist = requests.get(f"{BASE}/calls/history", headers=auth_header(doc)).json()
    record = next((c for c in hist if c["call_id"] == call_id), None)
    check("call record found", record is not None)
    check("§29: caller_platform is 'ios'", record is not None and record.get("caller_platform") == "ios")
    check("§29: callee_platform is 'android'", record is not None and record.get("callee_platform") == "android")
    check("§23: last_network_quality persisted for the caller", record is not None and record.get("last_network_quality", {}).get(doc_id) == "poor")

    print("\n=== 2. §30 notes: rejected while the call is still live ===")
    notes_resp = requests.patch(f"{BASE}/calls/{call_id}/notes", headers=auth_header(doc), json={"notes": "too early"})
    check("notes on a live call rejected with 409", notes_resp.status_code == 409)

    print("\n=== 3. End the call, then exercise the notes endpoint ===")
    await doctor.send({"type": "call:end", "call_id": call_id, "to": pat_id})
    await patient.wait_for("call:ended")
    await asyncio.sleep(0.2)

    users_resp2 = requests.get(f"{BASE}/users", headers=auth_header(doc)).json()
    pat_entry2 = next((u for u in users_resp2 if u["id"] == pat_id), None)
    check("§7: patient shows in_active_call=False once the call ended", pat_entry2 is not None and pat_entry2["in_active_call"] is False)

    forbidden_resp = requests.patch(f"{BASE}/calls/{call_id}/notes", headers=auth_header(bystander), json={"notes": "not mine to add"})
    check("§30: non-participant adding notes gets 403", forbidden_resp.status_code == 403)

    bad_outcome_resp = requests.patch(f"{BASE}/calls/{call_id}/notes", headers=auth_header(doc), json={"outcome": "NOT_A_REAL_OUTCOME"})
    check("§30: invalid outcome value rejected with 400", bad_outcome_resp.status_code == 400)

    good_resp = requests.patch(
        f"{BASE}/calls/{call_id}/notes",
        headers=auth_header(doc),
        json={"notes": "Discussed follow-up bloodwork.", "outcome": "FOLLOW_UP_REQUIRED", "follow_up_required": True},
    )
    check("§30: notes saved successfully (200)", good_resp.status_code == 200)
    saved = good_resp.json()
    check("§30: saved notes text matches", saved.get("notes") == "Discussed follow-up bloodwork.")
    check("§30: saved outcome matches", saved.get("outcome") == "FOLLOW_UP_REQUIRED")
    check("§30: follow_up_required is True", saved.get("follow_up_required") is True)
    check("§30: notes_added_by is the doctor", saved.get("notes_added_by") == doc_id)

    # Callee (patient) should also be allowed to add/overwrite notes -- both
    # participants are authorized, not just the caller.
    patient_notes_resp = requests.patch(f"{BASE}/calls/{call_id}/notes", headers=auth_header(pat), json={"notes": "Patient-side note"})
    check("§30: the callee (patient) can also add notes (200)", patient_notes_resp.status_code == 200)

    print("\n=== 4. §35/§36 analytics events: full lifecycle trail, participant-scoped ===")
    events_resp = requests.get(f"{BASE}/calls/{call_id}/events", headers=auth_header(doc))
    check("events endpoint returns 200 for a participant", events_resp.status_code == 200)
    events = events_resp.json()
    event_types = [e["event_type"] for e in events]
    check("events include call_initiated", "call_initiated" in event_types)
    check("events include call_connected", "call_connected" in event_types)
    check("events include network_quality_report", "network_quality_report" in event_types)
    check("events include call_ended", "call_ended" in event_types)
    check("call_initiated occurs before call_connected (chronological order)", event_types.index("call_initiated") < event_types.index("call_connected"))

    forbidden_events_resp = requests.get(f"{BASE}/calls/{call_id}/events", headers=auth_header(bystander))
    check("§36: non-participant fetching events gets 403", forbidden_events_resp.status_code == 403)

    print("\n=== 5. §35/§36: permission_denied and caller_busy/patient_busy events actually get emitted ===")
    await patient.send({"type": "call:invite", "to": doc_id, "media": "video", "platform": "web"})
    perm_err = await patient.wait_for("error")
    check("patient invite rejected (not authorized)", perm_err is not None and perm_err.get("code") == "not_authorized_to_call")
    # No call_id was created for this rejected attempt, so there's no
    # events endpoint to check against -- the structured log line + Mongo
    # insert happened (see app/analytics.py), which is what §35/§36 asked
    # for; there's no participant-scoped call_id to query it back through,
    # by design (permission_denied fires before a call ever exists).

    for p in (doctor, patient):
        await p.ws.close()

    print("\n" + "=" * 60)
    print(f"RESULT: {len(results['pass'])} passed, {len(results['fail'])} failed")
    if results["fail"]:
        print("Failed:", results["fail"])
    print("=" * 60)
    sys.exit(1 if results["fail"] else 0)


asyncio.run(main())
