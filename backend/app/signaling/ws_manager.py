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
    call:invite         { to: userId, media: "audio" | "video", platform?: "ios"|"android"|"web" }
    call:accept         { call_id, to: userId, consent: true, platform?: "ios"|"android"|"web" }
    call:reject         { call_id, to: userId }
    call:cancel         { call_id, to: userId }
    call:end            { call_id, to: userId }
    webrtc:offer        { call_id, to: userId, sdp }
    webrtc:answer       { call_id, to: userId, sdp }
    webrtc:ice-candidate{ call_id, to: userId, candidate }
    call:media-switch   { call_id, to: userId, media: "audio" | "video", auto?: true }
    call:network-quality{ call_id, to: userId, quality: "good" | "fair" | "poor" }

  Server -> Client
    call:incoming          { call_id, from: userId, from_name, media: "audio" | "video" }
    call:accepted           { call_id, from: userId }
    call:rejected           { call_id, from: userId }
    call:cancelled          { call_id, from: userId, reason?: "timeout" }
    call:answered_elsewhere { call_id, from: userId }  (sent to this user's OTHER devices)
    call:ended               { call_id, from: userId, reason?: "peer_disconnected" }
    call:timeout            { call_id }   (caller only: nobody answered in time)
    call:user-offline      { call_id }   (callee not connected on any device)
    call:peer-disconnected  { call_id }   (the other party's connection just dropped -- show "Reconnecting...", don't end the call yet)
    call:peer-reconnected   { call_id }   (they're back -- clear the "Reconnecting..." state)
    webrtc:offer / answer / ice-candidate  (relayed as-is)
    call:media-switch      { call_id, from: userId, media: "audio" | "video" }  (relayed as-is)
    call:network-quality   { call_id, from: userId, quality: "good" | "fair" | "poor" }  (relayed as-is)
    call:session-token     { call_id, token, expires_at }  (sent to each side individually once CONNECTED -- see Settings.call_session_token)
    presence:update         { user_id, is_online }
    error                   { message, code }

Role-based call initiation
----------------------------
Only a user whose role holds the VIDEO_CALL_INITIATE permission (see
Settings.has_permission / video_call_initiate_roles -- "doctor" by default,
configurable via env, not a hardcoded string comparison) can send
call:invite; anyone else never can. This is enforced here in the server,
not just by hiding the UI button on the client, since a client is never
trusted input. The initiator also needs an active ("scheduled") appointment
with the specific patient they're calling -- see appointments_collection /
app/routers/appointments.py. A rejected call:invite never creates a call_id
and gets a targeted `error` back with a `code` the client can key off of:
    not_authorized_to_call  -- sender's role lacks VIDEO_CALL_INITIATE
    invalid_callee           -- target isn't a patient (or doesn't exist)
    no_active_appointment    -- no scheduled appointment links the two
    caller_busy               -- sender already has another call in progress
    patient_busy               -- callee already has another call in progress

Patient consent
-----------------
The callee (always the patient) must send `consent: true` on call:accept
for it to be honored -- this is the server-side gate behind the client's
consent step before a telehealth session connects. A missing/false consent
gets a targeted error back with code `consent_required` and the call stays
ringing so the client can show the consent step and retry (each rejected
attempt increments the call record's permission_failures counter). On a
successful accept, `consent_given`/`consent_at` are stamped onto the call
record.

Switching between voice and video mid-call is NOT a new call -- it's a
WebRTC renegotiation on the *existing* peer connection: the side that's
switching adds or removes its local video track, then runs a second
offer/answer exchange (the very same "webrtc:offer"/"webrtc:answer"
messages above, sent again on an already-active call_id). call:media-switch
is purely an advance notice so the other side's UI can react immediately
(e.g. swap to an avatar) without waiting on the renegotiation round-trip;
this server does not need to understand or validate it, only relay it, the
same as it does for the webrtc:* messages -- except for one thing: if it
carries `auto: true` (the client's automatic audio-only fallback, distinct
from the user manually tapping "switch to voice"), the call record is
tagged audio_only_fallback_occurred so that's auditable later.

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

Single-active-call locking
-----------------------------
Before a call_id is created, both sides are checked against
`call_participants` (the live, in-memory source of truth for what's
currently ringing or connected) -- if the caller is already a party to
another call, or the callee is, the invite is rejected with caller_busy /
patient_busy respectively rather than creating a second overlapping
session for either of them.

Stale/replayed messages
-------------------------
call:accept and every webrtc:*/call:media-switch message are only honored
while `call_id` is still an open entry in `call_participants` -- one that's
already ended, timed out, or been cancelled is ignored rather than
resurrecting call state on a client or silently corrupting a new call. This
guards against a message arriving late (a slow network) after the call it
refers to is already over.

Ringing timeout & disconnect grace period
-------------------------------------------
A call left ringing for RINGING_TIMEOUT_SECONDS with nobody accepting is
auto-expired: the caller gets call:timeout, the callee's devices get
call:cancelled, and the call is marked NO_ANSWER. Once a call is connected,
if one side's WebSocket connection drops (wifi hiccup, app backgrounded),
the call is NOT ended immediately -- the other side gets
call:peer-disconnected (and the call record's interruption_count is
incremented) and the call stays pinned for DISCONNECT_GRACE_SECONDS so a
reconnecting client (see signaling.ts's auto-reconnect, which reuses the
same stable device_id) can resume it; call:peer-reconnected fires if they
make it back in time (reconnection_count incremented), otherwise the call
is cleanly ended as DROPPED instead of hanging forever on both sides.

Call-state vocabulary
------------------------
Persisted call.status values: RINGING, CONNECTED, DECLINED, NO_ANSWER,
CANCELLED, ENDED, DROPPED (BUSY is rejected before a call record is ever
created, so it never appears as a status). Every terminal transition also
stamps an `end_reason` from schemas.call.END_REASONS.

Analytics (epic §35, §36)
------------------------
Every lifecycle transition above also calls app.analytics.emit_event(),
which both logs a structured line and writes to analytics_events_collection
-- see that module for the full event-type list. Best-effort: a failure to
persist an event never blocks or fails the signaling path that triggered it.
"""

import asyncio
import logging
import os
import uuid
from datetime import datetime, timezone

from fastapi import WebSocket
from bson import ObjectId

from app.analytics import emit_event
from app.config import settings
from app.database import appointments_collection, calls_collection, users_collection

logger = logging.getLogger(__name__)

# Overridable via env for tests -- a verification script shouldn't have to
# sleep 45+30 real seconds to exercise the timeout/grace-period paths.
RINGING_TIMEOUT_SECONDS = int(os.environ.get("RINGING_TIMEOUT_SECONDS", "45"))
DISCONNECT_GRACE_SECONDS = int(os.environ.get("DISCONNECT_GRACE_SECONDS", "30"))


class ConnectionManager:
    def __init__(self):
        # user_id -> { device_id -> WebSocket }
        self.active_connections: dict[str, dict[str, WebSocket]] = {}
        # call_id -> { "caller": (user_id, device_id), "callee": (user_id, device_id) | None }
        self.call_participants: dict[str, dict[str, tuple[str, str] | None]] = {}
        # call_id -> background task, so it can be cancelled once the call
        # resolves by some other means (accepted/rejected/cancelled/ended).
        self.ringing_timers: dict[str, asyncio.Task] = {}
        self.disconnect_timers: dict[str, asyncio.Task] = {}

    async def connect(self, user_id: str, device_id: str, websocket: WebSocket):
        await websocket.accept()
        was_offline = not self.active_connections.get(user_id)
        self.active_connections.setdefault(user_id, {})[device_id] = websocket
        if was_offline:
            await users_collection.update_one({"_id": ObjectId(user_id)}, {"$set": {"is_online": True}})
            await self.broadcast_presence(user_id, True)

        # If this exact device was mid-call and had dropped, it just made it
        # back inside the grace period -- tell the other side to clear its
        # "Reconnecting..." state and cancel the pending auto-end timer.
        for call_id, participants in list(self.call_participants.items()):
            if call_id not in self.disconnect_timers:
                continue
            mine, other = self._match_participant(participants, user_id, device_id)
            if not mine:
                continue
            self._cancel_disconnect_timer(call_id)
            await calls_collection.update_one({"call_id": call_id}, {"$inc": {"reconnection_count": 1}})
            await self.send_to_device(other[0], other[1], {"type": "call:peer-reconnected", "call_id": call_id})

    async def disconnect(self, user_id: str, device_id: str):
        devices = self.active_connections.get(user_id)
        if devices:
            devices.pop(device_id, None)
            if not devices:
                self.active_connections.pop(user_id, None)
        if not self.active_connections.get(user_id):
            await users_collection.update_one({"_id": ObjectId(user_id)}, {"$set": {"is_online": False}})
            await self.broadcast_presence(user_id, False)

        # If this device was a pinned participant of a still-active call,
        # don't just let the call go silent for the other side: tell them
        # right away, and give this device a grace period to reconnect
        # (client auto-reconnects with the same stable device_id) before
        # treating the call as actually over.
        for call_id, participants in list(self.call_participants.items()):
            if participants.get("callee") is None:
                continue  # not yet an active call -- the ringing timeout handles this case
            mine, other = self._match_participant(participants, user_id, device_id)
            if not mine:
                continue
            await calls_collection.update_one({"call_id": call_id}, {"$inc": {"interruption_count": 1}})
            await self.send_to_device(other[0], other[1], {"type": "call:peer-disconnected", "call_id": call_id})
            self._cancel_disconnect_timer(call_id)
            self.disconnect_timers[call_id] = asyncio.create_task(
                self._expire_disconnected_call(call_id, user_id, device_id, other[0], other[1])
            )

    @staticmethod
    def _match_participant(participants: dict, user_id: str, device_id: str):
        """Returns (this_side, other_side) tuples if (user_id, device_id) is
        one of the two pinned participants of this call, else (None, None)."""
        caller = participants.get("caller")
        callee = participants.get("callee")
        target = (user_id, device_id)
        if caller == target:
            return caller, callee
        if callee == target:
            return callee, caller
        return None, None

    def find_active_call_for(self, user_id: str) -> str | None:
        """The call_id `user_id` is currently ringing or connected on
        (as either caller or callee), if any -- used for single-active-call
        locking (epic §19) before a new call_id is created."""
        for call_id, participants in self.call_participants.items():
            caller = participants.get("caller")
            callee = participants.get("callee")
            if (caller and caller[0] == user_id) or (callee and callee[0] == user_id):
                return call_id
        return None

    async def _expire_disconnected_call(self, call_id, gone_user_id, gone_device_id, other_user_id, other_device_id):
        try:
            await asyncio.sleep(DISCONNECT_GRACE_SECONDS)
        except asyncio.CancelledError:
            return
        still_gone = self.active_connections.get(gone_user_id, {}).get(gone_device_id) is None
        if not still_gone:
            return  # reconnected in time -- connect() already notified the peer
        call = await calls_collection.find_one({"call_id": call_id})
        update = {"status": "DROPPED", "end_reason": "NETWORK_FAILURE", "ended_at": datetime.now(timezone.utc)}
        if call and call.get("started_at"):
            update["duration_seconds"] = int((update["ended_at"] - call["started_at"]).total_seconds())
        await calls_collection.update_one({"call_id": call_id}, {"$set": update})
        await emit_event("call_dropped", call_id=call_id, disconnected_user_id=gone_user_id)
        await self.send_to_device(
            other_user_id, other_device_id,
            {"type": "call:ended", "call_id": call_id, "from": gone_user_id, "reason": "peer_disconnected"},
        )
        self.call_participants.pop(call_id, None)
        self.disconnect_timers.pop(call_id, None)

    async def _expire_ringing_call(self, call_id, caller_id, caller_device_id, callee_id):
        try:
            await asyncio.sleep(RINGING_TIMEOUT_SECONDS)
        except asyncio.CancelledError:
            return
        participants = self.call_participants.get(call_id)
        if not participants or participants.get("callee") is not None:
            return  # already accepted/resolved by some other path
        await calls_collection.update_one(
            {"call_id": call_id},
            {"$set": {"status": "NO_ANSWER", "end_reason": "NO_ANSWER", "ended_at": datetime.now(timezone.utc)}},
        )
        await emit_event("call_no_answer", call_id=call_id, caller_id=caller_id, callee_id=callee_id, reason="timeout")
        await self.send_to_device(caller_id, caller_device_id, {"type": "call:timeout", "call_id": call_id})
        await self.send_to_all_devices(
            callee_id, {"type": "call:cancelled", "call_id": call_id, "from": caller_id, "reason": "timeout"}
        )
        self.call_participants.pop(call_id, None)
        self.ringing_timers.pop(call_id, None)

    def _cancel_ringing_timer(self, call_id: str):
        task = self.ringing_timers.pop(call_id, None)
        if task and not task.done():
            task.cancel()

    def _cancel_disconnect_timer(self, call_id: str):
        task = self.disconnect_timers.pop(call_id, None)
        if task and not task.done():
            task.cancel()

    def clear_call(self, call_id: str):
        self._cancel_ringing_timer(call_id)
        self._cancel_disconnect_timer(call_id)
        self.call_participants.pop(call_id, None)

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
                    if await self.send_to_device(target_user_id, entry[1], message):
                        return True
                    # The pinned device isn't reachable right now -- e.g. it
                    # reconnected under a fresh device_id somehow. Fall back
                    # to whatever devices of theirs ARE connected instead of
                    # silently dropping a message for an active call.
                    return await self.send_to_all_devices(target_user_id, message)
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

        # -- Role-based initiation ---------------------------------------
        # This is enforced here, server-side, deliberately -- a client
        # can't be trusted to just hide the "call" button, since nothing
        # stops a modified/malicious client from sending this message
        # directly. Reject anything sent by a role without
        # VIDEO_CALL_INITIATE before a call_id is even created.
        if not settings.has_permission(sender_role, "VIDEO_CALL_INITIATE"):
            await manager.send_to_device(
                sender_id, sender_device_id,
                {"type": "error", "message": "You don't have permission to start a call.", "code": "not_authorized_to_call"},
            )
            await emit_event("permission_denied", user_id=sender_id, role=sender_role, code="not_authorized_to_call")
            return

        callee = await users_collection.find_one({"_id": ObjectId(callee_id)})
        if not callee or callee.get("role") != "patient":
            await manager.send_to_device(
                sender_id, sender_device_id,
                {"type": "error", "message": "You can only call a patient.", "code": "invalid_callee"},
            )
            return

        # -- Appointment linkage ---------------------------------------
        # The initiator can only ring a patient they have an active (not
        # completed/cancelled) appointment with -- prevents unrelated or
        # accidental contact. (Epic §6 replaces this with a broader
        # role/tenant/assignment eligibility model; appointments remain the
        # eligibility signal here pending that product decision -- see the
        # gap-analysis doc.)
        appointment = await appointments_collection.find_one(
            {"doctor_id": sender_id, "patient_id": callee_id, "status": "scheduled"}
        )
        if not appointment:
            await manager.send_to_device(
                sender_id, sender_device_id,
                {"type": "error", "message": "No active appointment with this patient. Schedule one before calling.", "code": "no_active_appointment"},
            )
            return

        # -- Single-active-call locking (epic §19) ------------------------
        # Enforced against call_participants -- the live, authoritative
        # record of who's currently ringing or connected -- not just the
        # UI. Checked caller-first so a caller who's themselves busy gets
        # that specific reason rather than a generic failure.
        if manager.find_active_call_for(sender_id):
            await manager.send_to_device(
                sender_id, sender_device_id,
                {"type": "error", "message": "You already have an active video call.", "code": "caller_busy"},
            )
            await emit_event("caller_busy", user_id=sender_id, attempted_callee_id=callee_id)
            return
        if manager.find_active_call_for(callee_id):
            await manager.send_to_device(
                sender_id, sender_device_id,
                {"type": "error", "message": "Patient is already in an iLive call.", "code": "patient_busy"},
            )
            await emit_event("patient_busy", user_id=sender_id, attempted_callee_id=callee_id)
            return

        call_id = str(uuid.uuid4())

        # Pin the caller's device now -- everything for this call_id that's
        # addressed back to the caller (accepted/rejected/webrtc/...) must
        # land on this specific device, not fan out to their other devices.
        manager.call_participants[call_id] = {"caller": (sender_id, sender_device_id), "callee": None}

        now = datetime.now(timezone.utc)
        await calls_collection.insert_one(
            {
                "call_id": call_id,
                "caller_id": sender_id,
                "callee_id": callee_id,
                "caller_role": sender_role,
                "tenant_id": "default",
                "media": media,
                "status": "RINGING",
                "end_reason": None,
                "initiated_at": now,
                "ringing_at": now,
                "answered_at": None,
                "started_at": None,
                "ended_at": None,
                "duration_seconds": None,
                "consent_given": False,
                "consent_at": None,
                "interruption_count": 0,
                "reconnection_count": 0,
                "permission_failures": 0,
                "audio_only_fallback_occurred": False,
                "qualifies_for_downstream_workflow": False,
                # Epic §29 -- self-reported by the client in call:invite's
                # optional "platform" field ("ios" | "android" | "web").
                # Not trusted for anything security-relevant, purely
                # informational for the audit trail.
                "caller_platform": message.get("platform"),
                "callee_platform": None,
                "created_at": now,
            }
        )
        await emit_event("call_initiated", call_id=call_id, caller_id=sender_id, callee_id=callee_id, caller_role=sender_role, media=media)

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
            await calls_collection.update_one(
                {"call_id": call_id},
                {"$set": {"status": "NO_ANSWER", "end_reason": "NO_ANSWER", "ended_at": datetime.now(timezone.utc)}},
            )
            await manager.send_to_device(sender_id, sender_device_id, {"type": "call:user-offline", "call_id": call_id})
            await emit_event("call_no_answer", call_id=call_id, caller_id=sender_id, callee_id=callee_id, reason="offline")
            manager.call_participants.pop(call_id, None)
        else:
            # Nobody answered within RINGING_TIMEOUT_SECONDS -- auto-expire
            # rather than leaving a "ringing" call (and a ringing UI on the
            # callee's devices) stuck forever.
            manager.ringing_timers[call_id] = asyncio.create_task(
                manager._expire_ringing_call(call_id, sender_id, sender_device_id, callee_id)
            )
        return

    if msg_type == "call:accept":
        call_id = message["call_id"]
        participants = manager.call_participants.get(call_id)
        if not participants:
            # Stale/replayed accept for a call that's already ended, timed
            # out, or was cancelled -- ignore rather than resurrecting it.
            return

        if participants.get("callee") is None:
            # -- Patient consent gate ---------------------------------
            # The callee here is always the patient (only a
            # VIDEO_CALL_INITIATE-permitted role can invite). Require
            # explicit consent before pinning this device as the callee or
            # marking the call connected -- enforced here, not just by a
            # client-side modal, same reasoning as role-based initiation
            # above: a client can't be trusted.
            if not message.get("consent"):
                await calls_collection.update_one({"call_id": call_id}, {"$inc": {"permission_failures": 1}})
                await manager.send_to_device(
                    sender_id, sender_device_id,
                    {"type": "error", "message": "Patient consent is required before joining the call.", "code": "consent_required"},
                )
                await emit_event("consent_denied", call_id=call_id, user_id=sender_id)
                return
            # First device to accept wins. Pin this device as the callee
            # for the rest of the call, and tell any other devices of this
            # same user that they were beaten to it so they can dismiss
            # the incoming-call screen.
            participants["callee"] = (sender_id, sender_device_id)
            await manager.send_to_all_devices(
                sender_id,
                {"type": "call:answered_elsewhere", "call_id": call_id, "from": sender_id},
                exclude_device=sender_device_id,
            )

        manager._cancel_ringing_timer(call_id)
        now = datetime.now(timezone.utc)
        accept_update = {"status": "CONNECTED", "started_at": now, "answered_at": now, "consent_given": True, "consent_at": now}
        if message.get("platform"):
            accept_update["callee_platform"] = message["platform"]
        await calls_collection.update_one({"call_id": call_id}, {"$set": accept_update})
        await emit_event("call_connected", call_id=call_id, callee_id=sender_id)
        await manager.route(call_id, message["to"], {"type": "call:accepted", "call_id": call_id, "from": sender_id})

        # Epic §28/§3 call-session token -- mint one for each side now that
        # the call is actually connected, and push it to their pinned
        # device. See Settings.call_session_token for what this is (and
        # isn't yet) used for; also fetchable via GET
        # /calls/{call_id}/session-token for as long as the call stays live.
        caller_participant = participants.get("caller")
        callee_participant = participants.get("callee")
        if caller_participant:
            caller_user_id, caller_device = caller_participant
            token, expires_at = settings.call_session_token(call_id, caller_user_id)
            await manager.send_to_device(caller_user_id, caller_device, {"type": "call:session-token", "call_id": call_id, "token": token, "expires_at": expires_at})
        if callee_participant:
            callee_user_id, callee_device = callee_participant
            token, expires_at = settings.call_session_token(call_id, callee_user_id)
            await manager.send_to_device(callee_user_id, callee_device, {"type": "call:session-token", "call_id": call_id, "token": token, "expires_at": expires_at})
        return

    if msg_type == "call:reject":
        call_id = message["call_id"]
        await calls_collection.update_one(
            {"call_id": call_id},
            {"$set": {"status": "DECLINED", "end_reason": "PATIENT_DECLINED", "ended_at": datetime.now(timezone.utc)}},
        )
        await emit_event("call_declined", call_id=call_id, user_id=sender_id)
        await manager.route(call_id, message["to"], {"type": "call:rejected", "call_id": call_id, "from": sender_id})
        manager.clear_call(call_id)
        return

    if msg_type == "call:cancel":
        call_id = message["call_id"]
        await calls_collection.update_one(
            {"call_id": call_id},
            {"$set": {"status": "CANCELLED", "end_reason": "CALLER_CANCELLED", "ended_at": datetime.now(timezone.utc)}},
        )
        await emit_event("call_cancelled", call_id=call_id, user_id=sender_id)
        # Not yet accepted (or already pinned, route() handles both) -- if
        # still pre-accept this correctly fans out to every device that was
        # ringing so they all stop.
        await manager.route(call_id, message["to"], {"type": "call:cancelled", "call_id": call_id, "from": sender_id})
        manager.clear_call(call_id)
        return

    if msg_type == "call:end":
        call_id = message["call_id"]
        call = await calls_collection.find_one({"call_id": call_id})
        ended_at = datetime.now(timezone.utc)
        # Attribute the end reason to whichever side actually hung up --
        # useful for the audit trail (epic §29 distinguishes PATIENT_ENDED
        # from CLINICIAN_ENDED).
        end_reason = "COMPLETED"
        if call:
            if sender_id == call.get("callee_id"):
                end_reason = "PATIENT_ENDED"
            elif sender_id == call.get("caller_id"):
                end_reason = "CLINICIAN_ENDED"
        update = {"status": "ENDED", "end_reason": end_reason, "ended_at": ended_at}
        if call and call.get("started_at"):
            duration = (ended_at - call["started_at"]).total_seconds()
            update["duration_seconds"] = int(duration)
        await calls_collection.update_one({"call_id": call_id}, {"$set": update})
        await emit_event("call_ended", call_id=call_id, user_id=sender_id, end_reason=end_reason)
        await manager.route(call_id, message["to"], {"type": "call:ended", "call_id": call_id, "from": sender_id})
        manager.clear_call(call_id)
        return

    if msg_type in ("webrtc:offer", "webrtc:answer", "webrtc:ice-candidate", "call:media-switch", "call:network-quality"):
        # Relay untouched to the other peer's pinned device. call:media-switch
        # and call:network-quality are just advance notices for the UI; the
        # real media change (if any) happens via the webrtc:offer/answer
        # renegotiation the client sends alongside call:media-switch.
        call_id = message.get("call_id")
        if call_id not in manager.call_participants:
            # Stale/replayed message for a call that's already over --
            # ignore instead of acting on invalid call state.
            return
        if msg_type == "call:media-switch" and message.get("media") == "audio" and message.get("auto"):
            # The client's automatic poor-connection fallback (distinct
            # from a manual "switch to voice" tap) -- tag the call record
            # so this is auditable, per the epic §21 requirement that
            # audio-only participation be distinctly logged.
            await calls_collection.update_one({"call_id": call_id}, {"$set": {"audio_only_fallback_occurred": True}})
            await emit_event("audio_only_fallback", call_id=call_id, user_id=sender_id)
        if msg_type == "call:network-quality" and message.get("quality"):
            # Epic §23 -- persist the most recent self-reported quality
            # bucket from each side, so it's visible in call history even
            # after the call ends (not just live in the UI), and emit an
            # analytics event for anyone tracking degraded-call rates.
            await calls_collection.update_one(
                {"call_id": call_id},
                {"$set": {f"last_network_quality.{sender_id}": message["quality"]}},
            )
            await emit_event("network_quality_report", call_id=call_id, user_id=sender_id, quality=message["quality"])
        await manager.route(call_id, message["to"], {**message, "from": sender_id})
        return

    await manager.send_to_device(sender_id, sender_device_id, {"type": "error", "message": f"Unknown message type: {msg_type}"})
