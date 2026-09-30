from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class AdminUser(Base):
    __tablename__ = "admin_users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AdminSession(Base):
    __tablename__ = "admin_sessions"

    # SHA-256 of the session token; the token itself only lives in the cookie.
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("admin_users.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    user: Mapped[AdminUser] = relationship()


class Extension(Base):
    __tablename__ = "extensions"

    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[str] = mapped_column(String(16), unique=True)
    name: Mapped[str] = mapped_column(String(64))
    # Stored in plain text on purpose: Asterisk and the phone provisioning
    # (FORCE_SIP_PASSWORD) both need the real value. Protect the database file.
    sip_password: Mapped[str] = mapped_column(String(64))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    phone: Mapped["Phone | None"] = relationship(back_populates="extension")


class Phone(Base):
    __tablename__ = "phones"

    id: Mapped[int] = mapped_column(primary_key=True)
    # 12 lowercase hex digits, no separators.
    mac: Mapped[str] = mapped_column(String(12), unique=True)
    label: Mapped[str] = mapped_column(String(64), default="")
    model: Mapped[str] = mapped_column(String(32), default="")
    extension_id: Mapped[int | None] = mapped_column(
        ForeignKey("extensions.id", ondelete="SET NULL"), unique=True
    )
    last_ip: Mapped[str | None] = mapped_column(String(45))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    user_agent: Mapped[str | None] = mapped_column(String(255))
    firmware_version: Mapped[str | None] = mapped_column(String(64))
    # True when the phone showed up on its own (provisioning request) rather
    # than being added by an admin.
    discovered: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    extension: Mapped[Extension | None] = relationship(back_populates="phone")


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    username: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(64))
    target: Mapped[str] = mapped_column(String(128), default="")
    detail: Mapped[dict | None] = mapped_column(JSON)


class ConfigVersion(Base):
    __tablename__ = "config_versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_by: Mapped[str] = mapped_column(String(64))
    checksum: Mapped[str] = mapped_column(String(64))
    # applied | failed | superseded | rolled_back
    status: Mapped[str] = mapped_column(String(16))
    message: Mapped[str] = mapped_column(Text, default="")


class ProvisioningRequest(Base):
    __tablename__ = "provisioning_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    ip: Mapped[str] = mapped_column(String(45), index=True)
    mac: Mapped[str | None] = mapped_column(String(12), index=True)
    method: Mapped[str] = mapped_column(String(8))
    path: Mapped[str] = mapped_column(String(255))
    status: Mapped[int] = mapped_column(Integer)
    size: Mapped[int] = mapped_column(Integer, default=0)
    user_agent: Mapped[str | None] = mapped_column(String(255))


class PhoneLogLine(Base):
    __tablename__ = "phone_log_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    ip: Mapped[str] = mapped_column(String(45), index=True)
    message: Mapped[str] = mapped_column(Text)


class CallRecord(Base):
    """One call leg, imported from Asterisk's call records (CDR)."""

    __tablename__ = "call_records"
    __table_args__ = (UniqueConstraint("uniqueid", "sequence"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    # Asterisk's identifiers. Legs of the same call share linkedid.
    uniqueid: Mapped[str] = mapped_column(String(64))
    sequence: Mapped[int] = mapped_column(Integer)
    linkedid: Mapped[str] = mapped_column(String(64), index=True)

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    duration: Mapped[int] = mapped_column(Integer)  # seconds from start to end
    talk_seconds: Mapped[int] = mapped_column(Integer)  # seconds after answer

    # internal | inbound | outbound
    direction: Mapped[str] = mapped_column(String(10), index=True)
    # answered | missed | busy | failed
    status: Mapped[str] = mapped_column(String(10), index=True)
    disposition: Mapped[str] = mapped_column(String(16))  # Asterisk's raw value

    src_number: Mapped[str] = mapped_column(String(64), index=True)
    src_name: Mapped[str] = mapped_column(String(128), default="")
    dst_number: Mapped[str] = mapped_column(String(64), index=True)
    from_extension: Mapped[str | None] = mapped_column(String(16), index=True)
    to_extension: Mapped[str | None] = mapped_column(String(16), index=True)
    trunk: Mapped[str | None] = mapped_column(String(64))

    route: Mapped[str] = mapped_column(String(16), default="")
    channel: Mapped[str] = mapped_column(String(128), default="")
    dstchannel: Mapped[str] = mapped_column(String(128), default="")
    lastapp: Mapped[str] = mapped_column(String(32), default="")
