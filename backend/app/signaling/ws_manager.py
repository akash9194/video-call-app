"""
WebSocket signaling layer.

This does NOT carry any audio/video media itself — it only exchanges the
small JSON messages (call invites, SDP offers/answers, ICE candidates)
that two peers need to set up a *direct* WebRTC connection with each
other. Once that peer connection is established, media flows P2P (or
through your TURN server if a direct path isn't possible), not through
this server.

Protocol (all messages are JSON with a "type" field):

  Client -> Server
    call:invite         { to: userId, media: "audio" | "video" }
    call:accept         { call_id, to: userId }
    call:reject         { call_id, to: userId }
    call:cancel         { call_id, to: userId }
    call:end            { call_id, to: userId }
    webrtc:offer        { call_id, to: userId, sdp }
    webrtc:answer       { call_id, to: userId, sdp }
    webrtc:ice-candidate{ call_id, to: userId, candidate }
    call:media-switch   { call_id, to: userId, media: "audio" | "video" }

  Server -> Client
    call:incoming          { call_id, from: userId, from_name, media: "audio" | "video" }
    call:accepted           { call_id, from: userId }
    call:rejected           { call_id, from: userId }
    call:cancelled          { call_id, from: userId }
    call:answered_elsewhere { call_id, from: userId }  (sent to this user's OTHER devices)
    call:ended               { call_id, from: userId }
    call:user-offline      { call_id }   (callee not connected on any device)
    webrtc:offer / answer / ice-candidate  (relayed as-is)
    call:media-switch      { call_id, from: userId, media: "audio" | "video" }  (relayed as-is)
    presence:update         { user_id, is_online }
    error                   { message, code }

Doctor-only initiation
-----------------------
Only a "doctor"-role user can send call:invite; a "patient"-role user
never can. This is enforced here in the server, not just by hiding the UI
button on the client, since a client is never trusted input. A doctor also
needs an active ("scheduled") appointment with the specific patient they're
calling -- see appointments_collection / app/routers/appointments.py. A
rejected call:invite never creates a call_id and gets a targeted `error`
back with a `code` the client can key off of:
    not_authorized_to_call  -- sender isn't a doctor
    invalid_callee           -- target isn't a patient (or doesn't exist)
    no_active_appointment    -- no scheduled appointment links the two

Switching between voice and video mid-call is NOT a new call -- it's a
WebRTC renegotiation on the *existing* peer connection: the side that's
switching adds or removes its local video track, then runs a second
offer/answer exchange (the very same "webrtc:offer"/"webrtc:answer"
messages above, sent again on an already-active call_id). call:media-switch
is purely an advance notice so the other side's UI can react immediately
(e.g. swap to an avatar) without waiting on the renegotiation round-trip;
this server does not need to understand or validate it, only relay it, the
same as it does for the webrtc:* messages.

Multi-device behaviour
-----------------------
A user can be signed in on several devices (phone, tablet, web) at once,
each holding its own WebSocket connection. Presence is per-user (online if
ANY device is connected). An incoming call rings on ALL of a user's
connected devices at once (call:incoming is fanned out). Whichever device
answers first "wins": the server records which (user_id, device_id) is
actually party to that call_id, sends call:accepted only to the caller's
device, and tells the callee's OTHER devices call:answered_elsewhere so
they can dismiss their incoming-call screen. From that point on, every
message for that call_id (webrtc:*, call:media-switch, call:end, ...) is
routed only to the two pinned devices, never fanned out.
"""

import uuid
from datetime import datetime, timezone

from fastapi import WebSocket
from bson import ObjectId

from app.database import appointments_collection, calls_collection, users_collection


class ConnectionManager:
    def __init__(self):
        # user_id -> { device_id -> WebSocket }
        self.active_connections: dict[str, dict[str, WebSocket]] = {}
        # call_id -> { "caller": (user_id, device_id), "callee": (user_id, device_id) | None }
        self.call_participants: dict[str, dict[str, tuple[str, str] | None]] = {}

    async def connect(self, user_id: str, device_id: str, websocket: WebSocket):
        await websocket.accept()
        was_offline = not self.active_connections.get(user_id)
        self.active_connections.setdefault(user_id, {})[device_id] = websocket
        if was_offline:
            await users_collection.update_one({"_id": ObjectId(user_id)}, {"$set": {"is_online": True}})
            await self.broadcast_presence(user_id, True)

    async def disconnect(self, user_id: str, device_id: str):
        devices = self.active_connections.get(user_id)
        if devices:
            devices.pop(device_id, None)
            if not devices:
                self.active_connections.pop(user_id, None)
        if not self.active_connections.get(user_id):
            await users_collection.update_one({"_id": ObjectId(user_id)}, {"$set": {"is_online": False}})
            await self.broadcast_presence(user_id, False)

    async def broadcast_presence(self, user_id: str, is_online: bool):
        payload = {"type": "presence:update", "user_id": user_id, "is_online": is_online}
        for devices in list(self.active_connections.values()):
            for ws in list(devices.values()):
                try:
                    await ws.send_json(payload)
                except Exception:
                    pass

    async def send_to_device(self, user_id: str, device_id: str, message: dict) -> bool:
        ws = self.active_connections.get(user_id, {}).get(device_id)
        if not ws:
            return False
        try:
            await ws.send_json(message)
            return True
        except Exception:
            return False

    async def send_to_all_devices(self, user_id: str, message: dict, exclude_device: str | None = None) -> bool:
        devices = self.active_connections.get(user_id, {})
        delivered = False
        for device_id, ws in list(devices.items()):
            if device_id == exclude_device:
                continue
            try:
                await ws.send_json(message)
                delivered = True
            except Exception:
                pass
        return delivered

    async def route(self, call_id: str, target_user_id: str, message: dict) -> bool:
        """
        Send `message` to `target_user_id` for a given call. If the call has
        already pinned a specific device for that user (they're the caller,
        or they already accepted as the callee), deliver only to that
        device. Otherwise (e.g. callee hasn't answered yet) fan out to all
        of their connected devices.
        """
        participants = self.call_participants.get(call_id)
        if participants:
            for entry in (participants.get("caller"), participants.get("callee")):
                if entry and entry[0] == target_user_id:
                    return await self.send_to_device(target_user_id, entry[1], message)
        return await self.send_to_all_devices(target_user_id, message)

    def is_online(self, user_id: str) -> bool:
        return bool(self.active_connections.get(user_id))


manager = ConnectionManager()


async def handle_message(sender_id: str, sender_device_id: str, sender_name: str, sender_role: str, message: dict):
    msg_type = message.get("type")

    if msg_type == "call:invite":
        callee_id = message["to"]
        media = message.get("media", "video")
        if media not in ("audio", "video"):
            media = "video"

        # -- Doctor-only initiation -----------------------------------
        # This is enforced here, server-side, deliberately -- a patient's
        # client can't be trusted to just hide the "call" button, since
        # nothing stops a modified/malicious client from sending this
        # message directly. Reject anything that isn't a doctor calling a
        # patient before a call_id is even created.
        if sender_role != "doctor":
            await manager.send_to_device(
                sender_id, sender_device_id,
                {"type": "error", "message": "Only a doctor can start a call.", "code": "not_authorized_to_call"},
            )
            return

        callee = await users_collection.find_one({"_id": ObjectId(callee_id)})
        if not callee or callee.get("role") != "patient":
            await manager.send_to_device(
                sender_id, sender_device_id,
                {"type": "error", "message": "You can only call a patient.", "code": "invalid_callee"},
            )
            return

        # -- Appointment linkage ---------------------------------------
        # A doctor can only ring a patient they have an active (not
        # completed/cancelled) appointment with -- prevents unrelated or
        # accidental doctor -> patient contact.
        appointment = await appointments_collection.find_one(
            {"doctor_id": sender_id, "patient_id": callee_id, "status": "scheduled"}
        )
        if not appointment:
            await manager.send_to_device(
                sender_id, sender_device_id,
                {"type": "error", "message": "No active appointment with this patient. Schedule one before calling.", "code": "no_active_appointment"},
            )
            return

        call_id = str(uuid.uuid4())

        # Pin the caller's device now -- everything for this call_id that's
        # addressed back to the caller (accepted/rejected/webrtc/...) must
        # land on this specific device, not fan out to their other devices.
        manager.call_participants[call_id] = {"caller": (sender_id, sender_device_id), "callee": None}

        await calls_collection.insert_one(
            {
                "call_id": call_id,
                "caller_id": sender_id,
                "callee_id": callee_id,
                "media": media,
                "status": "ringing",
                "started_at": None,
                "ended_at": None,
                "duration_seconds": None,
                "created_at": datetime.now(timezone.utc),
            }
        )

        # Ring every device the callee is connected on.
        delivered = await manager.send_to_all_devices(
            callee_id,
            {
                "type": "call:incoming",
                "call_id": call_id,
                "from": sender_id,
                "from_name": sender_name,
                "media": media,
            },
        )
        if not delivered:
            await calls_collection.update_one({"call_id": call_id}, {"$set": {"status": "missed"}})
            await manager.send_to_device(sender_id, sender_device_id, {"type": "call:user-offline", "call_id": call_id})
            manager.call_participants.pop(call_id, None)
        return

    if msg_type == "call:accept":
        call_id = message["call_id"]
        participants = manager.call_participants.get(call_id)
        # First device to accept wins. Pin this device as the callee for
        # the rest of the call, and tell any other devices of this same
        # user that they were beaten to it so they can dismiss the
        # incoming-call screen.
        if participants and participants.get("callee") is None:
            participants["callee"] = (sender_id, sender_device_id)
            await manager.send_to_all_devices(
                sender_id,
                {"type": "call:answered_elsewhere", "call_id": call_id, "from": sender_id},
                exclude_device=sender_device_id,
            )
        await calls_collection.update_one(
            {"call_id": call_id},
            {"$set": {"status": "active", "started_at": datetime.now(timezone.utc)}},
        )
        await manager.route(call_id, message["to"], {"type": "call:accepted", "call_id": call_id, "from": sender_id})
        return

    if msg_type == "call:reject":
        call_id = message["call_id"]
        await calls_collection.update_one({"call_id": call_id}, {"$set": {"status": "rejected"}})
        await manager.route(call_id, message["to"], {"type": "call:rejected", "call_id": call_id, "from": sender_id})
        manager.call_participants.pop(call_id, None)
        return

    if msg_type == "call:cancel":
        call_id = message["call_id"]
        await calls_collection.update_one({"call_id": call_id}, {"$set": {"status": "cancelled"}})
        # Not yet accepted (or already pinned, route() handles both) -- if
        # still pre-accept this correctly fans out to every device that was
        # ringing so they all stop.
        await manager.route(call_id, message["to"], {"type": "call:cancelled", "call_id": call_id, "from": sender_id})
        manager.call_participants.pop(call_id, None)
        return

    if msg_type == "call:end":
        call_id = message["call_id"]
        call = await calls_collection.find_one({"call_id": call_id})
        update = {"status": "ended", "ended_at": datetime.now(timezone.utc)}
        if call and call.get("started_at"):
            duration = (update["ended_at"] - call["started_at"]).total_seconds()
            update["duration_seconds"] = int(duration)
        await calls_collection.update_one({"call_id": call_id}, {"$set": update})
        await manager.route(call_id, message["to"], {"type": "call:ended", "call_id": call_id, "from": sender_id})
        manager.call_participants.pop(call_id, None)
        return

    if msg_type in ("webrtc:offer", "webrtc:answer", "webrtc:ice-candidate", "call:media-switch"):
        # Relay untouched to the other peer's pinned device. call:media-switch
        # is just an advance notice for the UI; the real change happens via
        # the webrtc:offer/answer renegotiation the client sends alongside it.
        call_id = message.get("call_id")
        await manager.route(call_id, message["to"], {**message, "from": sender_id})
        return

    await manager.send_to_device(sender_id, sender_device_id, {"type": "error", "message": f"Unknown message type: {msg_type}"})
