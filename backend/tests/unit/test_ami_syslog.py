import socket
import threading

import pytest
from sqlalchemy import select

from phonesystem.db import Database
from phonesystem.models import PhoneLogLine
from phonesystem.services import syslog
from phonesystem.services.ami import (
    AmiClient,
    AmiError,
    list_contacts,
    list_endpoints,
    parse_message,
)

# --------------------------------------------------------------------- AMI


def test_parse_message_keeps_repeated_keys():
    msg = parse_message("Response: Success\r\nOutput: line one\r\nOutput: line two\r\nX: a: b")
    assert msg.get("response") == "Success"
    assert msg.all("output") == ["line one", "line two"]
    assert msg.get("x") == "a: b"


class FakeAmiServer:
    """A tiny AMI server that answers from a script of canned replies."""

    def __init__(self, replies: dict[str, list[str]], secret: str = "s3cret"):
        self.replies = replies
        self.secret = secret
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen()
        self.port = self.sock.getsockname()[1]
        self.received: list[dict[str, str]] = []
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        conn, _ = self.sock.accept()
        conn.sendall(b"Asterisk Call Manager/9.0.0\r\n")
        buf = b""
        while True:
            chunk = conn.recv(4096)
            if not chunk:
                return
            buf += chunk
            while b"\r\n\r\n" in buf:
                raw, _, buf = buf.partition(b"\r\n\r\n")
                fields = dict(
                    line.split(": ", 1) for line in raw.decode().split("\r\n") if ": " in line
                )
                self.received.append(fields)
                action, aid = fields["Action"], fields.get("ActionID", "")
                if action == "Login":
                    ok = fields.get("Secret") == self.secret
                    reply = [
                        f"Response: {'Success' if ok else 'Error'}\r\nActionID: {aid}\r\n"
                        f"Message: {'Authentication accepted' if ok else 'Authentication failed'}"
                    ]
                elif action == "Logoff":
                    reply = [f"Response: Goodbye\r\nActionID: {aid}"]
                else:
                    reply = [r.replace("{aid}", aid) for r in self.replies[action]]
                # An unrelated event first, to make sure the client skips it.
                conn.sendall(b"Event: FullyBooted\r\nStatus: Fully Booted\r\n\r\n")
                try:
                    for r in reply:
                        conn.sendall(r.encode() + b"\r\n\r\n")
                except OSError:
                    return  # client hung up (e.g. right after Logoff)


def client_for(server: FakeAmiServer, secret: str = "s3cret") -> AmiClient:
    return AmiClient("127.0.0.1", server.port, "phonesystem", secret, timeout=3)


def test_ami_event_lists():
    server = FakeAmiServer(
        {
            "PJSIPShowEndpoints": [
                "Response: Success\r\nActionID: {aid}\r\nEventList: start",
                "Event: EndpointList\r\nActionID: {aid}\r\n"
                "ObjectName: 101\r\nDeviceState: Not in use",
                "Event: EndpointList\r\nActionID: {aid}\r\n"
                "ObjectName: 102\r\nDeviceState: Unavailable",
                "Event: EndpointListComplete\r\nActionID: {aid}\r\nEventList: Complete",
            ],
            "PJSIPShowContacts": [
                "Response: Error\r\nActionID: {aid}\r\nMessage: No Contacts found"
            ],
        }
    )
    with client_for(server) as ami:
        assert list_endpoints(ami) == {"101": "Not in use", "102": "Unavailable"}
        assert list_contacts(ami) == []
    server.thread.join(timeout=3)
    assert server.received[0]["Action"] == "Login"
    assert server.received[-1]["Action"] == "Logoff"


def test_ami_command_output():
    server = FakeAmiServer(
        {
            "Command": [
                "Response: Success\r\nActionID: {aid}\r\nMessage: Command output follows\r\n"
                "Output: Asterisk 20.6.0\r\nOutput: second line"
            ]
        }
    )
    with client_for(server) as ami:
        assert ami.command("core show version") == "Asterisk 20.6.0\nsecond line"


def test_ami_bad_login():
    server = FakeAmiServer({})
    with pytest.raises(AmiError, match="login failed"):
        client_for(server, secret="wrong").connect()


def test_ami_unreachable():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    with pytest.raises(AmiError, match="can't connect"):
        AmiClient("127.0.0.1", port, "u", "p", timeout=1).connect()


def test_ami_refuses_header_injection():
    server = FakeAmiServer({})
    with client_for(server) as ami, pytest.raises(AmiError):
        ami.action("Command", Command="core show version\r\nAction: Originate")


# ------------------------------------------------------------------ syslog


def test_clean_message():
    assert syslog.clean_message(b"<134>Sep 30 12:00:00 phone: HTTP_response[200]") == (
        "Sep 30 12:00:00 phone: HTTP_response[200]"
    )
    assert syslog.clean_message(b"<13>bad\x00\x1b[31mchars") == "bad  [31mchars"
    assert len(syslog.clean_message(b"x" * 10000)) == syslog.MAX_LINE


def test_syslog_rate_limit_and_flush(db: Database):
    rx = syslog.SyslogReceiver(db)
    for i in range(syslog.MAX_LINES_PER_SECOND + 20):
        rx.datagram_received(f"<14>line {i}".encode(), ("10.0.0.20", 514))
    rx.datagram_received(b"<14>from another phone", ("10.0.0.21", 514))
    assert len(rx.queue) == syslog.MAX_LINES_PER_SECOND + 1
    rx.flush(rx.queue)
    with db.session() as s:
        rows = s.scalars(select(PhoneLogLine).order_by(PhoneLogLine.id)).all()
    assert rows[0].message == "line 0"
    assert rows[-1].ip == "10.0.0.21"
