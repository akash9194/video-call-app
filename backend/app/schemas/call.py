from pydantic import BaseModel
from datetime import datetime


class CallInitiate(BaseModel):
    callee_id: str


# Epic §30's post-call outcome options. Free-text notes are also allowed
# alongside this (see CallOut.notes) since real clinical outcomes rarely
# fit a fixed enum perfectly -- this list is a reasonable default covering
# the common cases, not a value verified against the epic's own schema
# (that level of detail needs Medical sign-off, same as the other open
# decisions in the gap-analysis doc's §40 section).
OUTCOMES = (
    "RESOLVED",
    "FOLLOW_UP_REQUIRED",
    "REFERRED",
    "ESCALATED",
    "NO_CLINICAL_ACTION",
)


class CallNotesUpdate(BaseModel):
    notes: str | None = None
    outcome: str | None = None  # one of OUTCOMES, if set
    follow_up_required: bool = False


# Epic §29's End Reasons enum. Only the ones this backend can actually
# determine are set for real; the rest exist so downstream consumers (audit
# reports, analytics) have a stable set of values to expect even before
# every producing path is wired up.
END_REASONS = (
    "COMPLETED",
    "PATIENT_DECLINED",
    "NO_ANSWER",
    "CALLER_CANCELLED",
    "PATIENT_ENDED",
    "CLINICIAN_ENDED",
    "PATIENT_BUSY",
    "CALLER_BUSY",
    "NETWORK_FAILURE",
    "APP_TERMINATED",
    "CELLULAR_CALL_INTERRUPTION",
    "PERMISSION_DENIED",
    "SERVICE_FAILURE",
)


class CallOut(BaseModel):
    call_id: str
    caller_id: str
    callee_id: str
    # Epic §13 call-state vocabulary: RINGING, CONNECTED, DECLINED,
    # NO_ANSWER, CANCELLED, ENDED, DROPPED. (BUSY is rejected before a call
    # record is ever created -- see ws_manager.py's call:invite handler --
    # so it never appears here.)
    status: str
    media: str = "video"  # how the call started -- "audio" or "video". It may have been switched mid-call.

    # Timestamps. initiated_at/ringing_at are the same instant in this
    # design (a call starts ringing the moment it's created); answered_at is
    # stamped when call:accept succeeds, same instant as started_at today --
    # the backend has no separate signal for "media actually connected"
    # (that would need an explicit client->server ack once the WebRTC
    # connection reaches 'connected', not built yet).
    initiated_at: datetime | None = None
    ringing_at: datetime | None = None
    answered_at: datetime | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    duration_seconds: int | None = None

    end_reason: str | None = None  # one of END_REASONS, set on every terminal transition

    consent_given: bool = False
    consent_at: datetime | None = None

    # Who initiated, and with what role -- part of the audit trail (§29).
    caller_role: str | None = None
    # Tenant scaffold (§6/§29) -- not yet enforced anywhere (single default
    # tenant today), but present on every record so a real multi-tenant
    # rollout doesn't require a data migration.
    tenant_id: str = "default"

    # Network-resilience counters -- genuinely tracked (see
    # ws_manager.py's peer-disconnect/reconnect handling), incremented in
    # real time as the call happens.
    interruption_count: int = 0
    reconnection_count: int = 0

    # Incremented when this call_id's callee sends call:accept without
    # consent:true (rejected, but recorded as an audit signal).
    permission_failures: int = 0

    # Set true if the call ever fell back to audio-only via the automatic
    # fallback path (gated off by default -- see Settings.audio_only_auto_
    # fallback_enabled) rather than a manual switch-to-voice. Distinct from
    # `media`, which only reflects how the call *started*.
    audio_only_fallback_occurred: bool = False

    # Self-reported by each client in call:invite/call:accept's optional
    # "platform" field ("ios" | "android" | "web") -- see ws_manager.py.
    caller_platform: str | None = None
    callee_platform: str | None = None

    # Whether this call qualifies as a verified video interaction for a
    # downstream workflow (e.g. E-Prescription, epic §30). No downstream
    # workflow exists yet, so this is always false today -- a placeholder,
    # not a real determination.
    qualifies_for_downstream_workflow: bool = False

    # Post-call notes & outcome (epic §30). Settable once via PATCH
    # /calls/{call_id}/notes by either participant, after the call has
    # ended -- see routers/calls.py.
    notes: str | None = None
    outcome: str | None = None
    follow_up_required: bool = False
    notes_added_at: datetime | None = None
    notes_added_by: str | None = None

    # Epic §23 network-quality indicator -- most recent self-reported
    # quality bucket ("good"/"fair"/"poor") from each side, keyed by
    # user_id. Live updates happen over the call:network-quality
    # signaling message (see ws_manager.py); this is just the
    # last-known-value snapshot for call history / post-call review.
    last_network_quality: dict[str, str] = {}


class CallSessionTokenResponse(BaseModel):
    # See Settings.call_session_token for what this is and isn't used for.
    token: str
    expires_at: int  # unix timestamp


class IceServersResponse(BaseModel):
    ice_servers: list[dict]
    # Lets clients know at call-setup time whether they're allowed to run
    # the automatic audio-only fallback loop at all -- see Settings.
    # audio_only_auto_fallback_enabled and the epic's §21 requirement that
    # this be explicitly approved before it's permitted.
    audio_only_auto_fallback_enabled: bool = False
