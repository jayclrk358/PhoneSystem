"""What the provisioning server hands to phones, and bookkeeping about who asked."""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ...db import Database
from ...models import Extension, Phone, ProvisioningRequest
from ...validation import check_extension_number, check_sip_password, format_mac
from .. import system_settings
from ..events import Notify
from ..firmware import FirmwareStore
from ..templating import env
from . import identify

log = logging.getLogger(__name__)

KEEP_REQUEST_ROWS = 5000


@dataclass(frozen=True)
class Identity:
    """Who sent a provisioning request.

    ``verified`` means the server's own ARP table ties ``mac`` to the request's
    IP address. Only verified phones receive login credentials.
    """

    mac: str | None
    verified: bool = False


@dataclass(frozen=True)
class PhoneLogin:
    mac: str
    extension: str
    password: str


def generated_dialplan(numbers: list[str]) -> str:
    """Avaya DIALPLAN patterns so extension numbers dial as soon as they're complete.

    A first digit that starts numbers of different lengths is left out for the
    shorter length (the phone can't know the number is complete yet).
    """
    first_digits: dict[int, set[str]] = {}
    for n in numbers:
        first_digits.setdefault(len(n), set()).add(n[0])
    patterns = []
    for length in sorted(first_digits):
        longer = set().union(*(d for ln, d in first_digits.items() if ln > length))
        digits = sorted(first_digits[length] - longer)
        if not digits:
            continue
        head = digits[0] if len(digits) == 1 else "[" + "".join(digits) + "]"
        patterns.append(head + "X" * (length - 1))
    return "|".join(patterns)


def phone_login(session: Session, mac: str) -> PhoneLogin | None:
    phone = session.scalar(select(Phone).where(Phone.mac == mac))
    if phone is None or phone.extension is None or not phone.extension.enabled:
        return None
    ext = phone.extension
    return PhoneLogin(
        mac=mac,
        extension=check_extension_number(ext.number),
        password=check_sip_password(ext.sip_password),
    )


def render_settings_file(session: Session, login: PhoneLogin | None) -> str:
    settings = system_settings.load(session)
    numbers = session.scalars(select(Extension.number).where(Extension.enabled.is_(True))).all()
    server_ip = settings.effective_server_ip() or "0.0.0.0"
    return env.get_template("avaya/46xxsettings.txt.j2").render(
        settings=settings,
        server_ip=server_ip,
        sip_domain=settings.effective_sip_domain() or server_ip,
        dialplan=settings.dialplan_override or generated_dialplan(list(numbers)),
        phone=(
            {"mac": format_mac(login.mac), "extension": login.extension, "password": login.password}
            if login
            else None
        ),
        extra=settings.extra_46xx_settings.splitlines() if settings.extra_46xx_settings else [],
    )


def upgrade_script(firmware: FirmwareStore) -> bytes:
    """Avaya's upgrade script from the uploaded firmware bundle, or our minimal one."""
    uploaded = firmware.upgrade_script()
    if uploaded is not None:
        return uploaded.read_bytes()
    return env.get_template("avaya/96x1Supgrade.txt.j2").render().encode()


class ProvisioningService:
    """Synchronous logic behind the provisioning HTTP server."""

    def __init__(
        self,
        db: Database,
        firmware: FirmwareStore,
        arp_table: Path = identify.ARP_TABLE,
        notify: Notify | None = None,
    ):
        self.db = db
        self.firmware = firmware
        self.arp_table = arp_table
        self.notify = notify
        self._inserts = 0

    def identify(self, ip: str, user_agent: str | None) -> Identity:
        arp_mac = identify.mac_from_arp(ip, self.arp_table)
        if arp_mac:
            return Identity(arp_mac, verified=True)
        # A MAC in the User-Agent is only the client's claim: good enough to
        # list the phone, never to hand out its SIP password.
        return Identity(identify.mac_from_user_agent(user_agent), verified=False)

    def settings_file(self, who: Identity) -> bytes:
        with self.db.session() as session:
            login = phone_login(session, who.mac) if who.mac and who.verified else None
            return render_settings_file(session, login).encode()

    def upgrade_script(self) -> bytes:
        return upgrade_script(self.firmware)

    def record(
        self,
        *,
        ip: str,
        mac: str | None,
        method: str,
        path: str,
        status: int,
        size: int,
        user_agent: str | None,
    ) -> None:
        now = datetime.now(UTC)
        with self.db.session() as session:
            session.add(
                ProvisioningRequest(
                    at=now,
                    ip=ip,
                    mac=mac,
                    method=method,
                    path=path[:255],
                    status=status,
                    size=size,
                    user_agent=(user_agent or "")[:255] or None,
                )
            )
            if mac:
                self._seen(session, mac, ip, user_agent, now)
            self._inserts += 1
            if self._inserts % 200 == 0:
                newest = session.scalar(select(func.max(ProvisioningRequest.id))) or 0
                session.execute(
                    delete(ProvisioningRequest).where(
                        ProvisioningRequest.id <= newest - KEEP_REQUEST_ROWS
                    )
                )
        # After the commit, so browsers that refresh see the new rows.
        if self.notify:
            topics = ("provisioning", "phones") if mac else ("provisioning",)
            self.notify(*topics)

    @staticmethod
    def _seen(session: Session, mac: str, ip: str, user_agent: str | None, now: datetime) -> None:
        phone = session.scalar(select(Phone).where(Phone.mac == mac))
        if phone is None:
            phone = Phone(mac=mac, discovered=True, label="", model="")
            session.add(phone)
            log.info("discovered new phone %s at %s", format_mac(mac), ip)
        phone.last_ip = ip
        phone.last_seen_at = now
        if user_agent:
            phone.user_agent = user_agent[:255]
            phone.model = identify.model_from_user_agent(user_agent) or phone.model
            phone.firmware_version = (
                identify.firmware_from_user_agent(user_agent) or phone.firmware_version
            )
