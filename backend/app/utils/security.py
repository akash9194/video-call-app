from datetime import datetime, timedelta, timezone
import bcrypt
from jose import jwt

from app.config import settings

# Using bcrypt directly rather than via passlib: passlib hasn't been
# released since 2020 and its bcrypt backend breaks on bcrypt>=4.1 (which
# dropped an internal attribute passlib's version-detection relies on),
# raising a confusing "password cannot be longer than 72 bytes" error even
# for short passwords. This sidesteps that whole compatibility layer.
_BCRYPT_MAX_BYTES = 72  # bcrypt's own hard limit -- truncate rather than crash


def hash_password(password: str) -> str:
    pw_bytes = password.encode("utf-8")[:_BCRYPT_MAX_BYTES]
    return bcrypt.hashpw(pw_bytes, bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    pw_bytes = plain_password.encode("utf-8")[:_BCRYPT_MAX_BYTES]
    return bcrypt.checkpw(pw_bytes, hashed_password.encode("utf-8"))


def create_access_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.access_token_expire_minutes)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    except Exception:
        return None
