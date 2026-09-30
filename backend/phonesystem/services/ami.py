"""Minimal synchronous Asterisk Manager Interface (AMI) client.

Used for short request/response exchanges (reloads, status queries). Each use
opens a connection, logs in, runs actions and logs off.
"""

import itertools
import socket
from dataclasses import dataclass, field


class AmiError(Exception):
    pass


@dataclass
class AmiMessage:
    fields: dict[str, str] = field(default_factory=dict)
    # Keys that appeared more than once (e.g. "Output" lines of a Command).
    multi: dict[str, list[str]] = field(default_factory=dict)

    def get(self, key: str, default: str | None = None) -> str | None:
        return self.fields.get(key.lower(), default)

    def all(self, key: str) -> list[str]:
        return self.multi.get(key.lower(), [])


@dataclass
class AmiResponse:
    response: AmiMessage
    events: list[AmiMessage] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return (self.response.get("response") or "").lower() in ("success", "goodbye")

    @property
    def message(self) -> str:
        return self.response.get("message") or ""


def parse_message(raw: str) -> AmiMessage:
    msg = AmiMessage()
    for line in raw.split("\r\n"):
        if not line:
            continue
        key, sep, value = line.partition(":")
        if not sep:
            # Asterisk 13-style command output lines have no key.
            key, value = "output", line
        key = key.strip().lower()
        value = value.strip()
        msg.multi.setdefault(key, []).append(value)
        msg.fields.setdefault(key, value)
    return msg


class AmiClient:
    def __init__(self, host: str, port: int, username: str, secret: str, timeout: float = 5.0):
        self.host = host
        self.port = port
        self.username = username
        self.secret = secret
        self.timeout = timeout
        self._sock: socket.socket | None = None
        self._buf = b""
        self._ids = itertools.count(1)

    def __enter__(self) -> "AmiClient":
        self.connect()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def connect(self) -> None:
        try:
            self._sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        except OSError as exc:
            where = f"{self.host}:{self.port}"
            raise AmiError(f"can't connect to Asterisk AMI at {where}: {exc}") from exc
        banner = self._read_line()
        if not banner.startswith("Asterisk Call Manager"):
            raise AmiError(f"unexpected AMI banner: {banner!r}")
        resp = self.action("Login", Username=self.username, Secret=self.secret, Events="off")
        if not resp.ok:
            raise AmiError(f"AMI login failed: {resp.message}")

    def close(self) -> None:
        if self._sock is None:
            return
        try:
            self._send("Logoff", {})
        except OSError:
            pass
        self._sock.close()
        self._sock = None

    def action(self, name: str, **fields: str) -> AmiResponse:
        """Send an action and wait for its response (and event list, if any)."""
        action_id = f"ps-{next(self._ids)}"
        self._send(name, {"ActionID": action_id, **fields})
        response: AmiMessage | None = None
        events: list[AmiMessage] = []
        while True:
            msg = self._read_message()
            if msg.get("actionid") != action_id:
                continue  # unrelated event
            if response is None and msg.get("response") is not None:
                response = msg
                if (msg.get("eventlist") or "").lower() != "start":
                    return AmiResponse(response)
                continue
            if (msg.get("eventlist") or "").lower() == "complete":
                assert response is not None
                return AmiResponse(response, events)
            events.append(msg)

    def command(self, cli: str) -> str:
        """Run a CLI command and return its output."""
        resp = self.action("Command", Command=cli)
        # Asterisk answers "Error" with output when the CLI command itself
        # reports failure (e.g. "no such context"); the output says why.
        if not resp.ok and resp.message != "Command output follows":
            raise AmiError(f"command {cli!r} failed: {resp.message}")
        return "\n".join(resp.response.all("output"))

    # -- wire helpers -------------------------------------------------------

    def _send(self, name: str, fields: dict[str, str]) -> None:
        if self._sock is None:
            raise AmiError("not connected")
        lines = [f"Action: {name}"]
        for key, value in fields.items():
            if "\r" in value or "\n" in value:
                raise AmiError(f"invalid value for {key}")
            lines.append(f"{key}: {value}")
        self._sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())

    def _fill(self) -> None:
        assert self._sock is not None
        try:
            chunk = self._sock.recv(65536)
        except TimeoutError as exc:
            raise AmiError("timed out waiting for Asterisk") from exc
        if not chunk:
            raise AmiError("Asterisk closed the AMI connection")
        self._buf += chunk

    def _read_line(self) -> str:
        while b"\r\n" not in self._buf:
            self._fill()
        line, _, self._buf = self._buf.partition(b"\r\n")
        return line.decode(errors="replace")

    def _read_message(self) -> AmiMessage:
        while b"\r\n\r\n" not in self._buf:
            self._fill()
        raw, _, self._buf = self._buf.partition(b"\r\n\r\n")
        return parse_message(raw.decode(errors="replace"))


@dataclass
class ContactStatus:
    endpoint: str
    uri: str
    status: str
    user_agent: str
    via_address: str
    roundtrip_usec: str


def list_contacts(client: AmiClient) -> list[ContactStatus]:
    resp = client.action("PJSIPShowContacts")
    if not resp.ok:
        if "no contacts" in resp.message.lower():
            return []
        raise AmiError(f"PJSIPShowContacts failed: {resp.message}")
    out = []
    for ev in resp.events:
        if (ev.get("event") or "") != "ContactList":
            continue
        out.append(
            ContactStatus(
                endpoint=ev.get("endpoint") or ev.get("aor") or "",
                uri=ev.get("uri") or "",
                status=ev.get("status") or "",
                user_agent=ev.get("useragent") or "",
                via_address=ev.get("viaaddress") or "",
                roundtrip_usec=ev.get("roundtripusec") or "",
            )
        )
    return out


def list_endpoints(client: AmiClient) -> dict[str, str]:
    """Endpoint name -> device state."""
    resp = client.action("PJSIPShowEndpoints")
    if not resp.ok:
        # Asterisk answers "Error: No endpoints found" when there are none.
        if "no endpoints" in resp.message.lower():
            return {}
        raise AmiError(f"PJSIPShowEndpoints failed: {resp.message}")
    return {
        ev.get("objectname") or "": ev.get("devicestate") or ""
        for ev in resp.events
        if ev.get("event") == "EndpointList"
    }
