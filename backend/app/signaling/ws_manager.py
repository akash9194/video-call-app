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
    call:incoming        { call_id, from: userId, from_name, media: "audio" | "video" }
    call:accepted         { call_id, from: userId }
    call:rejected         { call_id, from: userId }
    call:cancelled        { call_id, from: userId }
    call:ended             { call_id, from: userId }
    call:user-offline    { call_id }   (callee not connected)
    webrtc:offer / answer / ice-candidate  (relayed as-is)
    call:media-switch    { call_id, from: userId, media: "audio" | "video" }  (relayed as-is)
    presence:update       { user_id, is_online }
    error                 { message }

Switching between voice and video mid-call is NOT a new call -- it's a
WebRTC renegotiation on the *existing* peer connection: the side that's
switching adds or removes its local video track, then runs a second
offer/answer exchange (the very same "webrtc:offer"/"webrtc:answer"
messages above, sent again on an already-active call_id). call:media-switch
is purely an advance notice so the other side's UI can react immediately
(e.g. swap to an avatar) without waiting on the renegotiation round-trip;
this server does not need to understand or validate it, only relay it, the
same as it does for the webrtc:* messages.
"""

import uuid
from datetime import datetime, timezone

from fastapi import WebSocket
from bson import ObjectId

from app.database import calls_collection, users_collection


class ConnectionManager:
    def __init__(self):
        # user_id -> WebSocket
        self.active_connections: dict[str, WebSocket] = {}

    async def connect(self, user_id: str, websocket: WebSocket):
        await websocket.accept()
        self.active_connections[user_id] = websocket
        await users_collection.update_one({"_id": ObjectId(user_id)}, {"$set": {"is_online": True}})
        await self.broadcast_presence(user_id, True)

    async def disconnect(self, user_id: str):
        self.active_connections.pop(user_id, None)
        await users_collection.update_one({"_id": ObjectId(user_id)}, {"$set": {"is_online": False}})
        await self.broadcast_presence(user_id, False)

    async def broadcast_presence(self, user_id: str, is_online: bool):
        payload = {"type": "presence:update", "user_id": user_id, "is_online": is_online}
        for ws in list(self.active_connections.values()):
            try:
                await ws.send_json(payload)
            except Exception:
                pass

    async def send_to_user(self, user_id: str, message: dict) -> bool:
        ws = self.active_connections.get(user_id)
        if not ws:
            return False
        try:
            await ws.send_json(message)
            return True
        except Exception:
            return False

    def is_online(self, user_id: str) -> bool:
        return user_id in self.active_connections


manager = ConnectionManager()


async def handle_message(sender_id: str, sender_name: str, message: dict):
    msg_type = message.get("type")

    if msg_type == "call:invite":
        callee_id = message["to"]
        media = message.get("media", "video")
        if media not in ("audio", "video"):
            media = "video"
        call_id = str(uuid.uuid4())

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

        delivered = await manager.send_to_user(
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
            await manager.send_to_user(sender_id, {"type": "call:user-offline", "call_id": call_id})
        return

    if msg_type == "call:accept":
        call_id = message["call_id"]
        await calls_collection.update_one(
            {"call_id": call_id},
            {"$set": {"status": "active", "started_at": datetime.now(timezone.utc)}},
        )
        await manager.send_to_user(message["to"], {"type": "call:accepted", "call_id": call_id, "from": sender_id})
        return

    if msg_type == "call:reject":
        call_id = message["call_id"]
        await calls_collection.update_one({"call_id": call_id}, {"$set": {"status": "rejected"}})
        await manager.send_to_user(message["to"], {"type": "call:rejected", "call_id": call_id, "from": sender_id})
        return

    if msg_type == "call:cancel":
        call_id = message["call_id"]
        await calls_collection.update_one({"call_id": call_id}, {"$set": {"status": "cancelled"}})
        await manager.send_to_user(message["to"], {"type": "call:cancelled", "call_id": call_id, "from": sender_id})
        return

    if msg_type == "call:end":
        call_id = message["call_id"]
        call = await calls_collection.find_one({"call_id": call_id})
        update = {"status": "ended", "ended_at": datetime.now(timezone.utc)}
        if call and call.get("started_at"):
            duration = (update["ended_at"] - call["started_at"]).total_seconds()
            update["duration_seconds"] = int(duration)
        await calls_collection.update_one({"call_id": call_id}, {"$set": update})
        await manager.send_to_user(message["to"], {"type": "call:ended", "call_id": call_id, "from": sender_id})
        return

    if msg_type in ("webrtc:offer", "webrtc:answer", "webrtc:ice-candidate", "call:media-switch"):
        # Relay untouched to the other peer. call:media-switch is just an
        # advance notice for the UI; the real change happens via the
        # webrtc:offer/answer renegotiation the client sends alongside it.
        await manager.send_to_user(message["to"], {**message, "from": sender_id})
        return

    await manager.send_to_user(sender_id, {"type": "error", "message": f"Unknown message type: {msg_type}"})
