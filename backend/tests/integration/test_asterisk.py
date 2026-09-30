"""End-to-end tests against a real Asterisk, with SIPp standing in for phones.

Run with:  pytest -m integration
Needs the ``asterisk`` and ``sipp`` commands (Debian/Ubuntu: asterisk, sip-tester).
"""

import shutil
import subprocess
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from phonesystem.config import AppConfig
from phonesystem.db import Database
from phonesystem.migrate import upgrade
from phonesystem.models import CallRecord, Extension
from phonesystem.services import calls, system_settings
from phonesystem.services.ami import list_contacts, list_endpoints
from phonesystem.services.confgen import ConfigManager

from .harness import AMI_SECRET, AsteriskInstance, free_port, free_tcp_udp_port

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not (shutil.which("asterisk") and shutil.which("sipp")),
        reason="needs asterisk and sipp installed",
    ),
]

SIPP = Path(__file__).parent / "sipp"
ALICE = ("101", "Alice", "abcdefgh12345")
BOB = ("102", "Bob Smith", "zyxwvuts98765")


class Stack:
    def __init__(self, root: Path):
        self.root = root
        data = root / "data"
        data.mkdir()
        cfg = AppConfig(data_dir=data, ami_secret=AMI_SECRET)
        upgrade(cfg.db_url)
        self.db = Database(cfg.db_url)
        self.asterisk = AsteriskInstance(root / "asterisk", cfg.current_config_link)
        self.cfg = cfg.model_copy(
            update={"ami_port": self.asterisk.ami_port, "cdr_file": self.asterisk.cdr_file}
        )
        self.config = ConfigManager(self.cfg)
        self.sip_port = free_tcp_udp_port()
        with self.db.session() as s:
            st = system_settings.load(s)
            st.sip_tcp_port = self.sip_port
            system_settings.save(s, st)

    def add_extension(self, number: str, name: str, password: str, enabled: bool = True) -> None:
        with self.db.session() as s:
            s.add(Extension(number=number, name=name, sip_password=password, enabled=enabled))

    def apply(self):
        with self.db.session() as s:
            return self.config.apply(s, "test")

    def sipp(self, *args: str, name: str) -> list[str]:
        """Common SIPp arguments (TCP, one call, logs under the test dir)."""
        return [
            "sipp", f"127.0.0.1:{self.sip_port}", "-t", "t1", "-i", "127.0.0.1",
            "-p", str(free_port()), "-m", "1", "-nostdin",
            "-trace_msg", "-message_file", str(self.root / f"{name}_msgs.log"),
            "-trace_err", "-error_file", str(self.root / f"{name}_err.log"),
            *args,
        ]  # fmt: skip

    def start_phone(self, ext: tuple[str, str, str], incoming: str) -> subprocess.Popen:
        """Register ``ext`` over TCP; ``incoming`` is the SIPp scenario for calls to it."""
        n = len(list(self.root.glob("phone*_msgs.log")))
        proc = subprocess.Popen(  # noqa: S603
            self.sipp(
                "-sf",
                str(SIPP / "register_and_wait.xml"),
                "-oocsf",
                str(SIPP / incoming),
                "-s",
                ext[0],
                "-au",
                ext[0],
                "-ap",
                ext[2],
                name=f"phone{n}",
            ),  # fmt: skip
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            with self.asterisk.ami() as ami:
                if any(c.endpoint == ext[0] for c in list_contacts(ami)):
                    return proc
            time.sleep(0.2)
        proc.kill()
        raise AssertionError(f"{ext[0]} never registered")

    def call(self, ext: tuple[str, str, str], number: str, scenario: str = "call.xml") -> int:
        dest = self.root / "dest.csv"
        dest.write_text(f"SEQUENTIAL\n{number};\n")
        n = len(list(self.root.glob("call*_msgs.log")))
        return subprocess.run(  # noqa: S603
            self.sipp(
                "-sf",
                str(SIPP / scenario),
                "-inf",
                str(dest),
                "-s",
                ext[0],
                "-au",
                ext[0],
                "-ap",
                ext[2],
                "-timeout",
                "15",
                name=f"call{n}",
            ),  # fmt: skip
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
        ).returncode


@pytest.fixture
def stack(tmp_path: Path):
    s = Stack(tmp_path)
    yield s
    s.asterisk.stop()


def test_generated_config_loads_and_reloads_live(stack: Stack):
    stack.add_extension(*ALICE)
    stack.add_extension(*BOB)
    # Before Asterisk runs, apply succeeds with a warning and the config is
    # picked up when it starts.
    v = stack.apply()
    assert v.status == "applied" and "wasn't reachable" in v.message
    stack.asterisk.start()
    with stack.asterisk.ami() as ami:
        assert set(list_endpoints(ami)) == {"101", "102"}
        transports = ami.command("pjsip show transports")
    assert f"0.0.0.0:{stack.sip_port}" in transports
    assert "transport-tcp" in transports

    stack.add_extension("103", "Carol", "cccccccc33333")
    v = stack.apply()
    assert v.status == "applied", v.message
    with stack.asterisk.ami() as ami:
        assert set(list_endpoints(ami)) == {"101", "102", "103"}
        dialplan = ami.command("dialplan show phonesystem-extensions")
    assert "'103'" in dialplan

    with stack.db.session() as s:
        s.query(Extension).filter_by(number="103").update({"enabled": False})
    assert stack.apply().status == "applied"
    with stack.asterisk.ami() as ami:
        assert set(list_endpoints(ami)) == {"101", "102"}
    with stack.db.session() as s:
        assert not stack.config.has_pending_changes(s)


def test_two_phones_register_over_tcp_and_call(stack: Stack):
    stack.add_extension(*ALICE)
    stack.add_extension(*BOB)
    stack.asterisk.start()
    assert stack.apply().status == "applied"

    # Bob registers over TCP (like a 9608) and answers incoming calls.
    callee = subprocess.Popen(  # noqa: S603
        stack.sipp(
            "-sf",
            str(SIPP / "register_and_wait.xml"),
            "-oocsf",
            str(SIPP / "answer.xml"),
            "-s",
            BOB[0],
            "-au",
            BOB[0],
            "-ap",
            BOB[2],
            name="callee",
        ),  # fmt: skip
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 10
        contacts = []
        while time.monotonic() < deadline:
            with stack.asterisk.ami() as ami:
                contacts = list_contacts(ami)
            if contacts:
                break
            time.sleep(0.2)
        assert contacts, "Bob never registered"
        assert contacts[0].endpoint == "102"
        assert "transport=tcp" in contacts[0].uri.lower()

        # Alice calls Bob.
        dest = stack.root / "dest.csv"
        dest.write_text(f"SEQUENTIAL\n{BOB[0]};\n")
        caller = subprocess.run(  # noqa: S603
            stack.sipp(
                "-sf",
                str(SIPP / "call.xml"),
                "-inf",
                str(dest),
                "-s",
                ALICE[0],
                "-au",
                ALICE[0],
                "-ap",
                ALICE[2],
                "-timeout",
                "15",
                name="caller",
            ),  # fmt: skip
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
        )
        assert caller.returncode == 0, (stack.root / "caller_err.log").read_text(errors="replace")
        assert callee.wait(timeout=20) == 0
    finally:
        if callee.poll() is None:
            callee.kill()

    # Bob's phone was told who is calling, the way a 9608 reads it.
    invite = (stack.root / "callee_msgs.log").read_text(errors="replace")
    assert 'P-Asserted-Identity: "Alice" <sip:101@' in invite


def test_wrong_password_is_rejected(stack: Stack):
    stack.add_extension(*ALICE)
    stack.asterisk.start()
    assert stack.apply().status == "applied"
    result = subprocess.run(  # noqa: S603
        stack.sipp(
            "-sf",
            str(SIPP / "register_and_wait.xml"),
            "-s",
            ALICE[0],
            "-au",
            ALICE[0],
            "-ap",
            "wrongpassword",
            "-timeout",
            "5",
            name="badpass",
        ),  # fmt: skip
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=20,
    )
    assert result.returncode != 0
    with stack.asterisk.ami() as ami:
        assert list_contacts(ami) == []


def test_calls_are_recorded_and_labelled(stack: Stack):
    """Real calls through Asterisk end up in the call log with the right labels."""
    stack.add_extension(*ALICE)
    stack.add_extension(*BOB)
    stack.add_extension("103", "Carol", "cccccccc33333")  # never registers
    stack.asterisk.start()
    assert stack.apply().status == "applied"

    bob = stack.start_phone(BOB, "answer.xml")
    try:
        assert stack.call(ALICE, "102") == 0  # answered
    finally:
        bob.kill()
        bob.wait()
    assert stack.call(ALICE, "103") != 0  # offline extension
    assert stack.call(ALICE, "5551234") == 0  # not one of ours: "not in service"
    assert stack.call(ALICE, "*43") == 0  # echo test

    bob = stack.start_phone(BOB, "ring_only.xml")
    try:
        assert stack.call(ALICE, "102", "call_cancel.xml") == 0  # rings, nobody answers
    finally:
        bob.kill()

    time.sleep(0.5)  # let Asterisk finish writing the last record
    importer = calls.CdrImporter(stack.db, stack.asterisk.cdr_file)
    assert importer.import_new() == 5
    assert importer.import_new() == 0
    with stack.db.session() as s:
        rows = s.scalars(select(CallRecord).order_by(CallRecord.started_at)).all()
        got = [
            (r.dst_number, r.direction, r.status, r.from_extension, r.to_extension) for r in rows
        ]
        assert got == [
            ("102", "internal", "answered", "101", "102"),
            ("103", "internal", "failed", "101", "103"),
            ("5551234", "outbound", "failed", "101", None),
            ("*43", "internal", "answered", "101", None),
            ("102", "internal", "missed", "101", "102"),
        ]
        assert rows[0].src_name == "Alice"
        assert rows[0].talk_seconds >= 1
        totals = calls.stats(
            s,
            rows[0].started_at.replace(tzinfo=UTC) - timedelta(minutes=1),
            datetime.now(UTC) + timedelta(minutes=1),
            ZoneInfo("UTC"),
        )["totals"]
    assert (totals["calls"], totals["answered"], totals["missed"], totals["failed"]) == (5, 2, 1, 2)
