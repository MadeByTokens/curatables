from __future__ import annotations
"""Authentication service — password hashing and verification."""

import hashlib
import hmac
import os

from app.config import Config


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
        self.config.parent.password_hash = self.hash_password(password)
        self.config.save()
