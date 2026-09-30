"""Phone-facing HTTP server for Avaya 96x1 SIP phones.

The 96x1 SIP firmware fetches files with an old libcurl that is picky about
responses. Some simple servers (Python's ``http.server`` is a known example)
are rejected: the phone logs ``HTTP_response[0]`` and throws the file away. So
this server answers like a classic web server: an explicit ``HTTP/1.1 200 OK``
status line, ``Content-Type``, ``Content-Length``, ``Last-Modified``,
``Connection: close``, and then it closes the connection.
"""

import asyncio
import logging
import posixpath
import re
from email.utils import formatdate
from pathlib import Path
from urllib.parse import unquote

from .content import Identity, ProvisioningService

log = logging.getLogger(__name__)

MAX_HEAD_BYTES = 8192
READ_TIMEOUT = 10.0
CHUNK = 256 * 1024
_SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_REASONS = {
    200: "OK",
    400: "Bad Request",
    404: "Not Found",
    405: "Method Not Allowed",
    500: "Internal Server Error",
}


def response_head(
    status: int, content_type: str, length: int, last_modified: float | None
) -> bytes:
    lines = [
        f"HTTP/1.1 {status} {_REASONS[status]}",
        f"Date: {formatdate(usegmt=True)}",
        "Server: PhoneSystem",
        f"Content-Type: {content_type}",
        f"Content-Length: {length}",
    ]
    if last_modified is not None:
        lines.append(f"Last-Modified: {formatdate(last_modified, usegmt=True)}")
    lines += ["Connection: close", "", ""]
    return "\r\n".join(lines).encode()


def requested_name(target: str) -> str | None:
    """The file name a phone asked for, or None if the path is unacceptable.

    Phones may prefix names with an HTTPDIR folder; only the last part counts.
    """
    path = unquote(target.split("?", 1)[0])
    if "\\" in path or "\x00" in path:
        return None
    name = posixpath.basename(posixpath.normpath("/" + path))
    return name if _SAFE_NAME.fullmatch(name) else None


class ProvisioningServer:
    def __init__(self, service: ProvisioningService):
        self.service = service

    async def start(self, host: str, port: int) -> asyncio.Server:
        server = await asyncio.start_server(self.handle, host, port, limit=MAX_HEAD_BYTES)
        log.info("provisioning server listening on %s:%s", host, port)
        return server

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        ip = peer[0] if peer else "?"
        method, path, status, size, user_agent, mac = "?", "?", 400, 0, None, None
        try:
            try:
                head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), READ_TIMEOUT)
            except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, TimeoutError):
                return
            if len(head) > MAX_HEAD_BYTES:
                status = 400
                await self._send_simple(writer, 400, "request too large\n")
                return
            request_line, *header_lines = head.decode("latin-1").split("\r\n")
            parts = request_line.split(" ")
            if len(parts) != 3 or not parts[2].startswith("HTTP/1."):
                await self._send_simple(writer, 400, "bad request\n")
                return
            method, path = parts[0], parts[1]
            headers = {}
            for line in header_lines:
                key, sep, value = line.partition(":")
                if sep:
                    headers[key.strip().lower()] = value.strip()
            user_agent = headers.get("user-agent")

            if method not in ("GET", "HEAD"):
                status = 405
                await self._send_simple(writer, 405, "method not allowed\n")
                return

            name = requested_name(path)
            if name is None:
                status = 404
                await self._send_simple(writer, 404, "not found\n", head_only=method == "HEAD")
                return

            who = await asyncio.to_thread(self.service.identify, ip, user_agent)
            mac = who.mac
            status, size = await self._serve(writer, name, who, head_only=method == "HEAD")
        except (ConnectionError, OSError) as exc:
            log.debug("provisioning connection from %s ended: %s", ip, exc)
        except Exception:
            log.exception("provisioning request from %s failed", ip)
            status = 500
            try:
                await self._send_simple(writer, 500, "internal error\n")
            except (ConnectionError, OSError):
                pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass
            if method != "?":
                try:
                    await asyncio.to_thread(
                        self.service.record,
                        ip=ip,
                        mac=mac,
                        method=method,
                        path=path,
                        status=status,
                        size=size,
                        user_agent=user_agent,
                    )
                except Exception:
                    log.exception("couldn't record provisioning request")

    async def _serve(
        self, writer: asyncio.StreamWriter, name: str, who: Identity, head_only: bool
    ) -> tuple[int, int]:
        lower = name.lower()
        if lower == "46xxsettings.txt":
            body = await asyncio.to_thread(self.service.settings_file, who)
            return await self._send_bytes(writer, body, "text/plain", head_only)
        if lower.endswith("upgrade.txt"):
            # 96x1Supgrade.txt (SIP), 96x1Hupgrade.txt (H.323 phones being
            # converted with SIG=2) and older names all get the same script.
            body = await asyncio.to_thread(self.service.upgrade_script)
            return await self._send_bytes(writer, body, "text/plain", head_only)
        path = await asyncio.to_thread(self.service.firmware.find, name)
        if path is None:
            await self._send_simple(writer, 404, "not found\n", head_only=head_only)
            return 404, 0
        return await self._send_file(writer, path, head_only)

    async def _send_bytes(
        self, writer: asyncio.StreamWriter, body: bytes, content_type: str, head_only: bool
    ) -> tuple[int, int]:
        writer.write(response_head(200, content_type, len(body), None))
        if not head_only:
            writer.write(body)
        await writer.drain()
        return 200, len(body)

    async def _send_file(
        self, writer: asyncio.StreamWriter, path: Path, head_only: bool
    ) -> tuple[int, int]:
        st = path.stat()
        content_type = "text/plain" if path.suffix.lower() == ".txt" else "application/octet-stream"
        writer.write(response_head(200, content_type, st.st_size, st.st_mtime))
        if not head_only:
            with path.open("rb") as f:
                while chunk := await asyncio.to_thread(f.read, CHUNK):
                    writer.write(chunk)
                    await writer.drain()
        await writer.drain()
        return 200, st.st_size

    async def _send_simple(
        self, writer: asyncio.StreamWriter, status: int, text: str, head_only: bool = False
    ) -> None:
        body = text.encode()
        writer.write(response_head(status, "text/plain", len(body), None))
        if not head_only:
            writer.write(body)
        await writer.drain()
