import re
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, field_validator
from sqlalchemy import or_, select

from .. import validation
from ..models import Extension, Phone, PhoneLogLine, ProvisioningRequest
from ..services import audit
from ..services.provisioning.content import phone_login, render_settings_file
from .deps import DbSession, User

router = APIRouter(prefix="/api", tags=["phones"])


class PhoneOut(BaseModel):
    id: int
    mac: str
    label: str
    model: str
    extension_id: int | None
    extension_number: str | None
    extension_name: str | None
    last_ip: str | None
    last_seen_at: datetime | None
    user_agent: str | None
    firmware_version: str | None
    discovered: bool
    created_at: datetime


class PhoneCreate(BaseModel):
    mac: str
    label: str = ""
    extension_id: int | None = None

    @field_validator("mac")
    @classmethod
    def _mac(cls, v: str) -> str:
        return validation.normalize_mac(v)

    @field_validator("label")
    @classmethod
    def _label(cls, v: str) -> str:
        return validation.check_display_name(v) if v.strip() else ""


class PhoneUpdate(BaseModel):
    label: str | None = None
    # Send null to unassign; leave out to keep the current assignment.
    extension_id: int | None = None

    @field_validator("label")
    @classmethod
    def _label(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return validation.check_display_name(v) if v.strip() else ""


class ProvisioningRequestOut(BaseModel):
    at: datetime
    ip: str
    mac: str | None
    method: str
    path: str
    status: int
    size: int
    user_agent: str | None


class LogLineOut(BaseModel):
    at: datetime
    ip: str
    message: str


def _out(p: Phone) -> PhoneOut:
    return PhoneOut(
        id=p.id,
        mac=validation.format_mac(p.mac),
        label=p.label,
        model=p.model,
        extension_id=p.extension_id,
        extension_number=p.extension.number if p.extension else None,
        extension_name=p.extension.name if p.extension else None,
        last_ip=p.last_ip,
        last_seen_at=p.last_seen_at,
        user_agent=p.user_agent,
        firmware_version=p.firmware_version,
        discovered=p.discovered,
        created_at=p.created_at,
    )


def _get(session, phone_id: int) -> Phone:
    phone = session.get(Phone, phone_id)
    if phone is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "phone not found")
    return phone


def _assign(session, phone: Phone, extension_id: int | None) -> None:
    if extension_id is None:
        phone.extension_id = None
        return
    ext = session.get(Extension, extension_id)
    if ext is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "extension not found")
    if ext.phone is not None and ext.phone.id != phone.id:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"extension {ext.number} is already assigned to phone "
            f"{validation.format_mac(ext.phone.mac)}",
        )
    phone.extension_id = ext.id


@router.get("/phones")
def list_phones(session: DbSession, _user: User) -> list[PhoneOut]:
    phones = session.scalars(select(Phone).order_by(Phone.label, Phone.mac))
    return [_out(p) for p in phones]


@router.post("/phones", status_code=status.HTTP_201_CREATED)
def create_phone(body: PhoneCreate, session: DbSession, user: User) -> PhoneOut:
    if session.scalar(select(Phone).where(Phone.mac == body.mac)):
        raise HTTPException(status.HTTP_409_CONFLICT, "a phone with that MAC already exists")
    phone = Phone(mac=body.mac, label=body.label, model="", discovered=False)
    session.add(phone)
    session.flush()
    _assign(session, phone, body.extension_id)
    session.flush()
    audit.record(session, user.username, "phone.create", validation.format_mac(phone.mac))
    return _out(phone)


@router.patch("/phones/{phone_id}")
def update_phone(phone_id: int, body: PhoneUpdate, session: DbSession, user: User) -> PhoneOut:
    phone = _get(session, phone_id)
    changes: dict[str, object] = {}
    if body.label is not None and body.label != phone.label:
        changes["label"] = [phone.label, body.label]
        phone.label = body.label
    if "extension_id" in body.model_fields_set and body.extension_id != phone.extension_id:
        _assign(session, phone, body.extension_id)
        changes["extension_id"] = body.extension_id
    session.flush()
    session.refresh(phone)
    if changes:
        audit.record(
            session, user.username, "phone.update", validation.format_mac(phone.mac), changes
        )
    return _out(phone)


@router.delete("/phones/{phone_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_phone(phone_id: int, session: DbSession, user: User) -> None:
    phone = _get(session, phone_id)
    audit.record(session, user.username, "phone.delete", validation.format_mac(phone.mac))
    session.delete(phone)


_PASSWORD_LINE = re.compile(r"^(SET FORCE_SIP_PASSWORD) .*$", re.MULTILINE)


@router.get("/phones/{phone_id}/settings-file")
def phone_settings_file(
    phone_id: int, session: DbSession, _user: User, reveal: bool = False
) -> dict:
    """The 46xxsettings.txt this phone receives when it asks for it."""
    phone = _get(session, phone_id)
    text = render_settings_file(session, phone_login(session, phone.mac))
    if not reveal:
        text = _PASSWORD_LINE.sub(r"\1 ********", text)
    return {"content": text}


@router.get("/phones/{phone_id}/requests")
def phone_requests(
    phone_id: int, session: DbSession, _user: User, limit: int = Query(100, le=1000)
) -> list[ProvisioningRequestOut]:
    phone = _get(session, phone_id)
    cond = ProvisioningRequest.mac == phone.mac
    if phone.last_ip:
        cond = or_(cond, ProvisioningRequest.ip == phone.last_ip)
    rows = session.scalars(
        select(ProvisioningRequest).where(cond).order_by(ProvisioningRequest.id.desc()).limit(limit)
    )
    return [ProvisioningRequestOut.model_validate(r, from_attributes=True) for r in rows]


@router.get("/phones/{phone_id}/logs")
def phone_logs(
    phone_id: int, session: DbSession, _user: User, limit: int = Query(200, le=2000)
) -> list[LogLineOut]:
    phone = _get(session, phone_id)
    if not phone.last_ip:
        return []
    rows = session.scalars(
        select(PhoneLogLine)
        .where(PhoneLogLine.ip == phone.last_ip)
        .order_by(PhoneLogLine.id.desc())
        .limit(limit)
    )
    return [LogLineOut.model_validate(r, from_attributes=True) for r in rows]


@router.get("/provisioning/requests")
def recent_requests(
    session: DbSession, _user: User, limit: int = Query(200, le=2000)
) -> list[ProvisioningRequestOut]:
    """All recent provisioning requests, including ones from unidentified phones."""
    rows = session.scalars(
        select(ProvisioningRequest).order_by(ProvisioningRequest.id.desc()).limit(limit)
    )
    return [ProvisioningRequestOut.model_validate(r, from_attributes=True) for r in rows]


@router.get("/provisioning/logs")
def recent_logs(
    session: DbSession, _user: User, limit: int = Query(200, le=2000)
) -> list[LogLineOut]:
    rows = session.scalars(select(PhoneLogLine).order_by(PhoneLogLine.id.desc()).limit(limit))
    return [LogLineOut.model_validate(r, from_attributes=True) for r in rows]
