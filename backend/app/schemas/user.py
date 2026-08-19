from pydantic import BaseModel, EmailStr, Field


class UserSignup(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    email: EmailStr
    password: str = Field(min_length=6)
    # "doctor" can initiate calls; "patient" can never initiate one (see
    # ws_manager.handle_message's call:invite handler for enforcement).
    role: str = Field(default="patient", pattern="^(doctor|patient)$")


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


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut
