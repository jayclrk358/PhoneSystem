"""Call log: search / number lookup, statistics, CSV export."""

from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..models import CallRecord
from ..services import calls
from .deps import DbSession, State, User

router = APIRouter(prefix="/api/calls", tags=["calls"])

Direction = Literal["inbound", "outbound", "internal"]
Status = Literal["answered", "missed", "busy", "failed"]


class CallOut(BaseModel):
    id: int
    linkedid: str
    started_at: datetime
    answered_at: datetime | None
    ended_at: datetime
    direction: str
    status: str
    src_number: str
    src_name: str
    dst_number: str
    from_extension: str | None
    to_extension: str | None
    trunk: str | None
    talk_seconds: int
    duration: int


class CallPage(BaseModel):
    total: int
    items: list[CallOut]
    # First/last contact etc. for the current search; only when searching.
    lookup: dict | None = None


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def call_filter(
    start: datetime | None = None,
    end: datetime | None = None,
    q: Annotated[str, Query(max_length=64)] = "",
    direction: Direction | None = None,
    status: Status | None = None,
    extension: Annotated[str | None, Query(max_length=16)] = None,
) -> calls.CallFilter:
    return calls.CallFilter(
        start=_aware(start),
        end=_aware(end),
        q=q,
        direction=direction,
        status=status,
        extension=extension or None,
    )


Filter = Annotated[calls.CallFilter, Depends(call_filter)]


def _refresh(state: State) -> None:
    """Pick up calls that ended since the last background import."""
    state.cdr_importer.import_new()


def _out(r: CallRecord) -> CallOut:
    data = CallOut.model_validate(r, from_attributes=True)
    data.started_at = _aware(data.started_at)
    data.answered_at = _aware(data.answered_at)
    data.ended_at = _aware(data.ended_at)
    return data


@router.get("")
def list_calls(
    flt: Filter,
    state: State,
    session: DbSession,
    _user: User,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=500)] = 50,
) -> CallPage:
    _refresh(state)
    total, rows = calls.search(session, flt, offset=(page - 1) * page_size, limit=page_size)
    lookup = None
    if flt.q.strip():
        lookup = calls.lookup_summary(session, flt)
        lookup["first_at"] = _aware(lookup["first_at"])
        lookup["last_at"] = _aware(lookup["last_at"])
    return CallPage(total=total, items=[_out(r) for r in rows], lookup=lookup)


@router.get("/stats")
def call_stats(
    state: State,
    session: DbSession,
    _user: User,
    start: datetime,
    end: datetime,
    tz: Annotated[str, Query(max_length=64)] = "UTC",
) -> dict:
    """Totals by direction/status, per-extension counts and calls per day."""
    try:
        zone = ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise HTTPException(422, "unknown time zone") from exc
    start, end = _aware(start), _aware(end)
    if end <= start:
        raise HTTPException(422, "end must be after start")
    if end - start > timedelta(days=400):
        raise HTTPException(422, "range is limited to 400 days")
    _refresh(state)
    return calls.stats(session, start, end, zone)


@router.get("/export.csv")
def export_calls(flt: Filter, state: State, _user: User):
    _refresh(state)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M")

    def rows():
        # The response streams after this function returns, so it needs its
        # own database session rather than the request's.
        with state.db.session() as session:
            yield from calls.to_csv(calls.iter_all(session, flt))

    return StreamingResponse(
        rows(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="calls-{stamp}.csv"'},
    )
