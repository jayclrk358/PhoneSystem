from collections.abc import Iterator
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from ..config import AppConfig
from ..db import Database
from ..models import AdminUser
from ..security import SESSION_COOKIE, LoginThrottle, user_for_token
from ..services.confgen import ConfigManager
from ..services.firmware import FirmwareStore


@dataclass
class AppState:
    cfg: AppConfig
    db: Database
    config_manager: ConfigManager
    firmware: FirmwareStore
    login_throttle: LoginThrottle
    # Last /api/status answer, so several open browser tabs don't each hit AMI.
    status_cache: tuple[float, dict] | None = None


def get_state(request: Request) -> AppState:
    return request.app.state.phonesystem


def get_session(state: Annotated[AppState, Depends(get_state)]) -> Iterator[Session]:
    with state.db.session() as session:
        yield session


def current_user(request: Request, session: Annotated[Session, Depends(get_session)]) -> AdminUser:
    user = user_for_token(session, request.cookies.get(SESSION_COOKIE))
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not signed in")
    return user


State = Annotated[AppState, Depends(get_state)]
DbSession = Annotated[Session, Depends(get_session)]
User = Annotated[AdminUser, Depends(current_user)]
