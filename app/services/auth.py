from __future__ import annotations
"""Authentication service — password hashing and verification."""

import hashlib
import hmac
import os

from app.config import Config
from app.services.rate_limit import RateLimiter

MIN_PASSWORD_LENGTH = 8
_PIN_ITERATIONS = 100_000
_PIN_PREFIX = "pbkdf2$"

LOGIN_LIMITER = RateLimiter(max_events=10, window_seconds=300)
PIN_LIMITER = RateLimiter(max_events=5, window_seconds=300)


def hash_pin(pin: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", pin.encode(), salt, _PIN_ITERATIONS)
    return f"{_PIN_PREFIX}{salt.hex()}${dk.hex()}"


def is_hashed_pin(stored: str) -> bool:
    return stored.startswith(_PIN_PREFIX)


def verify_pin(stored: str, attempt: str) -> bool:
    """Check a PIN attempt against a stored value. Rows written before
    PIN hashing hold the PIN itself; those are compared directly."""
    if not stored:
        return False
    if not is_hashed_pin(stored):
        return hmac.compare_digest(stored.encode(), attempt.encode())
    try:
        salt_hex, hash_hex = stored[len(_PIN_PREFIX):].split("$", 1)
        dk = hashlib.pbkdf2_hmac("sha256", attempt.encode(),
                                 bytes.fromhex(salt_hex), _PIN_ITERATIONS)
    except ValueError:
        return False
    return hmac.compare_digest(dk.hex(), hash_hex)


class AuthService:
    def __init__(self, config: Config):
        self.config = config

    def hash_password(self, password: str) -> str:
        salt = os.urandom(16)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations=600000)
        return salt.hex() + ":" + dk.hex()

    def verify_password(self, password: str) -> bool:
        stored = self.config.parent.password_hash
        if not stored:
            return False
        try:
            salt_hex, hash_hex = stored.split(":", 1)
            salt = bytes.fromhex(salt_hex)
            dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations=600000)
            return hmac.compare_digest(dk.hex(), hash_hex)
        except (ValueError, AttributeError):
            return False

    def set_password(self, password: str) -> None:
        """Store a new password and invalidate every existing parent session."""
        self.config.parent.password_hash = self.hash_password(password)
        self.config.parent.session_epoch += 1
        self.config.save()

    def start_parent_session(self, session) -> None:
        session.pop("profile_id", None)
        session["parent_authenticated"] = True
        session["parent_epoch"] = self.config.parent.session_epoch

    def is_parent_session(self, session) -> bool:
        return bool(session.get("parent_authenticated")) and (
            session.get("parent_epoch", 0) == self.config.parent.session_epoch)
