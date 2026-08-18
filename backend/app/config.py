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
