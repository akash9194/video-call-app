"""
Validates the epic §35 alerting layer (app/alerting.py): raise_alert()
always logs + persists, optionally forwards to a webhook, and never raises
back out at its caller even when the DB or the webhook is broken -- that
last property matters most, since raise_alert is called from inside
exception handlers that exist specifically to keep a connection alive.

Part 1 (no server) exercises raise_alert() directly against an in-memory
Mongo (mongomock, same trick scripts/_mock_server.py uses) and a tiny
local HTTP server standing in for a webhook sink: webhook configured and
reachable, webhook configured but unreachable (must not raise), no webhook
configured (must not attempt a request), and the DB insert itself failing
(must still not raise, and the webhook path must still fire).

Part 2 runs against the real backend (scripts/_mock_server.py, which adds
a test-only /_test/alerts introspection route and a deliberate fault-
injection hook for this exact purpose -- see that file's comments):
triggers a genuine unhandled exception inside handle_message, proving the
alerting layer is actually wired into a real failure path, not just
unit-tested in isolation. Confirms the connection survives (routers/
ws.py's inner try/except + this alert firing shouldn't be mutually
exclusive) and that the alert shows up via /_test/alerts.
"""
import asyncio
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests
import websockets

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.join(SCRIPT_DIR, "..", "backend")
sys.path.insert(0, BACKEND_DIR)
os.environ.setdefault("JWT_SECRET_KEY", "local-selfcheck-secret")

BASE = "http://127.0.0.1:8123"
WS_BASE = "ws://127.0.0.1:8123"
results = {"pass": [], "fail": []}


def check(name, cond):
    (results["pass"] if cond else results["fail"]).append(name)
    print(("  [PASS] " if cond else "  [FAIL] ") + name)


# ---------------------------------------------------------------------------
# Tiny local webhook catcher -- avoids depending on any real external
# service (Slack, etc.) to prove the outbound POST actually happens.
# ---------------------------------------------------------------------------
received_payloads = []


class _CatcherHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        try:
            received_payloads.append(json.loads(body))
        except Exception:
            received_payloads.append({"_raw": body.decode("utf-8", "replace")})
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass  # keep test output clean


def start_catcher():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _CatcherHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, port


async def part1_unit_tests():
    print("=== Part 1: raise_alert() in isolation (no real backend needed) ===")

    import motor.motor_asyncio
    from mongomock_motor import AsyncMongoMockClient
    motor.motor_asyncio.AsyncIOMotorClient = AsyncMongoMockClient  # before app.database imports it

    from app.config import settings
    import app.database as database
    import app.alerting as alerting

    catcher, port = start_catcher()
    webhook_url = f"http://127.0.0.1:{port}/hook"

    # -- 1. webhook configured + reachable ---------------------------------
    settings.alert_webhook_url = webhook_url
    received_payloads.clear()
    await alerting.raise_alert("test_alert", "something broke", detail="abc")
    await asyncio.sleep(0.2)  # webhook POST happens inside raise_alert but give the catcher thread a beat
    check("alert persisted to alerts_collection", await database.alerts_collection.count_documents({"alert_type": "test_alert"}) == 1)
    saved = await database.alerts_collection.find_one({"alert_type": "test_alert"})
    check("persisted doc has the message text", saved is not None and saved.get("message") == "something broke")
    check("persisted doc carries through extra fields", saved is not None and saved.get("detail") == "abc")
    check("webhook received exactly one POST", len(received_payloads) == 1)
    check("webhook payload mentions the alert type", len(received_payloads) == 1 and "test_alert" in received_payloads[0].get("text", ""))

    # -- 2. no webhook configured -- must not attempt a request ------------
    settings.alert_webhook_url = ""
    received_payloads.clear()
    await alerting.raise_alert("test_alert_no_webhook", "no webhook configured")
    await asyncio.sleep(0.2)
    check("no webhook configured -> no HTTP request attempted", len(received_payloads) == 0)
    check("alert still persisted even with no webhook configured", await database.alerts_collection.count_documents({"alert_type": "test_alert_no_webhook"}) == 1)

    # -- 3. webhook configured but unreachable -- must not raise -----------
    settings.alert_webhook_url = "http://127.0.0.1:1/unreachable"  # port 1: nothing listens, connection refused fast
    raised = False
    try:
        await alerting.raise_alert("test_alert_bad_webhook", "webhook is down")
    except Exception:
        raised = True
    check("unreachable webhook does not raise out of raise_alert", not raised)
    check("alert still persisted even though the webhook failed", await database.alerts_collection.count_documents({"alert_type": "test_alert_bad_webhook"}) == 1)

    # -- 4. DB insert itself fails -- must still not raise, and the ---------
    #       webhook (independent of Mongo) should still fire, which matters
    #       most for the real-world case this is modeling: Mongo is down.
    settings.alert_webhook_url = webhook_url
    received_payloads.clear()
    original_insert_one = database.alerts_collection.insert_one

    async def _broken_insert_one(*a, **kw):
        raise RuntimeError("simulated Mongo outage")

    database.alerts_collection.insert_one = _broken_insert_one
    raised = False
    try:
        await alerting.raise_alert("test_alert_db_down", "db is unreachable")
    except Exception:
        raised = True
    finally:
        database.alerts_collection.insert_one = original_insert_one
    await asyncio.sleep(0.2)
    check("a broken DB insert does not raise out of raise_alert", not raised)
    check("webhook still fires even when the DB insert failed", len(received_payloads) == 1)

    # -- 5. the OTHER wiring site: analytics.emit_event's own DB write ------
    #       failure should also raise an alert (app/analytics.py, not
    #       app/alerting.py itself) -- confirms the second integration
    #       point actually calls through, not just the module in isolation.
    import app.analytics as analytics

    settings.alert_webhook_url = ""
    original_analytics_insert = database.analytics_events_collection.insert_one

    async def _broken_analytics_insert(*a, **kw):
        raise RuntimeError("simulated analytics DB outage")

    database.analytics_events_collection.insert_one = _broken_analytics_insert
    raised = False
    try:
        await analytics.emit_event("call_initiated", call_id="doesnt-matter")
    except Exception:
        raised = True
    finally:
        database.analytics_events_collection.insert_one = original_analytics_insert
    check("emit_event's own DB failure does not raise out to the caller", not raised)
    check(
        "analytics.emit_event's DB failure raised an analytics_write_failed alert",
        await database.alerts_collection.count_documents({"alert_type": "analytics_write_failed"}) == 1,
    )

    catcher.shutdown()


async def recv_until(ws, wanted_type, timeout=3):
    """
    ws_manager.py's connect() broadcasts presence:update to every active
    connection, including the one that just connected (self.
    active_connections is updated before broadcast_presence is called) --
    so the very first frame on a freshly-opened socket is routinely a
    presence:update about yourself, not whatever you're actually waiting
    for. A plain single recv() here would silently grab that instead of
    the real reply. Drain and ignore anything that isn't the wanted type.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        remaining = max(0.1, deadline - time.time())
        raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
        msg = json.loads(raw)
        if msg.get("type") == wanted_type:
            return msg
    return None


def signup(name, email, role):
    r = requests.post(f"{BASE}/auth/signup", json={"name": name, "email": email, "password": "testpass123", "role": role})
    r.raise_for_status()
    return r.json()


async def part2_live_backend():
    print("\n=== Part 2: a real unhandled exception, against the real backend ===")

    doc = signup("Dr. Alert", "alert.doc@example.com", "doctor")
    ws = await websockets.connect(f"{WS_BASE}/ws/signaling?token={doc['access_token']}&device_id=alert-doc-device")

    # This originally sent a malformed call:invite (no "to" field) to
    # trigger a real, then-existing KeyError in ws_manager.py's handler.
    # That bug got found and fixed as a direct result of running this
    # script (see ws_manager.py's call:invite hardening + the commit this
    # shipped in) -- which is exactly what should happen to a real bug,
    # but it also means the original trigger no longer crashes anything.
    # scripts/_mock_server.py installs a deliberate, test-only fault
    # injection hook for this exact reason -- see its comment for why
    # depending on "a real bug happens to still exist" would be fragile.
    await ws.send(json.dumps({"type": "__test:trigger_exception__"}))
    await asyncio.sleep(0.3)

    # The connection must have survived -- prove it by sending a normal,
    # well-formed message afterward (a call:invite to a syntactically
    # valid but nonexistent ObjectId, which the now-hardened handler
    # rejects cleanly) and confirming we get an ordinary response rather
    # than the socket being closed.
    still_alive = True
    try:
        await ws.send(json.dumps({"type": "call:invite", "to": "0" * 24, "media": "video"}))
        msg = await recv_until(ws, "error", timeout=3)
        check("connection survived the unhandled exception (got a reply to the next message)", msg is not None)
        check("the next message got a normal error reply, not a disconnect", msg is not None and msg.get("type") == "error")
    except websockets.exceptions.ConnectionClosed:
        still_alive = False
        check("connection survived the unhandled exception (got a reply to the next message)", False)
        check("the next message got a normal error reply, not a disconnect", False)

    await ws.close()

    alerts_resp = requests.get(f"{BASE}/_test/alerts")
    check("test-only /_test/alerts endpoint reachable", alerts_resp.status_code == 200)
    alerts = alerts_resp.json()
    matching = [a for a in alerts if a.get("alert_type") == "signaling_handler_exception"]
    check("a signaling_handler_exception alert was recorded for the injected exception", len(matching) >= 1)
    if matching:
        check("the alert records which user triggered it", matching[0].get("user_id") == doc["user"]["id"])
        check("the alert records the offending message type", matching[0].get("message_type") == "__test:trigger_exception__")

    return still_alive


async def main():
    await part1_unit_tests()
    await part2_live_backend()

    print("\n" + "=" * 60)
    print(f"RESULT: {len(results['pass'])} passed, {len(results['fail'])} failed")
    if results["fail"]:
        print("Failed:", results["fail"])
    print("=" * 60)
    sys.exit(1 if results["fail"] else 0)


asyncio.run(main())
