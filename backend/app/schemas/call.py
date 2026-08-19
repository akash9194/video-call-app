from pydantic import BaseModel
from datetime import datetime


class CallInitiate(BaseModel):
    callee_id: str


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

    # Not yet populated by either client -- both fields exist so the audit
    # schema matches the epic (§29) without a later migration, but nothing
    # sends this information to the backend today.
    caller_platform: str | None = None
    callee_platform: str | None = None

    # Whether this call qualifies as a verified video interaction for a
    # downstream workflow (e.g. E-Prescription, epic §30). No downstream
    # workflow exists yet, so this is always false today -- a placeholder,
    # not a real determination.
    qualifies_for_downstream_workflow: bool = False


class IceServersResponse(BaseModel):
    ice_servers: list[dict]
    # Lets clients know at call-setup time whether they're allowed to run
    # the automatic audio-only fallback loop at all -- see Settings.
    # audio_only_auto_fallback_enabled and the epic's §21 requirement that
    # this be explicitly approved before it's permitted.
    audio_only_auto_fallback_enabled: bool = False
