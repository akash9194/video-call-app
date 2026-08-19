from motor.motor_asyncio import AsyncIOMotorClient
from app.config import settings

# tz_aware=True: without it, pymongo/motor returns naive datetimes for
# anything read back from Mongo, while our own code stores timezone-aware
# UTC datetimes (datetime.now(timezone.utc)). Mixing the two raises
# "can't subtract offset-naive and offset-aware datetimes" the first time
# code (e.g. call duration calculation) does datetime arithmetic on a
# value read from the DB.
client = AsyncIOMotorClient(settings.mongo_uri, tz_aware=True)
db = client[settings.mongo_db_name]

users_collection = db["users"]
calls_collection = db["calls"]
appointments_collection = db["appointments"]
# Epic §36 -- structured, queryable analytics events (call_initiated,
# call_connected, call_ended, permission_denied, ...). Separate from
# calls_collection (which holds one evolving document per call) so this
# stays a pure append-only event log -- see app/analytics.py.
analytics_events_collection = db["analytics_events"]


async def ensure_indexes():
    """Call once on startup to create required indexes."""
    await users_collection.create_index("email", unique=True)
    await calls_collection.create_index("call_id", unique=True)
    await calls_collection.create_index("caller_id")
    await calls_collection.create_index("callee_id")
    await appointments_collection.create_index([("doctor_id", 1), ("patient_id", 1), ("status", 1)])
    await analytics_events_collection.create_index("call_id")
    await analytics_events_collection.create_index([("event_type", 1), ("timestamp", -1)])
