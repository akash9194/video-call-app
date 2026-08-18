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
from fastapi.staticfiles import StaticFiles  # noqa: E402

# Serve the web test client from the same origin/port as the API. This is
# test-only (kept out of backend/app/main.py, which is the real production
# app) but it matters for phone testing: getUserMedia (camera/mic) requires
# a "secure context" in mobile Safari, and same-origin means a single https
# tunnel (e.g. ngrok) covers both the page and its API calls with no
# mixed-content or CORS issues -- no second tunnel needed.
WEB_CLIENT_DIR = os.path.join(SCRIPT_DIR, "..", "web-test-client")
app.mount("/test", StaticFiles(directory=WEB_CLIENT_DIR, html=True), name="test-client")

if __name__ == "__main__":
    # 0.0.0.0, not 127.0.0.1: lets other devices on the same wifi (e.g. a
    # phone running Safari against the web test client) reach this server
    # by the machine's LAN IP, not just localhost on this machine.
    uvicorn.run(app, host="0.0.0.0", port=8123, log_level="warning")
