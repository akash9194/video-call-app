from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from bson import ObjectId

from app.utils.security import decode_access_token
from app.database import users_collection

bearer_scheme = HTTPBearer()
# auto_error=False: returns None instead of raising 401 when no Authorization
# header is present at all, so a dependency can fall back to a different
# credential (see get_current_user_optional / epic §28's call-session-token
# consumer on GET /calls/{call_id}/events) instead of every endpoint being
# forced to require a JWT.
optional_bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme)) -> dict:
    token = credentials.credentials
    payload = decode_access_token(token)
    if not payload or "sub" not in payload:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")

    user = await users_collection.find_one({"_id": ObjectId(payload["sub"])})
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return user


async def get_current_user_optional(
    credentials: HTTPAuthorizationCredentials | None = Depends(optional_bearer_scheme),
) -> dict | None:
    """Same as get_current_user, but returns None instead of raising when
    there's no (or an invalid) Authorization header, so a caller can fall
    back to a narrower credential -- e.g. a call-session token -- instead
    of being forced to hold a full user JWT. An Authorization header that
    IS present but doesn't decode to a real user still returns None here
    (not a 401) for the same reason: the caller may legitimately be
    authenticating via the fallback credential instead."""
    if credentials is None:
        return None
    payload = decode_access_token(credentials.credentials)
    if not payload or "sub" not in payload:
        return None
    return await users_collection.find_one({"_id": ObjectId(payload["sub"])})


async def get_user_id_from_token(token: str) -> str | None:
    """Used by the WebSocket signaling endpoint, which can't use HTTPBearer."""
    payload = decode_access_token(token)
    if not payload or "sub" not in payload:
        return None
    return payload["sub"]
