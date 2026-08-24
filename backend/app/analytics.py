"""
Structured analytics events + operational logging (epic §35, §36).

Before this, the only logging in this backend was Python's default
unhandled-exception traceback -- no structured events for the things an
operator or a product analytics pipeline would actually want to query
("how many calls failed with NO_ANSWER last week", "how often does
permission_denied fire", "what's our audio-only-fallback rate"). This
module is intentionally small: one function, called at each call
lifecycle transition already happening in ws_manager.py and routers/,
that does two things every time --

  1. Writes a structured JSON log line via the standard `logging` module
     (so it shows up in whatever log aggregation the deployment already
     has, no new infrastructure required), and
  2. Inserts a lightweight, append-only document into
     analytics_events_collection (so it's queryable without needing a
     separate log pipeline yet).

Event types emitted today: call_initiated, call_connected, call_declined,
call_cancelled, call_no_answer, call_ended, call_dropped,
permission_denied, consent_denied, caller_busy, patient_busy,
audio_only_fallback. This isn't necessarily the exact event taxonomy the
epic's own analytics section specifies -- that level of detail needs the
epic's full §36 event list, which wasn't available when this was built --
but it covers every call-lifecycle transition this backend currently
produces, which is the operationally useful part today.
"""
import logging
from datetime import datetime, timezone

from app.database import analytics_events_collection

logger = logging.getLogger("video_call.analytics")


async def emit_event(event_type: str, **fields) -> None:
    now = datetime.now(timezone.utc)
    doc = {"event_type": event_type, "timestamp": now, **fields}
    # Structured log line first -- this must never be skipped even if the
    # DB write below fails, since it's the cheaper/more reliable of the
    # two paths (no network round-trip, no schema).
    logger.info("analytics_event", extra={"event_type": event_type, **fields})
    try:
        await analytics_events_collection.insert_one(doc)
    except Exception:
        # Analytics is best-effort -- a DB hiccup here must never break
        # call signaling itself. Log and move on.
        logger.exception("failed to persist analytics event %s", event_type)
        # Epic §35: a write failure here usually means Mongo itself is
        # unreachable or degraded -- exactly the kind of thing an operator
        # needs to know about, not just have sitting in a log file. Import
        # is local to avoid a circular import (alerting.py doesn't import
        # analytics, but keeping it local here makes that non-dependency
        # explicit rather than accidental).
        from app.alerting import raise_alert

        try:
            await raise_alert("analytics_write_failed", f"Could not persist analytics event {event_type}", event_type=event_type)
        except Exception:
            logger.exception("raise_alert itself failed for analytics_write_failed")
