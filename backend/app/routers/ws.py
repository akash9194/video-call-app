import logging
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query

from app.auth.dependencies import get_user_id_from_token
from app.database import users_collection
from app.signaling.ws_manager import manager, handle_message
from bson import ObjectId

logger = logging.getLogger(__name__)

router = APIRouter()


@router.websocket("/ws/signaling")
async def signaling_endpoint(websocket: WebSocket, token: str = Query(...), device_id: str | None = Query(default=None)):
    user_id = await get_user_id_from_token(token)
    if not user_id:
        await websocket.close(code=4401)  # custom code: unauthorized
        return

    user = await users_collection.find_one({"_id": ObjectId(user_id)})
    if not user:
        await websocket.close(code=4404)
        return

    # Each physical connection (phone, tablet, browser tab, ...) gets its
    # own device_id so the same account can be signed in on several devices
    # at once without one connection silently evicting another. Clients may
    # supply a stable one; if they don't, a per-connection id is generated
    # here -- either way the important thing is it's unique among this
    # user's concurrently-open connections.
    connection_device_id = device_id or str(uuid.uuid4())

    await manager.connect(user_id, connection_device_id, websocket)
    try:
        while True:
            message = await websocket.receive_json()
            # A malformed/unexpected message or a bug handling one message
            # type should never tear down the whole connection -- that
            # would silently drop the user mid-session (they'd stop
            # receiving calls) until the app reconnects. Only a real
            # disconnect should end this loop.
            try:
                await handle_message(user_id, connection_device_id, user["name"], user["role"], message)
            except Exception:
                logger.exception("Error handling signaling message %r from user %s", message, user_id)
    except WebSocketDisconnect:
        pass
    finally:
        await manager.disconnect(user_id, connection_device_id)
