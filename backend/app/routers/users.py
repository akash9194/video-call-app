from fastapi import APIRouter, Depends

from app.database import users_collection
from app.auth.dependencies import get_current_user
from app.schemas.user import UserOut

router = APIRouter(prefix="/users", tags=["users"])


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
    users = []
    async for u in users_collection.find({"_id": {"$ne": current_user["_id"]}}):
        users.append(
            UserOut(
                id=str(u["_id"]),
                name=u["name"],
                email=u["email"],
                role=u["role"],
                is_online=u.get("is_online", False),
            )
        )
    return users
