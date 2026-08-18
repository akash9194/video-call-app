from fastapi import APIRouter, Depends

from app.config import settings
from app.database import calls_collection
from app.auth.dependencies import get_current_user
from app.schemas.call import CallOut, IceServersResponse

router = APIRouter(prefix="/calls", tags=["calls"])


@router.get("/ice-servers", response_model=IceServersResponse)
async def get_ice_servers(current_user: dict = Depends(get_current_user)):
    """
    The RN app fetches this right before placing/answering a call (not
    just once at startup, since TURN credentials are short-lived -- see
    Settings.turn_credentials) and passes it straight into the
    RTCPeerConnection config.
    """
    return IceServersResponse(ice_servers=settings.ice_servers(str(current_user["_id"])))


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
