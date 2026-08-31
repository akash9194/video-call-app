from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, status

from app.config import settings
from app.database import analytics_events_collection, calls_collection
from app.auth.dependencies import get_current_user, get_current_user_optional
from app.schemas.call import CallOut, CallNotesUpdate, CallSessionTokenResponse, IceServersResponse, OUTCOMES

router = APIRouter(prefix="/calls", tags=["calls"])


@router.get("/ice-servers", response_model=IceServersResponse)
async def get_ice_servers(current_user: dict = Depends(get_current_user)):
    """
    The RN app fetches this right before placing/answering a call (not
    just once at startup, since TURN credentials are short-lived -- see
    Settings.turn_credentials) and passes it straight into the
    RTCPeerConnection config.
    """
    return IceServersResponse(
        ice_servers=settings.ice_servers(str(current_user["_id"])),
        audio_only_auto_fallback_enabled=settings.audio_only_auto_fallback_enabled,
    )


@router.get("/history", response_model=list[CallOut])
async def call_history(current_user: dict = Depends(get_current_user)):
    user_id = str(current_user["_id"])
    calls = []
    cursor = calls_collection.find(
        {"$or": [{"caller_id": user_id}, {"callee_id": user_id}]}
    ).sort("started_at", -1)
    async for c in cursor:
        # Only pass along keys that actually exist on the document, so
        # fields with defaults (like `media`, absent on calls recorded
        # before that field existed) fall back correctly instead of a
        # literal None overriding the default and failing validation.
        calls.append(CallOut(**{k: c[k] for k in CallOut.model_fields if k in c}))
    return calls


TERMINAL_STATUSES = {"DECLINED", "NO_ANSWER", "CANCELLED", "ENDED", "DROPPED"}
LIVE_STATUSES = {"RINGING", "CONNECTED"}


@router.get("/{call_id}/session-token", response_model=CallSessionTokenResponse)
async def get_call_session_token(call_id: str, current_user: dict = Depends(get_current_user)):
    """
    Epic §28/§3 call-session token (see Settings.call_session_token for
    the full rationale). Also delivered proactively over the WebSocket at
    call:accept (see ws_manager.py) -- this REST endpoint exists so a
    client that reconnects or needs a fresh one mid-call (the WS-delivered
    one may have since expired) doesn't have to wait for another signaling
    round-trip. Only issued while the call is actually live: a token for
    an ended call authorizes nothing today, and minting one would be
    misleading about what "session" it refers to.
    """
    call = await calls_collection.find_one({"call_id": call_id})
    if not call:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Call not found")
    user_id = str(current_user["_id"])
    if user_id not in (call.get("caller_id"), call.get("callee_id")):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You weren't a participant on this call")
    if call.get("status") not in LIVE_STATUSES:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This call isn't live -- no session token to issue")

    token, expires_at = settings.call_session_token(call_id, user_id)
    return CallSessionTokenResponse(token=token, expires_at=expires_at)


@router.patch("/{call_id}/notes", response_model=CallOut)
async def add_call_notes(call_id: str, body: CallNotesUpdate, current_user: dict = Depends(get_current_user)):
    """
    Epic §30: post-call notes & outcome. Only the two participants on the
    call can add notes, and only once the call has actually ended -- notes
    on a still-ringing/connected call don't make sense and would race with
    the signaling layer's own writes to the same document.
    """
    user_id = str(current_user["_id"])
    call = await calls_collection.find_one({"call_id": call_id})
    if not call:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Call not found")
    if user_id not in (call.get("caller_id"), call.get("callee_id")):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You weren't a participant on this call")
    if call.get("status") not in TERMINAL_STATUSES:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Notes can only be added after the call has ended")
    if body.outcome is not None and body.outcome not in OUTCOMES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"outcome must be one of {sorted(OUTCOMES)}")

    update = {
        "notes": body.notes,
        "outcome": body.outcome,
        "follow_up_required": body.follow_up_required,
        "notes_added_at": datetime.now(timezone.utc),
        "notes_added_by": user_id,
    }
    await calls_collection.update_one({"call_id": call_id}, {"$set": update})
    call.update(update)
    return CallOut(**{k: call[k] for k in CallOut.model_fields if k in call})


@router.get("/{call_id}/events")
async def get_call_events(
    call_id: str,
    current_user: dict | None = Depends(get_current_user_optional),
    x_call_session_token: str | None = Header(default=None, alias="X-Call-Session-Token"),
):
    """
    Epic §36: queryable analytics events for a single call (call_initiated,
    call_connected, permission_denied, ...), emitted by
    app.analytics.emit_event at each lifecycle transition. Scoped to
    participants only -- there's no admin/operator role in this build yet,
    so this is the privacy-safe subset: you can see the event trail for
    calls you were actually on.

    Epic §28's call-session token gets its first real consumer here: this
    endpoint now accepts EITHER the normal JWT (unchanged -- every existing
    caller keeps working exactly as before) OR an X-Call-Session-Token
    header carrying the short-lived token minted for this exact call_id,
    with no JWT at all. That's the scenario the token was built for --
    something that legitimately needs to read one call's event trail
    (an embedded widget, a future CallKit action handler) without being
    handed the user's full, long-lived account credential. The token
    itself already encodes which user it's for (see
    Settings.identity_from_call_session_token), so no separate identity
    lookup is needed on that path.
    """
    if current_user is not None:
        user_id = str(current_user["_id"])
    elif x_call_session_token:
        user_id = settings.identity_from_call_session_token(x_call_session_token, call_id)
        if user_id is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired call session token")
    else:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    call = await calls_collection.find_one({"call_id": call_id})
    if not call:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Call not found")
    if user_id not in (call.get("caller_id"), call.get("callee_id")):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You weren't a participant on this call")

    events = []
    cursor = analytics_events_collection.find({"call_id": call_id}).sort("timestamp", 1)
    async for e in cursor:
        e["_id"] = str(e["_id"])
        events.append(e)
    return events
