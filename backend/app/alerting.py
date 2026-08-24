"""
Epic §35: alerting layer on top of the structured logging/analytics that
already existed (app/analytics.py). Before this, an operational failure
(an unhandled exception in signaling message handling, a database write
failure) only ever showed up as a log line -- nobody gets notified, and
there's nothing queryable without grepping logs. This module is that
missing piece: a single raise_alert() call, wired at the places in this
codebase where a `logger.exception(...)` already marks a genuine
operational failure.

Deliberately does NOT attempt threshold/anomaly-based alerting (e.g. "N
permission_denied events in 5 minutes means something's wrong"). That
needs tuning against real production traffic this build has never seen --
a half-tuned threshold is worse than none, either noisy enough to be
ignored or loose enough to never fire. What's here instead is simpler and
more honest: every genuine failure alerts immediately, every alert is
always logged at ERROR and always persisted (so alerting works with zero
external integrations configured -- see alerts_collection), and is
OPTIONALLY forwarded to a webhook if one is configured.

What counts as "alert-worthy" today (deliberately narrow -- see call
sites in routers/ws.py and analytics.py): an unhandled exception while
processing a signaling message (the exact bug class that silently
dropped a user's connection before routers/ws.py's own try/except was
added), and a failure persisting an analytics event (usually means Mongo
itself is unreachable/degraded). Routine call outcomes -- NO_ANSWER,
DECLINED, a dropped socket from a backgrounded app -- are NOT alerts;
they're normal operation, already captured as analytics events, and
alerting on them would just be noise an operator learns to ignore.
"""
import logging
from datetime import datetime, timezone

import httpx

from app.config import settings
from app.database import alerts_collection

logger = logging.getLogger("video_call.alerts")


async def raise_alert(alert_type: str, message: str, **fields) -> None:
    now = datetime.now(timezone.utc)
    doc = {"alert_type": alert_type, "message": message, "timestamp": now, **fields}

    # Logged first, unconditionally -- this must never be skipped even if
    # the DB write or webhook below fails, same reasoning as
    # analytics.emit_event: it's the cheapest, most reliable of the three
    # paths (no network round-trip, no schema, can't itself be the thing
    # that's broken).
    logger.error("ALERT [%s] %s", alert_type, message, extra={"alert_type": alert_type, **fields})

    try:
        await alerts_collection.insert_one(doc)
    except Exception:
        # An alerting-plumbing failure must never cascade into breaking
        # the very call flow it exists to monitor.
        logger.exception("failed to persist alert %s", alert_type)

    if not settings.alert_webhook_url:
        return
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            await client.post(settings.alert_webhook_url, json={"text": f"[{alert_type}] {message}"})
    except Exception:
        # The webhook is a best-effort notification, not the record of
        # truth (the DB insert above is that) -- a flaky or unreachable
        # webhook endpoint must never raise out of here and must never
        # block/slow down whatever triggered the alert.
        logger.exception("failed to deliver alert %s to webhook", alert_type)
