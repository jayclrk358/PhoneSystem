"""Admin passwords, sessions and login throttling."""

import hashlib
import secrets
import threading
import time
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .models import AdminSession, AdminUser

SESSION_COOKIE = "ps_session"
CSRF_HEADER = "X-Requested-With"
CSRF_VALUE = "PhoneSystem"
MIN_PASSWORD_LENGTH = 10

_hasher = PasswordHasher()
# Used to spend the same time on unknown usernames as on wrong passwords.
_DUMMY_HASH = _hasher.hash(secrets.token_hex(16))


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def check_password_policy(password: str) -> str:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
    if len(password) > 256:
        raise ValueError("password is too long")
    return password


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def authenticate(session: Session, username: str, password: str) -> AdminUser | None:
    user = session.scalar(select(AdminUser).where(AdminUser.username == username))
    if user is None:
        verify_password(_DUMMY_HASH, password)
        return None
    if not verify_password(user.password_hash, password):
        return None
    if _hasher.check_needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    return user


def create_session(session: Session, user: AdminUser, hours: int) -> str:
    now = datetime.now(UTC)
    session.execute(delete(AdminSession).where(AdminSession.expires_at < now))
    token = secrets.token_urlsafe(32)
    session.add(
        AdminSession(
            token_hash=_token_hash(token), user_id=user.id, expires_at=now + timedelta(hours=hours)
        )
    )
    user.last_login_at = now
    return token


def user_for_token(session: Session, token: str | None) -> AdminUser | None:
    if not token:
        return None
    row = session.get(AdminSession, _token_hash(token))
    if row is None:
        return None
    expires = row.expires_at
    if expires.tzinfo is None:  # SQLite drops the timezone
        expires = expires.replace(tzinfo=UTC)
    if expires < datetime.now(UTC):
        session.delete(row)
        return None
    return row.user


def end_session(session: Session, token: str | None) -> None:
    if token:
        session.execute(delete(AdminSession).where(AdminSession.token_hash == _token_hash(token)))


def end_all_sessions(session: Session, user: AdminUser) -> None:
    session.execute(delete(AdminSession).where(AdminSession.user_id == user.id))


class LoginThrottle:
    """At most ``limit`` failed logins per client IP in ``window`` seconds."""

    def __init__(self, limit: int = 5, window: float = 300):
        self.limit = limit
        self.window = window
        self._failures: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def blocked(self, ip: str) -> bool:
        with self._lock:
            recent = self._recent(ip)
            return len(recent) >= self.limit

    def failed(self, ip: str) -> None:
        with self._lock:
            self._recent(ip).append(time.monotonic())
            if len(self._failures) > 10000:
                self._failures.clear()

    def succeeded(self, ip: str) -> None:
        with self._lock:
            self._failures.pop(ip, None)

    def _recent(self, ip: str) -> list[float]:
        cutoff = time.monotonic() - self.window
        recent = [t for t in self._failures.get(ip, []) if t > cutoff]
        self._failures[ip] = recent
        return recent
