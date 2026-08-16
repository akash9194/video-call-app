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

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8123, log_level="warning")
