"""Call log: import Asterisk's call records (CDR) and answer questions about them.

Asterisk appends one CSV line per call leg to ``cfg.cdr_file`` (format set in
``asterisk/cdr_custom.conf``). ``CdrImporter`` reads whatever is new since
last time and stores it as ``CallRecord`` rows, labelled with a direction
(internal / inbound / outbound) and a status (answered / missed / busy /
failed).
"""

import csv
import io
import logging
import re
import threading
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session

from ..db import Database
from ..models import CallRecord, Extension, Setting
from .events import Notify

log = logging.getLogger(__name__)

# Trunk endpoints (Phase 4) are named with this prefix, which is how a call
# leg is recognised as coming from / going to the outside world.
TRUNK_PREFIX = "trunk-"
IMPORT_STATE_KEY = "cdr_import"
# Read at most this much of the file per import pass.
MAX_READ_BYTES = 4 * 1024 * 1024

V1_FIELDS = (
    "uniqueid", "linkedid", "sequence", "start", "answer", "end", "duration", "billsec",
    "disposition", "src", "clid", "dst", "channel", "dstchannel", "lastapp",
    "ps_dialed", "ps_route",
)  # fmt: skip

DIRECTIONS = ("inbound", "outbound", "internal")
STATUSES = ("answered", "missed", "busy", "failed")
_STATUS_BY_DISPOSITION = {
    "ANSWERED": "answered",
    "NO ANSWER": "missed",
    "BUSY": "busy",
    "FAILED": "failed",
    "CONGESTION": "failed",
}
_CLID_NAME = re.compile(r'^\s*"?(.*?)"?\s*<[^>]*>\s*$', re.DOTALL)


# ------------------------------------------------------------------ parsing


def endpoint_of(channel: str) -> str | None:
    """'PJSIP/101-0000002a' -> '101'. None for empty or non-PJSIP channels."""
    if not channel.startswith("PJSIP/"):
        return None
    name = channel[len("PJSIP/") :]
    return name.rsplit("-", 1)[0] if "-" in name else name


def caller_name(clid: str) -> str:
    """'"Smith, \\"Bob\\"" <102>' -> 'Smith, "Bob"' (Asterisk backslash-escapes quotes)."""
    m = _CLID_NAME.match(clid)
    if not m:
        return ""
    return re.sub(r"\\(.)", r"\1", m.group(1)).strip()


def _epoch(value: str) -> datetime | None:
    try:
        seconds = float(value)
    except ValueError:
        return None
    return datetime.fromtimestamp(seconds, UTC) if seconds > 0 else None


def _int(value: str) -> int:
    try:
        return max(0, int(float(value)))
    except ValueError:
        return 0


def parse_line(line: str) -> dict[str, str] | None:
    """One line of the CDR file -> field dict, or None if it isn't a v1 record."""
    try:
        row = next(csv.reader([line]))
    except (csv.Error, StopIteration):
        return None
    if not row or row[0] != "v1" or len(row) != len(V1_FIELDS) + 1:
        return None
    return dict(zip(V1_FIELDS, row[1:], strict=True))


def to_record(fields: dict[str, str]) -> CallRecord | None:
    """Label one CDR leg. Returns None for records we can't make sense of."""
    started = _epoch(fields["start"])
    ended = _epoch(fields["end"]) or started
    if started is None or ended is None or not fields["uniqueid"]:
        return None

    src_ep = endpoint_of(fields["channel"])
    dst_ep = endpoint_of(fields["dstchannel"])
    src_trunk = src_ep is not None and src_ep.startswith(TRUNK_PREFIX)
    dst_trunk = dst_ep is not None and dst_ep.startswith(TRUNK_PREFIX)
    route = fields["ps_route"]
    dialed = fields["ps_dialed"] or fields["dst"]

    if src_trunk:
        direction = "inbound"
    elif dst_trunk or route in ("trunk", "invalid"):
        # "invalid": a number that isn't one of ours. Until trunks exist that
        # is a failed attempt to call outside.
        direction = "outbound"
    else:
        # "extension", "feature", or a record from dialplan we didn't tag.
        direction = "internal"

    status = _STATUS_BY_DISPOSITION.get(fields["disposition"].upper(), "failed")
    if route == "invalid":
        status = "failed"  # we answered only to play "not in service"

    from_ext = src_ep if src_ep and not src_trunk else None
    if dst_ep and not dst_trunk:
        to_ext = dst_ep
    elif route == "extension":
        to_ext = dialed  # rang an extension that couldn't be reached
    else:
        to_ext = None
    trunk = src_ep if src_trunk else (dst_ep if dst_trunk else None)

    answered = _epoch(fields["answer"])
    return CallRecord(
        uniqueid=fields["uniqueid"][:64],
        sequence=_int(fields["sequence"]),
        linkedid=(fields["linkedid"] or fields["uniqueid"])[:64],
        started_at=started,
        answered_at=answered,
        ended_at=ended,
        duration=_int(fields["duration"]),
        talk_seconds=_int(fields["billsec"]) if status == "answered" else 0,
        direction=direction,
        status=status,
        disposition=fields["disposition"][:16],
        src_number=fields["src"][:64],
        src_name=caller_name(fields["clid"])[:128],
        dst_number=dialed[:64],
        from_extension=from_ext[:16] if from_ext else None,
        to_extension=to_ext[:16] if to_ext else None,
        trunk=trunk[:64] if trunk else None,
        route=route[:16],
        channel=fields["channel"][:128],
        dstchannel=fields["dstchannel"][:128],
        lastapp=fields["lastapp"][:32],
    )


# ------------------------------------------------------------------ importing


class CdrImporter:
    """Imports new lines from the CDR file. Safe to call from several threads."""

    def __init__(self, db: Database, path: Path, notify: Notify | None = None):
        self.db = db
        self.path = path
        self.notify = notify
        self._lock = threading.Lock()
        # (inode, size) after the last pass: skip the database when unchanged.
        self._last_seen: tuple[int, int] | None = None

    def import_new(self) -> int:
        with self._lock:
            total = 0
            while True:
                imported, more = self._import_chunk()
                total += imported
                if not more:
                    break
        if total and self.notify:
            self.notify("calls")
        return total

    def _import_chunk(self) -> tuple[int, bool]:
        try:
            st = self.path.stat()
        except FileNotFoundError:
            return 0, False
        except PermissionError:
            log.warning("can't read call records at %s (permission denied)", self.path)
            return 0, False
        if (st.st_ino, st.st_size) == self._last_seen:
            return 0, False

        with self.db.session() as session:
            state_row = session.get(Setting, IMPORT_STATE_KEY)
            state = dict(state_row.value) if state_row else {}
            offset = int(state.get("offset", 0))
            # A new file (rotated/recreated) or a truncated one: start over.
            if state.get("inode") != st.st_ino or st.st_size < offset:
                offset = 0
            if st.st_size == offset:
                self._last_seen = (st.st_ino, st.st_size)
                return 0, False

            try:
                with self.path.open("rb") as f:
                    f.seek(offset)
                    data = f.read(MAX_READ_BYTES)
            except OSError as exc:
                log.warning("can't read call records at %s: %s", self.path, exc)
                return 0, False
            # Only complete lines; a partial last line is read next time.
            end = data.rfind(b"\n") + 1
            if end == 0:
                if len(data) >= MAX_READ_BYTES:  # one absurdly long line: skip it
                    end = len(data)
                else:
                    return 0, False
            records = []
            for raw in data[:end].decode("utf-8", errors="replace").splitlines():
                fields = parse_line(raw)
                record = to_record(fields) if fields else None
                if record is not None:
                    records.append(record)
                elif raw.strip():
                    log.warning("skipping unreadable call record line: %.120s", raw)

            imported = self._store(session, records)
            new_state = {"inode": st.st_ino, "offset": offset + end}
            if offset + end == st.st_size:
                self._last_seen = (st.st_ino, st.st_size)
            if state_row is None:
                session.add(Setting(key=IMPORT_STATE_KEY, value=new_state))
            else:
                state_row.value = new_state
            return imported, offset + end < st.st_size

    @staticmethod
    def _store(session: Session, records: list[CallRecord]) -> int:
        if not records:
            return 0
        # Skip legs we already have (the file may be re-read from the start).
        ids = {r.uniqueid for r in records}
        existing = set(
            session.execute(
                select(CallRecord.uniqueid, CallRecord.sequence).where(CallRecord.uniqueid.in_(ids))
            ).all()
        )
        new = []
        for r in records:
            key = (r.uniqueid, r.sequence)
            if key not in existing:
                existing.add(key)
                new.append(r)
        session.add_all(new)
        session.flush()
        return len(new)


# ------------------------------------------------------------------ queries


@dataclass
class CallFilter:
    start: datetime | None = None
    end: datetime | None = None
    q: str = ""
    direction: str | None = None
    status: str | None = None
    extension: str | None = None

    def apply(self, stmt: Select) -> Select:
        if self.start:
            stmt = stmt.where(CallRecord.started_at >= self.start)
        if self.end:
            stmt = stmt.where(CallRecord.started_at < self.end)
        if self.direction:
            stmt = stmt.where(CallRecord.direction == self.direction)
        if self.status:
            stmt = stmt.where(CallRecord.status == self.status)
        if self.extension:
            stmt = stmt.where(
                or_(
                    CallRecord.from_extension == self.extension,
                    CallRecord.to_extension == self.extension,
                )
            )
        q = self.q.strip()
        if q:
            # Search ignores spaces, dashes and brackets in phone numbers.
            digits = re.sub(r"[\s().-]", "", q)
            like = f"%{_escape_like(digits)}%"
            name_like = f"%{_escape_like(q)}%"
            stmt = stmt.where(
                or_(
                    CallRecord.src_number.like(like, escape="\\"),
                    CallRecord.dst_number.like(like, escape="\\"),
                    CallRecord.src_name.ilike(name_like, escape="\\"),
                    CallRecord.from_extension == digits,
                    CallRecord.to_extension == digits,
                )
            )
        return stmt


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def search(
    session: Session, flt: CallFilter, offset: int = 0, limit: int = 50
) -> tuple[int, list[CallRecord]]:
    total = session.scalar(flt.apply(select(func.count(CallRecord.id)))) or 0
    rows = session.scalars(
        flt.apply(select(CallRecord))
        .order_by(CallRecord.started_at.desc(), CallRecord.id.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    return total, list(rows)


def iter_all(session: Session, flt: CallFilter, batch: int = 1000) -> Iterable[CallRecord]:
    """Every matching record, newest first (for exports)."""
    stmt = flt.apply(select(CallRecord)).order_by(
        CallRecord.started_at.desc(), CallRecord.id.desc()
    )
    yield from session.scalars(stmt.execution_options(yield_per=batch))


def lookup_summary(session: Session, flt: CallFilter) -> dict:
    """First/last contact and counts for a search (a number lookup)."""
    row = session.execute(
        flt.apply(
            select(
                func.count(CallRecord.id),
                func.min(CallRecord.started_at),
                func.max(CallRecord.started_at),
            )
        )
    ).one()
    by_status = dict(
        session.execute(
            flt.apply(select(CallRecord.status, func.count(CallRecord.id))).group_by(
                CallRecord.status
            )
        ).all()
    )
    return {
        "total": row[0],
        "first_at": row[1],
        "last_at": row[2],
        "answered": by_status.get("answered", 0),
        "missed": by_status.get("missed", 0),
    }


def stats(session: Session, start: datetime, end: datetime, tz: ZoneInfo) -> dict:
    """Totals, per-extension numbers and a per-day series for [start, end)."""
    rows = session.execute(
        select(
            CallRecord.started_at,
            CallRecord.direction,
            CallRecord.status,
            CallRecord.talk_seconds,
            CallRecord.from_extension,
            CallRecord.to_extension,
        ).where(CallRecord.started_at >= start, CallRecord.started_at < end)
    ).all()

    totals = {"calls": 0, "talk_seconds": 0, "inbound_missed": 0}
    totals.update({d: 0 for d in DIRECTIONS})
    totals.update({s: 0 for s in STATUSES})
    per_ext: dict[str, dict[str, int]] = defaultdict(
        lambda: {
            "outgoing": 0,
            "outgoing_external": 0,
            "incoming": 0,
            "incoming_external": 0,
            "answered": 0,
            "missed": 0,
            "talk_seconds": 0,
        }
    )
    days: dict[date, dict[str, int]] = {}
    first_day = start.astimezone(tz).date()
    last_day = (end - timedelta(microseconds=1)).astimezone(tz).date()
    d = first_day
    while d <= last_day and len(days) < 400:
        days[d] = {k: 0 for k in DIRECTIONS}
        d += timedelta(days=1)

    for started, direction, status, talk, from_ext, to_ext in rows:
        totals["calls"] += 1
        totals[direction] = totals.get(direction, 0) + 1
        totals[status] = totals.get(status, 0) + 1
        totals["talk_seconds"] += talk
        if direction == "inbound" and status != "answered":
            totals["inbound_missed"] += 1
        if started.tzinfo is None:  # SQLite drops the timezone
            started = started.replace(tzinfo=UTC)
        bucket = days.get(started.astimezone(tz).date())
        if bucket is not None:
            bucket[direction] = bucket.get(direction, 0) + 1
        if from_ext:
            e = per_ext[from_ext]
            e["outgoing"] += 1
            e["outgoing_external"] += direction == "outbound"
            e["talk_seconds"] += talk
        if to_ext:
            e = per_ext[to_ext]
            e["incoming"] += 1
            e["incoming_external"] += direction == "inbound"
            e["answered"] += status == "answered"
            e["missed"] += status in ("missed", "busy")
            if to_ext != from_ext:
                e["talk_seconds"] += talk

    extensions = {
        e.number: e for e in session.scalars(select(Extension).order_by(Extension.number)).all()
    }
    per_extension = []
    for number in sorted(set(extensions) | set(per_ext), key=lambda n: (len(n), n)):
        ext = extensions.get(number)
        per_extension.append(
            {
                "extension": number,
                "name": ext.name if ext else "",
                "phone": (ext.phone.label or ext.phone.mac) if ext and ext.phone else None,
                "exists": ext is not None,
                **per_ext.get(number, per_ext.default_factory()),
            }
        )
    daily = [{"date": d.isoformat(), **counts} for d, counts in sorted(days.items())]
    return {"totals": totals, "per_extension": per_extension, "daily": daily}


def to_csv(records: Iterable[CallRecord]) -> Iterable[str]:
    """CSV text for an export, one chunk per row."""
    header = [
        "started_at", "answered_at", "ended_at", "direction", "status", "from_number",
        "from_name", "to_number", "from_extension", "to_extension", "talk_seconds",
        "duration_seconds", "trunk", "linkedid",
    ]  # fmt: skip
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(header)
    yield buf.getvalue()
    for r in records:
        buf.seek(0)
        buf.truncate()
        writer.writerow(
            [
                _iso(r.started_at),
                _iso(r.answered_at),
                _iso(r.ended_at),
                r.direction,
                r.status,
                _csv_safe(r.src_number),
                _csv_safe(r.src_name),
                _csv_safe(r.dst_number),
                r.from_extension or "",
                r.to_extension or "",
                r.talk_seconds,
                r.duration,
                r.trunk or "",
                r.linkedid,
            ]  # fmt: skip
        )
        yield buf.getvalue()


def _iso(value: datetime | None) -> str:
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat()


def _csv_safe(value: str) -> str:
    # Caller names come from outside callers; stop spreadsheets from running
    # them as formulas.
    return "'" + value if value[:1] in ("=", "+", "-", "@") else value
