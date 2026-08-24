from pydantic import BaseModel, EmailStr, Field


class UserSignup(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    email: EmailStr
    password: str = Field(min_length=6)
    # "doctor" can initiate calls; "patient" can never initiate one (see
    # ws_manager.handle_message's call:invite handler for enforcement).
    role: str = Field(default="patient", pattern="^(doctor|patient)$")
    # Epic §6/§28: tenant/organization scoping. Optional and unvalidated
    # against any real tenant registry -- there isn't one yet, this repo
    # has always run as a single implicit "default" tenant. Accepting it
    # here (rather than hardcoding "default" at signup) means the
    # enforcement added throughout this round (see routers/users.py,
    # routers/appointments.py, ws_manager.py's call:invite) can actually
    # be exercised and verified now, ahead of whatever real
    # provisioning/admin flow eventually decides how a user's tenant gets
    # assigned. Omitted or blank -> "default", same as every existing
    # single-tenant deployment.
    tenant_id: str | None = Field(default=None, max_length=100)


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class UserOut(BaseModel):
    id: str
    name: str
    email: EmailStr
    role: str
    is_online: bool = False
    # Epic §7 entry-point button states -- lets the caller's UI show
    # "Patient Busy" proactively (before even attempting call:invite,
    # which would otherwise be the first time they learn this) rather
    # than only finding out after the invite is rejected. Computed from
    # calls_collection, not the live in-memory call_participants map
    # (that only exists inside the signaling module) -- see
    # routers/users.py's list_users.
    in_active_call: bool = False
    # Epic §6/§28. Exposed mainly for admin/debugging visibility -- the
    # mobile/web clients don't need to read or act on this today, since
    # tenant scoping is enforced server-side (same principle as every
    # other access check in this codebase: never trust the client to
    # police itself).
    tenant_id: str = "default"


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut
