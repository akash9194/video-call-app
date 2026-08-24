import logging
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query

from app.alerting import raise_alert
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
                await handle_message(
                    user_id, connection_device_id, user["name"], user["role"], user.get("tenant_id", "default"), message
                )
            except Exception as exc:
                logger.exception("Error handling signaling message %r from user %s", message, user_id)
                # Epic §35: this is exactly the failure mode the alerting
                # layer exists for -- a bug here would otherwise only show
                # up as a log line an operator has to go looking for. This
                # try/except is also what keeps the bug from tearing down
                # the whole connection, so the alert fires without the
                # user being disconnected.
                try:
                    await raise_alert(
                        "signaling_handler_exception",
                        f"Unhandled exception handling {message.get('type', '?') if isinstance(message, dict) else '?'}: {exc}",
                        user_id=user_id,
                        message_type=message.get("type") if isinstance(message, dict) else None,
                    )
                except Exception:
                    # raise_alert() already guards its own DB/webhook
                    # calls internally -- this outer guard exists purely
                    # so that even a bug inside the alerting path itself
                    # can never be the thing that disconnects the user,
                    # which would defeat the entire point of this loop's
                    # inner try/except.
                    logger.exception("raise_alert itself failed for signaling_handler_exception")
    except WebSocketDisconnect:
        pass
    finally:
        await manager.disconnect(user_id, connection_device_id)
