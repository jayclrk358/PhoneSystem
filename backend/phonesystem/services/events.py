"""Live updates: tell connected browsers what changed so they can refresh.

Anything that changes data (a call is imported, a phone asks for its
settings, an admin edits an extension, ...) publishes one or more *topics*.
Each open ``/api/events`` stream has a subscriber that collects them. Events
only say *what* changed; the browser then re-reads that data through the
normal (authenticated) API, so nothing sensitive travels on the stream.
"""

import asyncio
import threading
from collections.abc import Callable, Iterable

# Everything a page can listen for.
TOPICS = frozenset(
    {
        "calls",  # new call records imported
        "provisioning",  # a phone fetched a file
        "phones",  # phone list/details changed (discovery, edits)
        "phone_logs",  # new syslog lines from phones
        "status",  # registrations / Asterisk reachability changed
        "extensions",
        "settings",
        "firmware",
        "config",  # applied config / pending changes
        "audit",
    }
)

Notify = Callable[..., None]


class Subscriber:
    """One live stream. Collects topics until the stream sends them."""

    def __init__(self, loop: asyncio.AbstractEventLoop):
        self.loop = loop
        self.pending: set[str] = set()
        self.ready = asyncio.Event()

    def _add(self, topics: Iterable[str]) -> None:  # runs on self.loop
        self.pending.update(topics)
        self.ready.set()

    def drain(self) -> set[str]:
        """Whatever has arrived so far, without waiting."""
        self.ready.clear()
        topics, self.pending = self.pending, set()
        return topics

    async def next(self, timeout: float) -> set[str] | None:
        """Wait for topics; None on timeout. Bursts arrive as one set."""
        try:
            await asyncio.wait_for(self.ready.wait(), timeout)
        except TimeoutError:
            return None
        return self.drain()


class EventBus:
    """Thread-safe fan-out of change topics to live streams."""

    def __init__(self) -> None:
        self._subs: set[Subscriber] = set()
        self._lock = threading.Lock()

    def subscribe(self) -> Subscriber:
        """Call from the event loop that will consume the subscriber."""
        sub = Subscriber(asyncio.get_running_loop())
        with self._lock:
            self._subs.add(sub)
        return sub

    def unsubscribe(self, sub: Subscriber) -> None:
        with self._lock:
            self._subs.discard(sub)

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subs)

    def publish(self, *topics: str) -> None:
        """Safe to call from any thread (request handlers, importers, ...)."""
        unknown = set(topics) - TOPICS
        if unknown:
            raise ValueError(f"unknown event topics: {sorted(unknown)}")
        if not topics:
            return
        with self._lock:
            subs = list(self._subs)
        for sub in subs:
            try:
                sub.loop.call_soon_threadsafe(sub._add, topics)
            except RuntimeError:  # that stream's loop has closed
                self.unsubscribe(sub)


def status_snapshot(contacts: Iterable, endpoints: dict[str, str]) -> tuple:
    """Comparable summary of registrations, to notice when they change."""
    return (
        tuple(sorted((c.endpoint, c.uri, c.status) for c in contacts)),
        tuple(sorted(endpoints.items())),
    )
