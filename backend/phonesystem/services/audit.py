from typing import Any

from sqlalchemy.orm import Session

from ..models import AuditLog


def record(
    session: Session,
    username: str,
    action: str,
    target: str = "",
    detail: dict[str, Any] | None = None,
) -> None:
    """Add an audit entry. Never put secrets (passwords) in ``detail``."""
    session.add(AuditLog(username=username, action=action, target=target, detail=detail))
