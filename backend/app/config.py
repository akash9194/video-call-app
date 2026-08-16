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
    turn_url: str = ""
    turn_username: str = ""
    turn_credential: str = ""

    @property
    def cors_origin_list(self) -> list[str]:
        if self.cors_origins == "*":
            return ["*"]
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def ice_servers(self) -> list[dict]:
        servers = [{"urls": [u.strip() for u in self.stun_urls.split(",") if u.strip()]}]
        if self.turn_url:
            servers.append(
                {
                    "urls": [self.turn_url],
                    "username": self.turn_username,
                    "credential": self.turn_credential,
                }
            )
        return servers


settings = Settings()
