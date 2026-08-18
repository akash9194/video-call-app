"""
Validates the TURN integration end-to-end against whatever TURN server is
configured in backend/.env (TURN_URLS / TURN_SHARED_SECRET) -- local
coturn for dev, or your real deployed server from
docs/TURN_SERVER_SETUP.md. Not a mock: the backend's own
credential-generation code (app.config.Settings.turn_credentials) mints a
short-lived credential, a real ICE agent (aioice) uses it against the
real TURN server, and we confirm a genuine "relay" candidate comes back
-- plus that a forged/expired credential is correctly rejected, proving
the server is actually checking the HMAC and not just accepting anything.

If no TURN server is configured yet, this test is skipped (exit 0) rather
than failed, since TURN is optional infrastructure you set up separately
-- see docs/TURN_SERVER_SETUP.md.
"""
import asyncio
import base64
import hashlib
import hmac
import os
import re
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(SCRIPT_DIR, "..", "backend"))

from app.config import settings  # noqa: E402
import aioice  # noqa: E402

results = {"pass": [], "fail": []}


def check(name, cond):
    (results["pass"] if cond else results["fail"]).append(name)
    print(("  [PASS] " if cond else "  [FAIL] ") + name)


def parse_first_turn_host_port(turn_urls: str) -> tuple[str, int]:
    """turn:host:port?transport=udp -> (host, port). Grabs the first URL."""
    first = turn_urls.split(",")[0].strip()
    m = re.match(r"turns?:([^:?]+):(\d+)", first)
    if not m:
        raise ValueError(f"Could not parse host:port from TURN_URLS entry: {first!r}")
    return m.group(1), int(m.group(2))


async def gather_with_credentials(host, port, username, password, label):
    conn = aioice.Connection(
        ice_controlling=True,
        stun_server=(host, port),
        turn_server=(host, port),
        turn_username=username,
        turn_password=password,
        turn_transport="udp",
    )
    try:
        await asyncio.wait_for(conn.gather_candidates(), timeout=10)
    except Exception as e:
        print(f"    <{label}> gather_candidates raised: {e!r}")
    types = [c.type for c in conn.local_candidates]
    print(f"    <{label}> candidate types gathered: {types}")
    await conn.close()
    return types


async def main():
    if not settings.turn_shared_secret or not settings.turn_urls:
        print("No TURN server configured (TURN_URLS / TURN_SHARED_SECRET empty in backend/.env).")
        print("This is expected until you've run through docs/TURN_SERVER_SETUP.md -- skipping, not failing.")
        sys.exit(0)

    host, port = parse_first_turn_host_port(settings.turn_urls)
    print(f"Testing against TURN server at {host}:{port}")

    print("\nStep 1: generate real ephemeral TURN credentials using the ACTUAL backend code")
    creds = settings.turn_credentials("test-user-id-123")
    check("Settings.turn_credentials() returned a credential pair", creds is not None)
    username, password = creds
    print(f"    username={username}")

    print("\nStep 2: use these EXACT credentials with a real ICE agent (aioice) against the real TURN server")
    types = await gather_with_credentials(host, port, username, password, "valid-creds")
    check("a 'relay' candidate was obtained using the backend-generated credentials", "relay" in types)

    print("\nStep 3: confirm a WRONG credential is actually rejected")
    bad_types = await gather_with_credentials(host, port, username, "not-the-real-password", "invalid-creds")
    check("no relay candidate obtained with a forged password", "relay" not in bad_types)

    print("\nStep 4: confirm an EXPIRED credential is rejected")
    expired_username = f"{int(time.time()) - 3600}:test-user-id-123"
    digest = hmac.new(settings.turn_shared_secret.encode(), expired_username.encode(), hashlib.sha1).digest()
    expired_password = base64.b64encode(digest).decode()
    expired_types = await gather_with_credentials(host, port, expired_username, expired_password, "expired-creds")
    check("no relay candidate obtained with an expired credential", "relay" not in expired_types)

    print("\n" + "=" * 60)
    print(f"RESULT: {len(results['pass'])} passed, {len(results['fail'])} failed")
    if results["fail"]:
        print("Failed:", results["fail"])
    print("=" * 60)
    sys.exit(1 if results["fail"] else 0)


asyncio.run(main())
