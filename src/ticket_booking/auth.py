"""Small in-memory authentication service for the M1 single-node system."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import pbkdf2_hmac
from hmac import compare_digest
from secrets import token_urlsafe
from threading import Lock


@dataclass(frozen=True)
class LoginResult:
    status: str
    token: str = ""
    message: str = ""


@dataclass(frozen=True)
class Session:
    user_id: str
    expires_at: datetime


class AuthService:
    """Keeps credentials as derived values and sessions as short-lived opaque tokens."""

    _SALT = b"ticket-booking-demo-v1"

    def __init__(self, credentials: dict[str, str] | None = None, ttl: timedelta = timedelta(hours=1)):
        # Demo-only seeded accounts; callers can supply a real credential source later.
        credentials = credentials or {"alice": "wonderland", "bob": "builder"}
        self._credentials = {name: self._derive(password) for name, password in credentials.items()}
        self._sessions: dict[str, Session] = {}
        self._ttl = ttl
        self._lock = Lock()

    @classmethod
    def _derive(cls, password: str) -> bytes:
        return pbkdf2_hmac("sha256", password.encode("utf-8"), cls._SALT, 200_000)

    def login(self, username: str, password: str, now: datetime | None = None) -> LoginResult:
        expected = self._credentials.get(username)
        if expected is None or not compare_digest(expected, self._derive(password)):
            return LoginResult("AUTH_FAILED", message="Invalid username or password.")
        now = now or datetime.now(UTC)
        token = token_urlsafe(32)
        with self._lock:
            self._sessions[token] = Session(user_id=username, expires_at=now + self._ttl)
        return LoginResult("OK", token=token, message="Logged in.")

    def logout(self, token: str) -> bool:
        with self._lock:
            return self._sessions.pop(token, None) is not None

    def user_for_token(self, token: str, now: datetime | None = None) -> str | None:
        with self._lock:
            session = self._sessions.get(token)
            if session is None:
                return None
            if session.expires_at <= (now or datetime.now(UTC)):
                self._sessions.pop(token, None)
                return None
            return session.user_id
