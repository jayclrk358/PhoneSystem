"""Live updates for the web UI (Server-Sent Events).

The browser keeps one ``EventSource`` open on ``/api/events``. Each message
lists topics that changed (see ``services/events.py``); the page re-reads
that data through the normal API. The stream re-checks the sign-in every
``KEEPALIVE_SECONDS`` and ends when the session is gone.
"""

import asyncio
import json

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from .. import security
from .deps import State, User

router = APIRouter(prefix="/api", tags=["events"])

KEEPALIVE_SECONDS = 15.0
# Let a burst of changes (e.g. many phone log lines) arrive as one message.
COALESCE_SECONDS = 0.25


def _message(event: str, data: object) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@router.get("/events")
async def events(request: Request, state: State, _user: User) -> StreamingResponse:
    token = request.cookies.get(security.SESSION_COOKIE)
    sub = state.bus.subscribe()

    def still_signed_in() -> bool:
        with state.db.session() as session:
            return security.user_for_token(session, token) is not None

    async def stream():
        try:
            # retry: how long the browser waits before reconnecting.
            yield "retry: 3000\n" + _message("ready", {})
            while True:
                topics = await sub.next(timeout=KEEPALIVE_SECONDS)
                if await request.is_disconnected():
                    return
                if topics is None:
                    if not await asyncio.to_thread(still_signed_in):
                        yield _message("signed-out", {})
                        return
                    yield ": keepalive\n\n"
                    continue
                await asyncio.sleep(COALESCE_SECONDS)
                topics |= sub.drain()
                yield _message("change", sorted(topics))
        finally:
            state.bus.unsubscribe(sub)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )
