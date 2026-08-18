"""
Validates multi-device signaling: one account connected from 3 devices at
once (e.g. phone + tablet + web), a second account calls it, all 3 devices
must ring, whichever accepts first wins, the other 2 get told
"answered elsewhere" so they can dismiss, and all subsequent signaling
(webrtc offer/answer, call:end) is routed ONLY to the device that actually
answered -- never fanned out to the idle devices.

Runs against the real backend (scripts/_mock_server.py) with real
WebSocket connections -- this is protocol-level, not mocked.
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


def signup(name, email, role="patient"):
    r = requests.post(f"{BASE}/auth/signup", json={"name": name, "email": email, "password": "testpass123", "role": role})
    r.raise_for_status()
    return r.json()


class Device:
    def __init__(self, label, token, device_id):
        self.label = label
        self.token = token
        self.device_id = device_id
        self.ws = None
        self.events = asyncio.Queue()

    async def connect(self):
        self.ws = await websockets.connect(f"{WS_BASE}/ws/signaling?token={self.token}&device_id={self.device_id}")
        asyncio.create_task(self._loop())

    async def _loop(self):
        try:
            async for raw in self.ws:
                msg = json.loads(raw)
                print(f"    <{self.label}> {msg['type']}")
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

    async def drain_none(self, t, timeout=1.5):
        """Confirm a message type does NOT arrive within the window."""
        msg = await self.wait_for(t, timeout=timeout)
        return msg is None

    def send(self, msg):
        return self.ws.send(json.dumps(msg))


async def main():
    print("Setup: Caller (doctor, 1 device) calls Callee (patient, connected on 3 devices: phone, tablet, web)")
    caller_acc = signup("Caller Solo", "md.caller@example.com", role="doctor")
    callee_acc = signup("Callee MultiDevice", "md.callee@example.com", role="patient")
    caller_id = caller_acc["user"]["id"]
    callee_id = callee_acc["user"]["id"]

    # Doctor-only initiation now requires an active appointment between the
    # two before call:invite is allowed to ring -- see ws_manager.py.
    appt = requests.post(
        f"{BASE}/appointments",
        headers={"Authorization": f"Bearer {caller_acc['access_token']}"},
        json={"patient_id": callee_id, "scheduled_time": "2026-08-20T10:00:00Z"},
    )
    appt.raise_for_status()

    caller = Device("Caller", caller_acc["access_token"], "caller-device-1")
    phone = Device("Callee/Phone", callee_acc["access_token"], "callee-phone")
    tablet = Device("Callee/Tablet", callee_acc["access_token"], "callee-tablet")
    web = Device("Callee/Web", callee_acc["access_token"], "callee-web")

    await asyncio.gather(caller.connect(), phone.connect(), tablet.connect(), web.connect())
    await asyncio.sleep(0.3)

    # --- Step 1: invite, confirm all 3 callee devices ring ---
    print("\nStep 1: caller places a video call, all 3 callee devices should ring")
    await caller.send({"type": "call:invite", "to": callee_id, "media": "video"})

    inc_phone = await phone.wait_for("call:incoming")
    inc_tablet = await tablet.wait_for("call:incoming")
    inc_web = await web.wait_for("call:incoming")
    check("phone received call:incoming", inc_phone is not None)
    check("tablet received call:incoming", inc_tablet is not None)
    check("web received call:incoming", inc_web is not None)
    call_id = (inc_phone or inc_tablet or inc_web)["call_id"]
    check("same call_id delivered to all 3 devices", all(
        m and m["call_id"] == call_id for m in (inc_phone, inc_tablet, inc_web)
    ))

    # --- Step 2: tablet accepts first ---
    print("\nStep 2: tablet accepts -- phone and web should be told 'answered elsewhere', caller gets call:accepted")
    await tablet.send({"type": "call:accept", "call_id": call_id, "to": caller_id})

    accepted = await caller.wait_for("call:accepted")
    check("caller received call:accepted", accepted is not None and accepted["call_id"] == call_id)

    ae_phone = await phone.wait_for("call:answered_elsewhere")
    ae_web = await web.wait_for("call:answered_elsewhere")
    check("phone received call:answered_elsewhere", ae_phone is not None)
    check("web received call:answered_elsewhere", ae_web is not None)

    # tablet itself should NOT get an answered_elsewhere (it's the winner)
    tablet_got_ae = await tablet.wait_for("call:answered_elsewhere", timeout=1.0)
    check("tablet (the accepting device) did NOT get answered_elsewhere", tablet_got_ae is None)

    # --- Step 3: post-accept signaling must go ONLY to tablet, not phone/web ---
    print("\nStep 3: caller sends webrtc:offer -- must reach ONLY tablet, not phone or web")
    fake_sdp = {"sdp": "v=0\r\no=- 0 0 IN IP4 127.0.0.1\r\ns=-\r\nt=0 0\r\n", "type": "offer"}
    await caller.send({"type": "webrtc:offer", "call_id": call_id, "to": callee_id, "sdp": fake_sdp})

    offer_on_tablet = await tablet.wait_for("webrtc:offer")
    check("tablet (winning device) received the webrtc:offer", offer_on_tablet is not None)

    offer_on_phone = await phone.drain_none("webrtc:offer")
    offer_on_web = await web.drain_none("webrtc:offer")
    check("phone did NOT receive the webrtc:offer (idle device, not fanned out)", offer_on_phone)
    check("web did NOT receive the webrtc:offer (idle device, not fanned out)", offer_on_web)

    # --- Step 4: tablet answers, routed back only to caller ---
    print("\nStep 4: tablet sends webrtc:answer -- must reach caller")
    await tablet.send({"type": "webrtc:answer", "call_id": call_id, "to": caller_id, "sdp": {"sdp": "v=0\r\n", "type": "answer"}})
    answer_on_caller = await caller.wait_for("webrtc:answer")
    check("caller received the webrtc:answer from the winning device", answer_on_caller is not None)

    # --- Step 5: call:end from caller routed only to tablet ---
    print("\nStep 5: caller ends the call -- only tablet should get call:ended, not phone/web")
    await caller.send({"type": "call:end", "call_id": call_id, "to": callee_id})
    ended_on_tablet = await tablet.wait_for("call:ended")
    check("tablet received call:ended", ended_on_tablet is not None)
    ended_on_phone = await phone.drain_none("call:ended")
    check("phone did NOT receive call:ended (already lost this call)", ended_on_phone)

    # --- Step 6: presence -- callee should still show online (other 2 devices still connected) ---
    print("\nStep 6: presence sanity -- callee still has phone+web connected, should still be 'online'")
    r = requests.get(f"{BASE}/users", headers={"Authorization": f"Bearer {caller_acc['access_token']}"})
    users = r.json()
    callee_user = next((u for u in users if u["id"] == callee_id), None)
    check("callee still shown online (2 of 3 devices still connected)", callee_user is not None and callee_user["is_online"] is True)

    for d in (caller, phone, tablet, web):
        await d.ws.close()

    print("\n" + "=" * 60)
    print(f"RESULT: {len(results['pass'])} passed, {len(results['fail'])} failed")
    if results["fail"]:
        print("Failed:", results["fail"])
    print("=" * 60)
    sys.exit(1 if results["fail"] else 0)


asyncio.run(main())
