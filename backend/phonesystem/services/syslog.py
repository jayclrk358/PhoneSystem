"""UDP syslog receiver, so phone logs show up in the web UI.

The 96x1 phones log every file download (with the HTTP status they saw),
firmware signature checks and registration problems; this is the most useful
tool for diagnosing provisioning.
"""

import asyncio
import logging
import re
import time
from datetime import UTC, datetime

from sqlalchemy import delete, func, select

from ..db import Database
from ..models import PhoneLogLine

log = logging.getLogger(__name__)

KEEP_LINES = 20000
MAX_LINE = 2000
# Per source IP, to keep a chatty or hostile sender from filling the database.
MAX_LINES_PER_SECOND = 50
_PRI = re.compile(r"^<\d{1,3}>")


def clean_message(data: bytes) -> str:
    text = data.decode("utf-8", errors="replace")
    text = _PRI.sub("", text, count=1)
    text = "".join(ch if ch.isprintable() or ch == "\t" else " " for ch in text)
    return text.strip()[:MAX_LINE]


class SyslogReceiver(asyncio.DatagramProtocol):
    def __init__(self, db: Database):
        self.db = db
        self.queue: list[tuple[datetime, str, str]] = []
        self._budget: dict[str, tuple[int, int]] = {}  # ip -> (second, count)
        self._flusher: asyncio.Task | None = None
        self._flushes = 0

    def datagram_received(self, data: bytes, addr) -> None:
        ip = addr[0]
        now = int(time.monotonic())
        second, count = self._budget.get(ip, (now, 0))
        if second != now:
            second, count = now, 0
        if count >= MAX_LINES_PER_SECOND:
            return
        self._budget[ip] = (second, count + 1)
        if len(self._budget) > 10000:
            self._budget.clear()
        message = clean_message(data)
        if message:
            self.queue.append((datetime.now(UTC), ip, message))

    async def start(self, host: str, port: int) -> asyncio.DatagramTransport:
        loop = asyncio.get_running_loop()
        transport, _ = await loop.create_datagram_endpoint(lambda: self, local_addr=(host, port))
        self._flusher = asyncio.create_task(self._flush_loop())
        log.info("syslog receiver listening on %s:%s/udp", host, port)
        return transport

    async def _flush_loop(self) -> None:
        while True:
            await asyncio.sleep(1)
            if self.queue:
                batch, self.queue = self.queue, []
                try:
                    await asyncio.to_thread(self.flush, batch)
                except Exception:
                    log.exception("couldn't store phone log lines")

    def flush(self, batch: list[tuple[datetime, str, str]]) -> None:
        with self.db.session() as session:
            session.add_all(PhoneLogLine(at=at, ip=ip, message=msg) for at, ip, msg in batch)
            self._flushes += 1
            if self._flushes % 30 == 0:
                newest = session.scalar(select(func.max(PhoneLogLine.id))) or 0
                session.execute(delete(PhoneLogLine).where(PhoneLogLine.id <= newest - KEEP_LINES))
