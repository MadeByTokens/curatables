from __future__ import annotations
"""CSRF token minting and validation.

Defense-in-depth on top of SameSite=Strict on the session cookie. A
signed, time-limited token is bound to a per-session nonce and must
accompany every state-mutating request. An attacker who can somehow
get a POST through the SameSite guard still cannot forge the token
without both the signing secret and the victim's session cookie.
"""

import secrets

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer


_SALT = "curatables.csrf.v1"


class CSRFService:
    def __init__(self, secret_key: str,
                 max_age_seconds: int = 3600):
        self._serializer = URLSafeTimedSerializer(secret_key, salt=_SALT)
        self.max_age = max_age_seconds

    def ensure_session_nonce(self, session: dict) -> str:
        """Mint a per-session nonce on first access, reuse thereafter.
        Mutates `session` in place — caller must be under SessionMiddleware.
        """
        nonce = session.get("_csrf_nonce")
        if not nonce:
            nonce = secrets.token_urlsafe(16)
            session["_csrf_nonce"] = nonce
        return nonce

    def mint_token(self, session: dict) -> str:
        nonce = self.ensure_session_nonce(session)
        return self._serializer.dumps(nonce)

    def validate(self, session: dict, token: str) -> bool:
        if not token:
            return False
        expected = session.get("_csrf_nonce")
        if not expected:
            return False
        try:
            payload = self._serializer.loads(token, max_age=self.max_age)
        except (BadSignature, SignatureExpired):
            return False
        return secrets.compare_digest(payload, expected)
