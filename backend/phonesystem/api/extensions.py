from datetime import datetime

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, field_validator
from sqlalchemy import select

from .. import validation
from ..models import Extension
from ..services import audit
from .deps import DbSession, User

router = APIRouter(prefix="/api/extensions", tags=["extensions"])


class ExtensionOut(BaseModel):
    id: int
    number: str
    name: str
    enabled: bool
    phone_id: int | None
    phone_mac: str | None
    created_at: datetime
    updated_at: datetime


class ExtensionDetail(ExtensionOut):
    sip_password: str


class ExtensionCreate(BaseModel):
    number: str
    name: str
    sip_password: str | None = None
    enabled: bool = True

    @field_validator("number")
    @classmethod
    def _number(cls, v: str) -> str:
        return validation.check_extension_number(v)

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        return validation.check_display_name(v)

    @field_validator("sip_password")
    @classmethod
    def _pw(cls, v: str | None) -> str | None:
        return validation.check_sip_password(v) if v else None


class ExtensionUpdate(BaseModel):
    number: str | None = None
    name: str | None = None
    enabled: bool | None = None
    sip_password: str | None = None
    regenerate_password: bool = False

    @field_validator("number")
    @classmethod
    def _number(cls, v: str | None) -> str | None:
        return validation.check_extension_number(v) if v is not None else None

    @field_validator("name")
    @classmethod
    def _name(cls, v: str | None) -> str | None:
        return validation.check_display_name(v) if v is not None else None

    @field_validator("sip_password")
    @classmethod
    def _pw(cls, v: str | None) -> str | None:
        return validation.check_sip_password(v) if v else None


def _out(ext: Extension, detail: bool = False) -> ExtensionOut:
    data = {
        "id": ext.id,
        "number": ext.number,
        "name": ext.name,
        "enabled": ext.enabled,
        "phone_id": ext.phone.id if ext.phone else None,
        "phone_mac": validation.format_mac(ext.phone.mac) if ext.phone else None,
        "created_at": ext.created_at,
        "updated_at": ext.updated_at,
    }
    if detail:
        return ExtensionDetail(**data, sip_password=ext.sip_password)
    return ExtensionOut(**data)


def _get(session, ext_id: int) -> Extension:
    ext = session.get(Extension, ext_id)
    if ext is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "extension not found")
    return ext


def _check_number_free(session, number: str, exclude_id: int | None = None) -> None:
    existing = session.scalar(select(Extension).where(Extension.number == number))
    if existing is not None and existing.id != exclude_id:
        raise HTTPException(status.HTTP_409_CONFLICT, f"extension {number} already exists")


@router.get("")
def list_extensions(session: DbSession, _user: User) -> list[ExtensionOut]:
    return [_out(e) for e in session.scalars(select(Extension).order_by(Extension.number))]


@router.post("", status_code=status.HTTP_201_CREATED)
def create_extension(body: ExtensionCreate, session: DbSession, user: User) -> ExtensionDetail:
    _check_number_free(session, body.number)
    ext = Extension(
        number=body.number,
        name=body.name,
        sip_password=body.sip_password or validation.generate_sip_password(),
        enabled=body.enabled,
    )
    session.add(ext)
    session.flush()
    audit.record(session, user.username, "extension.create", ext.number, {"name": ext.name})
    return _out(ext, detail=True)


@router.get("/{ext_id}")
def get_extension(ext_id: int, session: DbSession, _user: User) -> ExtensionDetail:
    return _out(_get(session, ext_id), detail=True)


@router.patch("/{ext_id}")
def update_extension(
    ext_id: int, body: ExtensionUpdate, session: DbSession, user: User
) -> ExtensionDetail:
    ext = _get(session, ext_id)
    changes: dict[str, object] = {}
    if body.number is not None and body.number != ext.number:
        _check_number_free(session, body.number, exclude_id=ext.id)
        changes["number"] = [ext.number, body.number]
        ext.number = body.number
    if body.name is not None and body.name != ext.name:
        changes["name"] = [ext.name, body.name]
        ext.name = body.name
    if body.enabled is not None and body.enabled != ext.enabled:
        changes["enabled"] = body.enabled
        ext.enabled = body.enabled
    if body.regenerate_password:
        ext.sip_password = validation.generate_sip_password()
        changes["credentials"] = "SIP password regenerated"
    elif body.sip_password:
        ext.sip_password = body.sip_password
        changes["credentials"] = "SIP password changed"
    session.flush()
    if changes:
        audit.record(session, user.username, "extension.update", ext.number, changes)
    return _out(ext, detail=True)


@router.delete("/{ext_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_extension(ext_id: int, session: DbSession, user: User) -> None:
    ext = _get(session, ext_id)
    if ext.phone is not None:
        ext.phone.extension_id = None
    audit.record(session, user.username, "extension.delete", ext.number, {"name": ext.name})
    session.delete(ext)
