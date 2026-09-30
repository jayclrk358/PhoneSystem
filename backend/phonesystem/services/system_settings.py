"""System-wide settings an admin edits in the web UI (stored as one JSON row)."""

import ipaddress
import re
import secrets
import socket

from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from ..models import Setting

SETTINGS_KEY = "system"


class SystemSettings(BaseModel):
    # The phones need a numeric IPv4 address in SIP_CONTROLLER_LIST.
    server_ip: str | None = None
    sip_domain: str | None = None
    sip_tcp_port: int = Field(5060, ge=1, le=65535)

    # Phone "craft" (local admin) menu password, PROCPSWD. Digits only.
    phone_admin_password: str = ""

    ntp_server: str = "pool.ntp.org"
    gmt_offset: str = "-5:00"
    dst_offset: int = Field(1, ge=0, le=2)
    dst_start: str = "2SunMar2L"
    dst_stop: str = "1SunNov2L"

    register_interval: int = Field(300, ge=30, le=86400)
    inter_digit_timeout: int = Field(3, ge=1, le=10)
    # Replaces the generated DIALPLAN when set.
    dialplan_override: str = ""
    # Ask phones to send their syslog to this server.
    phone_syslog: bool = True
    # Raw lines appended to 46xxsettings.txt (escape hatch for any setting the
    # UI doesn't cover yet). Each line must be a SET/GET/IF/GOTO/# statement.
    extra_46xx_settings: str = ""

    @field_validator("server_ip")
    @classmethod
    def _ipv4(cls, v: str | None) -> str | None:
        if v in (None, ""):
            return None
        try:
            ipaddress.IPv4Address(v)
        except ValueError as exc:
            raise ValueError("server_ip must be a numeric IPv4 address") from exc
        return v

    @field_validator("sip_domain", "ntp_server")
    @classmethod
    def _hostname(cls, v: str | None) -> str | None:
        if v in (None, ""):
            return None
        if not re.fullmatch(r"[A-Za-z0-9.-]{1,253}", v):
            raise ValueError("must be a hostname or IP address")
        return v

    @field_validator("phone_admin_password")
    @classmethod
    def _craft_pw(cls, v: str) -> str:
        if v and not re.fullmatch(r"\d{4,7}", v):
            raise ValueError("phone admin password must be 4-7 digits")
        return v

    @field_validator("gmt_offset")
    @classmethod
    def _gmt(cls, v: str) -> str:
        if not re.fullmatch(r"[+-]?\d{1,2}:\d{2}", v):
            raise ValueError('GMT offset must look like "-5:00"')
        return v

    @field_validator("dst_start", "dst_stop")
    @classmethod
    def _dst_rule(cls, v: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9]{1,16}", v):
            raise ValueError('DST rule must look like "2SunMar2L"')
        return v

    @field_validator("dialplan_override")
    @classmethod
    def _dialplan(cls, v: str) -> str:
        if v and not re.fullmatch(r"[0-9XZxz*#|\[\]\-]{1,1000}", v):
            raise ValueError("dial plan may only contain digits, X, Z, *, #, |, [ ] and -")
        return v

    @field_validator("extra_46xx_settings")
    @classmethod
    def _extra(cls, v: str) -> str:
        lines = []
        for raw in v.replace("\r\n", "\n").split("\n"):
            line = raw.strip()
            if not line:
                continue
            if not re.match(r"(SET|GET|IF|GOTO)\s|#", line):
                raise ValueError(f"not a settings-file statement: {line[:40]!r}")
            lines.append(line)
        return "\n".join(lines)

    def effective_server_ip(self) -> str | None:
        return self.server_ip or detect_server_ip()

    def effective_sip_domain(self) -> str | None:
        return self.sip_domain or self.effective_server_ip()


def detect_server_ip() -> str | None:
    """Best guess at this machine's LAN address (no packets are sent)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))  # TEST-NET-1; connect() on UDP only picks a route
            ip = s.getsockname()[0]
    except OSError:
        return None
    return None if ip.startswith("127.") else ip


def load(session: Session) -> SystemSettings:
    row = session.get(Setting, SETTINGS_KEY)
    if row is None:
        settings = SystemSettings(phone_admin_password=f"{secrets.randbelow(10**6):06d}")
        session.add(Setting(key=SETTINGS_KEY, value=settings.model_dump()))
        session.flush()
        return settings
    return SystemSettings.model_validate(row.value)


def save(session: Session, settings: SystemSettings) -> None:
    row = session.get(Setting, SETTINGS_KEY)
    if row is None:
        session.add(Setting(key=SETTINGS_KEY, value=settings.model_dump()))
    else:
        row.value = settings.model_dump()
    session.flush()
