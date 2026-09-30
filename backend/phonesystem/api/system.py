"""Settings, firmware, config apply/rollback, live status and the audit log."""

import re
import shutil
import tempfile
import time
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import select

from ..models import AuditLog, ConfigVersion
from ..services import audit, confgen, system_settings
from ..services.ami import AmiError, list_contacts, list_endpoints
from ..services.firmware import FirmwareError
from ..services.provisioning.content import upgrade_script
from .deps import DbSession, State, User

router = APIRouter(prefix="/api", tags=["system"])

# ---------------------------------------------------------------- settings


class SettingsOut(system_settings.SystemSettings):
    detected_server_ip: str | None
    # Fixed at install time (process config), shown so the UI can build the
    # DHCP option 242 string.
    provisioning_port: int
    syslog_port: int


def _settings_out(s: system_settings.SystemSettings, state: State) -> SettingsOut:
    return SettingsOut(
        **s.model_dump(),
        detected_server_ip=system_settings.detect_server_ip(),
        provisioning_port=state.cfg.provisioning_port,
        syslog_port=state.cfg.syslog_port,
    )


@router.get("/settings")
def get_settings(session: DbSession, state: State, _user: User) -> SettingsOut:
    return _settings_out(system_settings.load(session), state)


@router.put("/settings")
def put_settings(
    body: system_settings.SystemSettings, session: DbSession, state: State, user: User
) -> SettingsOut:
    before = system_settings.load(session)
    system_settings.save(session, body)
    changed = sorted(
        k
        for k, v in body.model_dump().items()
        if getattr(before, k) != v and k != "phone_admin_password"
    )
    if before.phone_admin_password != body.phone_admin_password:
        changed.append("phone_admin_password")
    audit.record(session, user.username, "settings.update", "system", {"changed": changed})
    return _settings_out(body, state)


# ---------------------------------------------------------------- firmware


class FirmwareFileOut(BaseModel):
    name: str
    size: int
    modified: datetime
    served: bool


class FirmwareOut(BaseModel):
    files: list[FirmwareFileOut]
    upgrade_script: str
    upgrade_script_source: str  # "uploaded" | "generated"


@router.get("/firmware")
def get_firmware(state: State, _user: User) -> FirmwareOut:
    uploaded = state.firmware.upgrade_script()
    script = upgrade_script(state.firmware)
    return FirmwareOut(
        files=[FirmwareFileOut(**f.__dict__) for f in state.firmware.files()],
        upgrade_script=script.decode(errors="replace"),
        upgrade_script_source="uploaded" if uploaded else "generated",
    )


@router.post("/firmware")
def upload_firmware(file: UploadFile, state: State, session: DbSession, user: User) -> dict:
    """Upload Avaya's firmware zip (unpacked here) or a single file."""
    name = Path(file.filename or "upload").name
    try:
        if name.lower().endswith(".zip"):
            with tempfile.TemporaryDirectory() as tmp:
                tmp_zip = Path(tmp) / "upload.zip"
                with tmp_zip.open("wb") as out:
                    shutil.copyfileobj(file.file, out, 1024 * 1024)
                saved, skipped = state.firmware.import_zip(tmp_zip)
        else:
            saved, skipped = [state.firmware.save_file(name, file.file)], []
    except FirmwareError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    audit.record(session, user.username, "firmware.upload", name, {"files": saved[:50]})
    return {"saved": saved, "skipped": skipped}


@router.delete("/firmware/{name}", status_code=status.HTTP_204_NO_CONTENT)
def delete_firmware_file(name: str, state: State, session: DbSession, user: User) -> None:
    if not state.firmware.delete(name):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "file not found")
    audit.record(session, user.username, "firmware.delete", name)


@router.delete("/firmware", status_code=status.HTTP_204_NO_CONTENT)
def delete_all_firmware(state: State, session: DbSession, user: User) -> None:
    state.firmware.delete_all()
    audit.record(session, user.username, "firmware.delete_all")


# ---------------------------------------------------------------- config


class ConfigVersionOut(BaseModel):
    id: int
    created_at: datetime
    created_by: str
    checksum: str
    status: str
    message: str


class ConfigStatusOut(BaseModel):
    pending: bool
    live_checksum: str | None
    latest: ConfigVersionOut | None


_SECRET_LINE = re.compile(r"^(password=).*$", re.MULTILINE)


def _version_out(v: ConfigVersion) -> ConfigVersionOut:
    return ConfigVersionOut.model_validate(v, from_attributes=True)


@router.get("/config/status")
def config_status(state: State, session: DbSession, _user: User) -> ConfigStatusOut:
    latest = session.scalar(select(ConfigVersion).order_by(ConfigVersion.id.desc()).limit(1))
    return ConfigStatusOut(
        pending=state.config_manager.has_pending_changes(session),
        live_checksum=state.config_manager.live_checksum(),
        latest=_version_out(latest) if latest else None,
    )


@router.post("/config/apply")
def config_apply(state: State, session: DbSession, user: User) -> ConfigVersionOut:
    try:
        version = state.config_manager.apply(session, user.username)
    except ValueError as exc:  # a stored value failed re-validation
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"can't generate config: {exc}") from exc
    audit.record(
        session, user.username, "config.apply", f"v{version.id}", {"status": version.status}
    )
    return _version_out(version)


@router.get("/config/versions")
def config_versions(
    session: DbSession, _user: User, limit: int = Query(50, le=500)
) -> list[ConfigVersionOut]:
    rows = session.scalars(select(ConfigVersion).order_by(ConfigVersion.id.desc()).limit(limit))
    return [_version_out(v) for v in rows]


@router.post("/config/versions/{version_id}/rollback")
def config_rollback(
    version_id: int, state: State, session: DbSession, user: User
) -> ConfigVersionOut:
    try:
        version = state.config_manager.rollback(session, version_id, user.username)
    except confgen.ApplyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    audit.record(
        session, user.username, "config.rollback", f"v{version_id}", {"status": version.status}
    )
    return _version_out(version)


@router.get("/config/preview")
def config_preview(state: State, session: DbSession, _user: User) -> dict[str, str]:
    """The Asterisk config the current settings would generate (passwords hidden)."""
    rendered = state.config_manager.render(session)
    return {name: _SECRET_LINE.sub(r"\1********", text) for name, text in rendered.files.items()}


# ---------------------------------------------------------------- status

STATUS_CACHE_SECONDS = 2.0


@router.get("/status")
def live_status(state: State, _user: User) -> dict:
    """Asterisk reachability and which extensions are registered."""
    cached = state.status_cache
    if cached and time.monotonic() - cached[0] < STATUS_CACHE_SECONDS:
        return cached[1]
    result: dict = {"asterisk": {"reachable": False, "message": ""}, "registrations": []}
    try:
        with state.config_manager.ami_factory() as ami:
            version = ami.command("core show version").strip()
            endpoints = list_endpoints(ami)
            contacts = list_contacts(ami)
        result["asterisk"] = {"reachable": True, "message": version}
        result["endpoints"] = endpoints
        result["registrations"] = [c.__dict__ for c in contacts]
    except AmiError as exc:
        result["asterisk"]["message"] = str(exc)
    state.status_cache = (time.monotonic(), result)
    return result


# ---------------------------------------------------------------- audit


class AuditOut(BaseModel):
    at: datetime
    username: str
    action: str
    target: str
    detail: dict | None


@router.get("/audit")
def audit_log(session: DbSession, _user: User, limit: int = Query(200, le=2000)) -> list[AuditOut]:
    rows = session.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(limit))
    return [AuditOut.model_validate(r, from_attributes=True) for r in rows]
