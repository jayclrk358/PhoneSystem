from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, field_validator
from sqlalchemy import func, select

from .. import security
from ..models import AdminUser
from ..services import audit
from .deps import DbSession, State, User

router = APIRouter(prefix="/api/auth", tags=["auth"])


class Credentials(BaseModel):
    username: str
    password: str


class SetupRequest(BaseModel):
    username: str
    password: str

    @field_validator("username")
    @classmethod
    def _username(cls, v: str) -> str:
        v = v.strip()
        if not (1 <= len(v) <= 64) or not v.replace("_", "").replace(".", "").isalnum():
            raise ValueError("username must be 1-64 letters, digits, dots or underscores")
        return v

    @field_validator("password")
    @classmethod
    def _password(cls, v: str) -> str:
        return security.check_password_policy(v)


class PasswordChange(BaseModel):
    current_password: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def _password(cls, v: str) -> str:
        return security.check_password_policy(v)


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "?"


def _set_cookie(response: Response, state: State, token: str) -> None:
    response.set_cookie(
        security.SESSION_COOKIE,
        token,
        max_age=state.cfg.session_hours * 3600,
        httponly=True,
        secure=state.cfg.secure_cookies,
        samesite="strict",
        path="/",
    )


@router.get("/status")
def auth_status(request: Request, session: DbSession) -> dict:
    admins = session.scalar(select(func.count()).select_from(AdminUser)) or 0
    user = security.user_for_token(session, request.cookies.get(security.SESSION_COOKIE))
    return {
        "setup_required": admins == 0,
        "user": {"username": user.username} if user else None,
    }


@router.post("/setup")
def first_run_setup(body: SetupRequest, response: Response, session: DbSession, state: State):
    """Create the first admin account. Only works while there are no admins."""
    if session.scalar(select(func.count()).select_from(AdminUser)):
        raise HTTPException(status.HTTP_409_CONFLICT, "setup has already been done")
    user = AdminUser(username=body.username, password_hash=security.hash_password(body.password))
    session.add(user)
    session.flush()
    _set_cookie(response, state, security.create_session(session, user, state.cfg.session_hours))
    audit.record(session, user.username, "admin.setup", user.username)
    return {"username": user.username}


@router.post("/login")
def login(
    body: Credentials, request: Request, response: Response, session: DbSession, state: State
):
    ip = _client_ip(request)
    if state.login_throttle.blocked(ip):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, "too many failed sign-ins; wait a few minutes"
        )
    user = security.authenticate(session, body.username, body.password)
    if user is None:
        state.login_throttle.failed(ip)
        audit.record(session, body.username[:64], "admin.login_failed", ip)
        session.commit()  # keep the audit entry; the error below rolls back otherwise
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "wrong username or password")
    state.login_throttle.succeeded(ip)
    _set_cookie(response, state, security.create_session(session, user, state.cfg.session_hours))
    audit.record(session, user.username, "admin.login", ip)
    return {"username": user.username}


@router.post("/logout")
def logout(request: Request, response: Response, session: DbSession):
    security.end_session(session, request.cookies.get(security.SESSION_COOKIE))
    response.delete_cookie(security.SESSION_COOKIE, path="/")
    return {"ok": True}


@router.post("/password")
def change_password(
    body: PasswordChange, response: Response, session: DbSession, user: User, state: State
):
    if not security.verify_password(user.password_hash, body.current_password):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "current password is wrong")
    user.password_hash = security.hash_password(body.new_password)
    security.end_all_sessions(session, user)
    _set_cookie(response, state, security.create_session(session, user, state.cfg.session_hours))
    audit.record(session, user.username, "admin.password_changed", user.username)
    return {"ok": True}
