from pydantic import BaseModel
from datetime import datetime


class CallInitiate(BaseModel):
    callee_id: str


class CallOut(BaseModel):
    call_id: str
    caller_id: str
    callee_id: str
    status: str
    media: str = "video"  # how the call started -- "audio" or "video". It may have been switched mid-call.
    started_at: datetime | None = None
    ended_at: datetime | None = None
    duration_seconds: int | None = None


class IceServersResponse(BaseModel):
    ice_servers: list[dict]
