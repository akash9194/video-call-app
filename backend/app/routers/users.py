from fastapi import APIRouter, Depends

from app.database import calls_collection, users_collection
from app.auth.dependencies import get_current_user
from app.schemas.user import UserOut

router = APIRouter(prefix="/users", tags=["users"])

# Statuses that mean "this call is live right now" -- mirrors
# ConnectionManager.find_active_call_for's definition (ringing or
# connected), just computed from the persisted collection instead of the
# in-memory map since routers/ can't reach into the signaling module's state.
_ACTIVE_CALL_STATUSES = ("RINGING", "CONNECTED")


@router.get("/me", response_model=UserOut)
async def get_me(current_user: dict = Depends(get_current_user)):
    return UserOut(
        id=str(current_user["_id"]),
        name=current_user["name"],
        email=current_user["email"],
        role=current_user["role"],
        is_online=current_user.get("is_online", False),
    )


@router.get("", response_model=list[UserOut])
async def list_users(current_user: dict = Depends(get_current_user)):
    """
    List everyone except yourself, so the app can show who's available to call.
    In production, filter this by 'contacts' / an active project relationship
    rather than returning every user in the system.
    """
    # Epic §7: precompute who's currently on a call, so the UI can show
    # "Patient Busy" before the caller even tries -- one query instead of
    # N, since this list can be dozens of users on a poll interval.
    busy_user_ids: set[str] = set()
    async for c in calls_collection.find({"status": {"$in": _ACTIVE_CALL_STATUSES}}, {"caller_id": 1, "callee_id": 1}):
        busy_user_ids.add(c["caller_id"])
        busy_user_ids.add(c["callee_id"])

    users = []
    async for u in users_collection.find({"_id": {"$ne": current_user["_id"]}}):
        user_id = str(u["_id"])
        users.append(
            UserOut(
                id=user_id,
                name=u["name"],
                email=u["email"],
                role=u["role"],
                is_online=u.get("is_online", False),
                in_active_call=user_id in busy_user_ids,
            )
        )
    return users
