import base64
import hashlib
import hmac
import time

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Central app configuration, loaded from environment variables / .env file.
    See .env.example for the full list of variables.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Mongo
    mongo_uri: str = "mongodb://localhost:27017"
    mongo_db_name: str = "freelance_app"

    # JWT
    jwt_secret_key: str = "dev-secret-change-me"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 1440

    # CORS
    cors_origins: str = "*"

    # WebRTC ICE servers
    stun_urls: str = "stun:stun.l.google.com:19302"
    # TURN relay -- see infra/coturn/turnserver.conf and
    # docs/TURN_SERVER_SETUP.md for how to stand this up. turn_urls should
    # list the same server reachable over udp/tcp/tls, e.g.
    # "turn:turn.example.com:3478?transport=udp,turn:turn.example.com:3478?transport=tcp,turns:turn.example.com:5349?transport=tcp"
    turn_urls: str = ""
    # Shared secret configured as `static-auth-secret` in turnserver.conf.
    # NEVER put a long-lived TURN username/password in the app or client --
    # this secret lets the backend mint short-lived credentials per user
    # instead (coturn's REST API credential mechanism, RFC 5389-adjacent
    # convention coturn implements natively via lt-cred-mech + this secret).
    turn_shared_secret: str = ""
    turn_credential_ttl_seconds: int = 3600

    # Role -> permission mapping. Only VIDEO_CALL_INITIATE is checked today
    # (see ws_manager.py's call:invite handler). Which roles hold it is
    # deliberately not a hardcoded `role == "doctor"` comparison -- the
    # Command Centre epic (§6) requires "the exact role list should remain
    # configurable." Override via env without a code/redeploy change to the
    # signaling logic itself.
    video_call_initiate_roles: str = "doctor"

    # Off by default: the epic (§21) requires audio-only fallback be
    # explicitly approved by Business/Medical before it's allowed at all,
    # and every audio-only call logged distinctly (see calls.audio_only_
    # fallback_occurred). Flip to true only once that approval exists.
    audio_only_auto_fallback_enabled: bool = False

    # Epic §28 / §3: "call session/room with a time-limited token", called
    # out explicitly as missing ("no separate time-limited call-session
    # token beyond call_id + JWT"). Short-lived, scoped to one (call_id,
    # user_id) pair -- see call_session_token()/verify_call_session_token()
    # below for what this is (and isn't) used for today.
    call_session_token_ttl_seconds: int = 600

    @property
    def video_call_initiate_role_set(self) -> set[str]:
        return {r.strip() for r in self.video_call_initiate_roles.split(",") if r.strip()}

    def has_permission(self, role: str, permission: str) -> bool:
        if permission == "VIDEO_CALL_INITIATE":
            return role in self.video_call_initiate_role_set
        return False

    @property
    def cors_origin_list(self) -> list[str]:
        if self.cors_origins == "*":
            return ["*"]
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    def turn_credentials(self, user_id: str) -> tuple[str, str] | None:
        """
        Generate a short-lived (turn_credential_ttl_seconds) TURN
        username/password pair for a specific user, per coturn's
        static-auth-secret / REST API convention:
            username = "<unix-expiry-timestamp>:<user_id>"
            password = base64(HMAC-SHA1(shared_secret, username))
        coturn (configured with `use-auth-secret` + the same
        `static-auth-secret`) derives the same password independently and
        accepts the credential until the embedded timestamp expires --
        nothing long-lived is ever handed to a client. Returns None if no
        TURN server is configured (falls back to STUN-only ICE).
        """
        if not self.turn_shared_secret:
            return None
        expiry = int(time.time()) + self.turn_credential_ttl_seconds
        username = f"{expiry}:{user_id}"
        digest = hmac.new(self.turn_shared_secret.encode("utf-8"), username.encode("utf-8"), hashlib.sha1).digest()
        password = base64.b64encode(digest).decode("utf-8")
        return username, password

    def call_session_token(self, call_id: str, user_id: str) -> tuple[str, int]:
        """
        Epic §28/§3's "call session/room with a time-limited token" --
        distinct from the user's JWT (which authenticates them generally,
        for however long the JWT lives) and distinct from call_id itself
        (which just names the call, isn't a credential). This proves "this
        specific user was a legitimate participant on this specific call,
        as of recently" and expires quickly on its own.

        Same HMAC-over-a-colon-joined-payload construction as
        turn_credentials() above, deliberately -- one signing pattern for
        every short-lived, self-verifying credential this backend mints,
        rather than pulling in a second token format/library.

        Today, nothing in this codebase actually gates access on this
        token -- the REST endpoints that act on a call_id (notes, events)
        already enforce participant membership directly against the call
        record, which doesn't expire and can't be replayed independently
        of the JWT, so it's arguably the stronger check for those. This
        exists for the boundary the epic is actually describing: handing
        a call-scoped credential to something that shouldn't hold the full
        user JWT -- a future native CallKit/Telecom action handler, or an
        embedded widget -- once a concrete consumer exists. Issued at
        call:accept (see ws_manager.py) and via GET
        /calls/{call_id}/session-token while the call is live.

        Returns (token, expires_at_unix_timestamp).
        """
        expiry = int(time.time()) + self.call_session_token_ttl_seconds
        payload = f"{call_id}:{user_id}:{expiry}"
        sig = hmac.new(self.jwt_secret_key.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
        return f"{payload}:{sig}", expiry

    def verify_call_session_token(self, token: str, call_id: str, user_id: str) -> bool:
        """True iff `token` was minted by call_session_token() for exactly
        this (call_id, user_id) pair, and hasn't expired yet."""
        try:
            t_call_id, t_user_id, t_expiry, t_sig = token.split(":", 3)
        except (ValueError, AttributeError):
            return False
        if t_call_id != call_id or t_user_id != user_id:
            return False
        expected_payload = f"{t_call_id}:{t_user_id}:{t_expiry}"
        expected_sig = hmac.new(self.jwt_secret_key.encode("utf-8"), expected_payload.encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected_sig, t_sig):
            return False
        try:
            return int(t_expiry) >= int(time.time())
        except ValueError:
            return False

    def ice_servers(self, user_id: str) -> list[dict]:
        servers = [{"urls": [u.strip() for u in self.stun_urls.split(",") if u.strip()]}]
        if self.turn_urls:
            creds = self.turn_credentials(user_id)
            if creds:
                username, credential = creds
                servers.append(
                    {
                        "urls": [u.strip() for u in self.turn_urls.split(",") if u.strip()],
                        "username": username,
                        "credential": credential,
                    }
                )
        return servers


settings = Settings()
