"""Small signed-session service for the first standalone vertical slice."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass

from ..settings import Settings


class AuthenticationError(ValueError):
    """Raised when credentials or a session token cannot be accepted."""


@dataclass(frozen=True, slots=True)
class AuthenticatedUser:
    username: str
    display_name: str
    role: str = "admin"

    def as_dict(self) -> dict[str, str]:
        return {
            "username": self.username,
            "display_name": self.display_name,
            "role": self.role,
        }


def _urlsafe(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _unurlsafe(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


class AuthService:
    """Issue short-lived signed bearer tokens without a database dependency.

    The in-process user store is deliberately a development boundary. A later
    milestone replaces it with PostgreSQL-backed users and server-side session
    revocation without changing the HTTP contract.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._user = AuthenticatedUser(settings.admin_username, settings.admin_username)
        salt = hashlib.sha256((settings.session_secret + ":password").encode()).digest()
        self._password_digest = hashlib.pbkdf2_hmac(
            "sha256",
            settings.admin_password.encode(),
            salt,
            120_000,
        )

    def authenticate(self, username: str, password: str) -> AuthenticatedUser:
        if not hmac.compare_digest(username, self._user.username):
            raise AuthenticationError("invalid credentials")
        salt = hashlib.sha256((self._settings.session_secret + ":password").encode()).digest()
        candidate = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 120_000)
        if not hmac.compare_digest(candidate, self._password_digest):
            raise AuthenticationError("invalid credentials")
        return self._user

    def issue_token(self, user: AuthenticatedUser) -> str:
        now = int(time.time())
        payload = {
            "sub": user.username,
            "iat": now,
            "exp": now + self._settings.token_ttl_seconds,
        }
        encoded = _urlsafe(json.dumps(payload, separators=(",", ":")).encode())
        signature = hmac.new(
            self._settings.session_secret.encode(), encoded.encode(), hashlib.sha256
        ).digest()
        return f"{encoded}.{_urlsafe(signature)}"

    def verify_token(self, token: str) -> AuthenticatedUser:
        try:
            encoded, supplied_signature = token.split(".", 1)
            expected_signature = _urlsafe(
                hmac.new(
                    self._settings.session_secret.encode(), encoded.encode(), hashlib.sha256
                ).digest()
            )
            if not hmac.compare_digest(supplied_signature, expected_signature):
                raise AuthenticationError("invalid session")
            payload = json.loads(_unurlsafe(encoded))
            if payload.get("sub") != self._user.username or int(payload["exp"]) <= int(time.time()):
                raise AuthenticationError("expired session")
        except (AuthenticationError, AttributeError, KeyError, ValueError, TypeError, json.JSONDecodeError) as error:
            raise AuthenticationError("invalid session") from error
        return self._user
