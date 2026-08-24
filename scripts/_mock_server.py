"""
Starts the REAL, unmodified backend/app FastAPI application, with MongoDB
swapped for an in-memory mock (mongomock-motor). This lets the app run
without installing or starting a MongoDB server -- useful for a quick local
sanity check. For real usage, run the actual backend against real MongoDB
as described in docs/BUILD_GUIDE.md.

Not meant to be run directly -- launched as a subprocess by
verify_video_call.py.
"""
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.join(SCRIPT_DIR, "..", "backend")
sys.path.insert(0, BACKEND_DIR)

import motor.motor_asyncio  # noqa: E402
from mongomock_motor import AsyncMongoMockClient  # noqa: E402
motor.motor_asyncio.AsyncIOMotorClient = AsyncMongoMockClient  # patch before app.database imports it

os.environ.setdefault("JWT_SECRET_KEY", "local-selfcheck-secret")

from app.main import app  # noqa: E402
import uvicorn  # noqa: E402
from fastapi import APIRouter  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

# Serve the web test client from the same origin/port as the API. This is
# test-only (kept out of backend/app/main.py, which is the real production
# app) but it matters for phone testing: getUserMedia (camera/mic) requires
# a "secure context" in mobile Safari, and same-origin means a single https
# tunnel (e.g. ngrok) covers both the page and its API calls with no
# mixed-content or CORS issues -- no second tunnel needed.
WEB_CLIENT_DIR = os.path.join(SCRIPT_DIR, "..", "web-test-client")
app.mount("/test", StaticFiles(directory=WEB_CLIENT_DIR, html=True), name="test-client")

# Test-only introspection for scripts/verify_alerting.py (epic §35). There's
# no admin/operator role in the real app yet (see routers/calls.py's
# GET /{call_id}/events docstring for the same caveat), so there's nowhere
# real to check "did an alert get persisted" from outside the process --
# this exists purely so a verification script can ask, same spirit as the
# /test static mount above. Never added to backend/app/main.py.
_debug_router = APIRouter()


@_debug_router.get("/_test/alerts")
async def _test_list_alerts():
    from app.database import alerts_collection

    docs = []
    async for d in alerts_collection.find().sort("timestamp", -1):
        d["_id"] = str(d["_id"])
        docs.append(d)
    return docs


# Test-only, for scripts/verify_tenant_enforcement.py (epic §6/§28). The
# real POST /appointments already refuses to create a cross-tenant
# appointment (see routers/appointments.py), which means the normal API
# can't produce the exact bad state the WS-level tenant check in
# ws_manager.py's call:invite is meant to catch as defense-in-depth. This
# writes one directly to the DB, bypassing that check on purpose, so the
# WS-level check can be proven to catch it independently rather than just
# trusting that it would.
@_debug_router.post("/_test/seed-cross-tenant-appointment")
async def _test_seed_cross_tenant_appointment(body: dict):
    import uuid
    from datetime import datetime, timezone

    from app.database import appointments_collection

    doc = {
        "appointment_id": str(uuid.uuid4()),
        "doctor_id": body["doctor_id"],
        "doctor_name": body.get("doctor_name", "Test Doctor"),
        "patient_id": body["patient_id"],
        "patient_name": body.get("patient_name", "Test Patient"),
        "scheduled_time": datetime.now(timezone.utc),
        "status": "scheduled",
        "notes": None,
        "created_at": datetime.now(timezone.utc),
        "tenant_id": body.get("tenant_id", "default"),
    }
    await appointments_collection.insert_one(doc)
    return {"appointment_id": doc["appointment_id"]}


app.include_router(_debug_router)

# Test-only fault injection for scripts/verify_alerting.py (epic §35). The
# alerting layer needs a genuine unhandled exception inside handle_message
# to prove it's actually wired in, not just unit-tested in isolation --
# but real bugs that used to serve as that trigger get fixed once found
# (see ws_manager.py's call:invite hardening), so relying on "a real bug
# happens to exist right now" is fragile. This gives the test a reliable,
# intentional trigger instead, without adding any test-only branch to the
# real production handler in ws_manager.py.
import app.routers.ws as _ws_router_module  # noqa: E402

_original_handle_message = _ws_router_module.handle_message


async def _handle_message_with_test_hook(sender_id, sender_device_id, sender_name, sender_role, sender_tenant_id, message):
    if isinstance(message, dict) and message.get("type") == "__test:trigger_exception__":
        raise RuntimeError("deliberately injected by scripts/verify_alerting.py")
    return await _original_handle_message(sender_id, sender_device_id, sender_name, sender_role, sender_tenant_id, message)


_ws_router_module.handle_message = _handle_message_with_test_hook

if __name__ == "__main__":
    # 0.0.0.0, not 127.0.0.1: lets other devices on the same wifi (e.g. a
    # phone running Safari against the web test client) reach this server
    # by the machine's LAN IP, not just localhost on this machine.
    uvicorn.run(app, host="0.0.0.0", port=8123, log_level="warning")
