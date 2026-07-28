"""Authentication, session handling and CSRF protection.

Sessions are signed cookies carrying only an operator id and issue time; no
authorisation data lives in the cookie, so revoking an operator takes effect
immediately on the next request.
"""

from __future__ import annotations

import hmac
import logging
import secrets
from dataclasses import dataclass
from typing import Any, Final

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

logger = logging.getLogger(__name__)

_hasher = PasswordHasher()

CSRF_FIELD_NAME: Final[str] = "csrf_token"
CSRF_SESSION_KEY: Final[str] = "csrf"
_SESSION_SALT: Final[str] = "quantlab.session.v1"

MINIMUM_PASSWORD_LENGTH: Final[int] = 12


class AuthenticationError(Exception):
    """Raised on invalid credentials. The message is deliberately generic."""


def hash_password(password: str) -> str:
    """Hash a password with Argon2id."""
    if len(password) < MINIMUM_PASSWORD_LENGTH:
        raise ValueError(
            f"password must be at least {MINIMUM_PASSWORD_LENGTH} characters"
        )
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    """Verify a password, returning False rather than raising on mismatch."""
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    """Whether the stored hash uses outdated Argon2 parameters."""
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


@dataclass(frozen=True)
class SessionData:
    operator_id: str
    csrf_token: str


class SessionManager:
    """Signs and verifies session cookies."""

    def __init__(self, secret_key: str, max_age_seconds: int) -> None:
        self._serializer = URLSafeTimedSerializer(secret_key, salt=_SESSION_SALT)
        self._max_age = max_age_seconds

    def issue(self, operator_id: str) -> tuple[str, SessionData]:
        """Create a new session cookie value and its decoded form."""
        data = SessionData(operator_id=operator_id, csrf_token=secrets.token_urlsafe(32))
        payload: dict[str, Any] = {
            "operator_id": data.operator_id,
            CSRF_SESSION_KEY: data.csrf_token,
        }
        return self._serializer.dumps(payload), data

    def read(self, cookie_value: str | None) -> SessionData | None:
        """Decode a cookie, returning None if absent, tampered with or expired."""
        if not cookie_value:
            return None
        try:
            payload = self._serializer.loads(cookie_value, max_age=self._max_age)
        except SignatureExpired:
            logger.info("session expired")
            return None
        except BadSignature:
            logger.warning("session signature rejected")
            return None
        if not isinstance(payload, dict):
            return None
        operator_id = payload.get("operator_id")
        csrf_token = payload.get(CSRF_SESSION_KEY)
        if not isinstance(operator_id, str) or not isinstance(csrf_token, str):
            return None
        return SessionData(operator_id=operator_id, csrf_token=csrf_token)


def csrf_tokens_match(session_token: str | None, submitted_token: str | None) -> bool:
    """Constant-time comparison of the session and submitted CSRF tokens."""
    if not session_token or not submitted_token:
        return False
    return hmac.compare_digest(session_token, submitted_token)
