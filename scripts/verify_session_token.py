"""
Validates the call-session token (epic §28/§3 -- "call session/room with a
time-limited token"): app.config.Settings.call_session_token /
verify_call_session_token as a standalone unit, plus the two ways a client
actually gets one -- pushed over the WebSocket at call:accept, and fetched
via GET /calls/{call_id}/session-token while the call is live.

Part 1 (no server needed) exercises the mint/verify roundtrip directly
against the Settings class: correct token verifies, wrong call_id/user_id/
tampered signature/expired token all get rejected.

Part 2 runs against the real backend (scripts/_mock_server.py): both sides
of a live call receive call:session-token over the wire, the REST endpoint
returns one that independently verifies against the SAME logic used in
part 1 (proving the two issuance paths are consistent, not just each
individually self-consistent), a non-participant is rejected, and the
endpoint correctly refuses once the call has ended (409) -- a token for a
call that's over isn't meaningful, by design (see the docstring on
Settings.call_session_token).

Part 3 (added this round) validates the token's first real consumer:
GET /calls/{call_id}/events now accepts EITHER the existing JWT (unchanged
-- proven by a regression check) OR an X-Call-Session-Token header alone,
with no JWT at all. Confirms: token-only access works and returns the real
event list; a garbage/tampered token is rejected (401); a token minted for
a DIFFERENT call_id is rejected against this call's events (401) even
though the signature itself is valid; a token that's technically valid but
belongs to a non-participant is still blocked by the same 403 participant
check the JWT path uses (proving the two auth paths funnel into one
authorization check, not two separately-maintained ones); and no auth at
all still gets a plain 401.
"""
import asyncio
import json
import os
import sys
import time

import requests
import websockets

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(SCRIPT_DIR, "..", "backend"))
os.environ.setdefault("JWT_SECRET_KEY", "local-selfcheck-secret")

from app.config import Settings  # noqa: E402

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


def part1_unit_tests():
    print("=== Part 1: mint/verify roundtrip (no server) ===")
    s = Settings(jwt_secret_key="unit-test-secret", call_session_token_ttl_seconds=1)

    token, expires_at = s.call_session_token("call-abc", "user-1")
    check("token is a non-empty string", isinstance(token, str) and len(token) > 0)
    check("expires_at is roughly now + ttl", abs(expires_at - (int(time.time()) + 1)) <= 1)
    check("correct (call_id, user_id) verifies", s.verify_call_session_token(token, "call-abc", "user-1") is True)
    check("wrong call_id is rejected", s.verify_call_session_token(token, "call-xyz", "user-1") is False)
    check("wrong user_id is rejected", s.verify_call_session_token(token, "call-abc", "user-2") is False)

    tampered = token[:-4] + ("0" * 4 if token[-4:] != "0000" else "1111")
    check("tampered signature is rejected", s.verify_call_session_token(tampered, "call-abc", "user-1") is False)
    check("garbage input doesn't crash verification", s.verify_call_session_token("not-a-real-token", "call-abc", "user-1") is False)
    check("empty string doesn't crash verification", s.verify_call_session_token("", "call-abc", "user-1") is False)

    # A token minted for a DIFFERENT secret must not verify against this one
    # -- proves the signature actually depends on jwt_secret_key, not just
    # on the payload shape.
    other_secret = Settings(jwt_secret_key="a-different-secret", call_session_token_ttl_seconds=2)
    cross_token, _ = other_secret.call_session_token("call-abc", "user-1")
    check("token minted with a different secret is rejected", s.verify_call_session_token(cross_token, "call-abc", "user-1") is False)

    # Sleep well past the 1s TTL -- expiry is compared with int()-truncated
    # unix timestamps on both sides, so the worst-case slack near the
    # boundary is just under an extra second (negligible against a
    # real-world multi-minute TTL, but needs a wider margin here than
    # "ttl + a few hundred ms" to avoid a flaky test).
    print("  -- waiting well past the 1s TTL to expire --")
    time.sleep(2.5)
    check("expired token is rejected", s.verify_call_session_token(token, "call-abc", "user-1") is False)

    print("\n-- identity_from_call_session_token (the §28 events-endpoint consumer) --")
    s2 = Settings(jwt_secret_key="unit-test-secret-2", call_session_token_ttl_seconds=30)
    fresh_token, _ = s2.call_session_token("call-xyz", "user-42")
    check("identity_from_call_session_token extracts the right user_id from a valid token", s2.identity_from_call_session_token(fresh_token, "call-xyz") == "user-42")
    check("identity_from_call_session_token rejects a mismatched call_id (valid sig, wrong call)", s2.identity_from_call_session_token(fresh_token, "call-other") is None)
    check("identity_from_call_session_token rejects garbage input without crashing", s2.identity_from_call_session_token("not:a:real:token:at-all-nope", "call-xyz") is None)
    check("identity_from_call_session_token rejects an empty string without crashing", s2.identity_from_call_session_token("", "call-xyz") is None)


async def part2_live_backend():
    print("\n=== Part 2: issuance against the real backend ===")
    verifier = Settings(jwt_secret_key=os.environ["JWT_SECRET_KEY"])  # same secret the running server uses

    doc = signup("Dr. Session", "session.doc@example.com", "doctor")
    pat = signup("Patient Session", "session.pat@example.com", "patient")
    bystander = signup("Bystander Session", "session.bystander@example.com", "patient")
    doc_id, pat_id = doc["user"]["id"], pat["user"]["id"]
    requests.post(f"{BASE}/appointments", headers=auth_header(doc), json={"patient_id": pat_id, "scheduled_time": "2026-08-20T10:00:00Z"}).raise_for_status()

    doctor = Peer("Doctor", doc["access_token"])
    patient = Peer("Patient", pat["access_token"])
    await asyncio.gather(doctor.connect(), patient.connect())
    await asyncio.sleep(0.3)

    await doctor.send({"type": "call:invite", "to": pat_id, "media": "video", "platform": "web"})
    incoming = await patient.wait_for("call:incoming")
    call_id = incoming["call_id"]
    await patient.send({"type": "call:accept", "call_id": call_id, "to": doc_id, "consent": True, "platform": "web"})
    await doctor.wait_for("call:accepted")

    caller_token_msg = await doctor.wait_for("call:session-token")
    callee_token_msg = await patient.wait_for("call:session-token")
    check("caller received call:session-token over the WS", caller_token_msg is not None and caller_token_msg.get("call_id") == call_id)
    check("callee received call:session-token over the WS", callee_token_msg is not None and callee_token_msg.get("call_id") == call_id)
    check(
        "caller's WS-delivered token verifies for (call_id, caller_id)",
        caller_token_msg is not None and verifier.verify_call_session_token(caller_token_msg["token"], call_id, doc_id),
    )
    check(
        "callee's WS-delivered token verifies for (call_id, callee_id)",
        callee_token_msg is not None and verifier.verify_call_session_token(callee_token_msg["token"], call_id, pat_id),
    )
    check(
        "caller's token does NOT verify for the callee's user_id (not interchangeable)",
        caller_token_msg is not None and not verifier.verify_call_session_token(caller_token_msg["token"], call_id, pat_id),
    )

    print("\n-- REST endpoint while the call is live --")
    rest_resp = requests.get(f"{BASE}/calls/{call_id}/session-token", headers=auth_header(doc))
    check("REST session-token endpoint returns 200 while live", rest_resp.status_code == 200)
    rest_token = rest_resp.json()
    check("REST-issued token verifies against the same logic", verifier.verify_call_session_token(rest_token["token"], call_id, doc_id))

    forbidden_resp = requests.get(f"{BASE}/calls/{call_id}/session-token", headers=auth_header(bystander))
    check("non-participant fetching a session token gets 403", forbidden_resp.status_code == 403)

    print("\n-- end the call, confirm the REST endpoint refuses afterward --")
    await doctor.send({"type": "call:end", "call_id": call_id, "to": pat_id})
    await patient.wait_for("call:ended")
    await asyncio.sleep(0.2)

    after_end_resp = requests.get(f"{BASE}/calls/{call_id}/session-token", headers=auth_header(doc))
    check("REST session-token endpoint returns 409 once the call has ended", after_end_resp.status_code == 409)

    for p in (doctor, patient):
        await p.ws.close()


async def part3_events_endpoint_enforcement():
    print("\n=== Part 3: GET /calls/{call_id}/events accepts a session token, no JWT ===")
    verifier = Settings(jwt_secret_key=os.environ["JWT_SECRET_KEY"])

    doc = signup("Dr. Enforce", "enforce.doc@example.com", "doctor")
    pat = signup("Patient Enforce", "enforce.pat@example.com", "patient")
    outsider = signup("Outsider Enforce", "enforce.outsider@example.com", "patient")
    doc_id, pat_id = doc["user"]["id"], pat["user"]["id"]
    requests.post(f"{BASE}/appointments", headers=auth_header(doc), json={"patient_id": pat_id, "scheduled_time": "2026-08-20T10:00:00Z"}).raise_for_status()

    doctor = Peer("Doctor", doc["access_token"])
    patient = Peer("Patient", pat["access_token"])
    await asyncio.gather(doctor.connect(), patient.connect())
    await asyncio.sleep(0.3)

    await doctor.send({"type": "call:invite", "to": pat_id, "media": "video", "platform": "web"})
    incoming = await patient.wait_for("call:incoming")
    call_id = incoming["call_id"]
    await patient.send({"type": "call:accept", "call_id": call_id, "to": doc_id, "consent": True, "platform": "web"})
    await doctor.wait_for("call:accepted")
    patient_token_msg = await patient.wait_for("call:session-token")
    patient_session_token = patient_token_msg["token"]

    await doctor.send({"type": "call:end", "call_id": call_id, "to": pat_id})
    await patient.wait_for("call:ended")
    await asyncio.sleep(0.3)  # let call:ended's analytics event actually land before querying it

    print("  -- regression: the existing JWT-only path still works unchanged --")
    jwt_resp = requests.get(f"{BASE}/calls/{call_id}/events", headers=auth_header(doc))
    check("JWT-only auth on /events still returns 200 (unchanged behavior)", jwt_resp.status_code == 200)
    jwt_events = jwt_resp.json() if jwt_resp.status_code == 200 else []
    check("JWT-only auth returns a non-empty real event list", len(jwt_events) > 0)

    print("  -- new: session-token-only auth, NO Authorization header at all --")
    token_resp = requests.get(f"{BASE}/calls/{call_id}/events", headers={"X-Call-Session-Token": patient_session_token})
    check("session-token-only auth (no JWT) returns 200", token_resp.status_code == 200)
    token_events = token_resp.json() if token_resp.status_code == 200 else []
    check("session-token-only auth returns the SAME events as the JWT path", token_events == jwt_events)

    print("  -- rejection cases --")
    no_auth_resp = requests.get(f"{BASE}/calls/{call_id}/events")
    check("no credential at all -> 401", no_auth_resp.status_code == 401)

    garbage_resp = requests.get(f"{BASE}/calls/{call_id}/events", headers={"X-Call-Session-Token": "not-a-real-token"})
    check("garbage session token -> 401", garbage_resp.status_code == 401)

    other_call_token, _ = verifier.call_session_token("some-other-call-id", pat_id)
    wrong_call_resp = requests.get(f"{BASE}/calls/{call_id}/events", headers={"X-Call-Session-Token": other_call_token})
    check("a validly-signed token for a DIFFERENT call_id -> 401 on this call's events", wrong_call_resp.status_code == 401)

    outsider_token, _ = verifier.call_session_token(call_id, outsider["user"]["id"])
    outsider_resp = requests.get(f"{BASE}/calls/{call_id}/events", headers={"X-Call-Session-Token": outsider_token})
    check("a validly-signed token for a non-participant -> 403 (same participant check as the JWT path)", outsider_resp.status_code == 403)

    for p in (doctor, patient):
        await p.ws.close()


async def main():
    part1_unit_tests()
    await part2_live_backend()
    await part3_events_endpoint_enforcement()

    print("\n" + "=" * 60)
    print(f"RESULT: {len(results['pass'])} passed, {len(results['fail'])} failed")
    if results["fail"]:
        print("Failed:", results["fail"])
    print("=" * 60)
    sys.exit(1 if results["fail"] else 0)


asyncio.run(main())
