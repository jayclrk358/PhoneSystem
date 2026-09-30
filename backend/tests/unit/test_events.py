import asyncio
import json
import socket
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import uvicorn

from phonesystem import cli
from phonesystem.api import events as events_api
from phonesystem.app import changed_topics, create_app
from phonesystem.config import AppConfig
from phonesystem.db import Database
from phonesystem.security import CSRF_HEADER, CSRF_VALUE
from phonesystem.services import calls
from phonesystem.services.events import EventBus
from phonesystem.services.firmware import FirmwareStore
from phonesystem.services.provisioning.content import ProvisioningService
from phonesystem.services.syslog import SyslogReceiver

from .test_calls import cdr_line

# ------------------------------------------------------------------ the bus


def test_publish_from_another_thread_reaches_subscribers():
    bus = EventBus()

    async def main():
        sub = bus.subscribe()
        threading.Thread(target=bus.publish, args=("calls",)).start()
        first = await sub.next(timeout=2)
        # A burst while nobody is reading arrives as one set.
        bus.publish("phones")
        bus.publish("phone_logs", "phones")
        await asyncio.sleep(0.05)
        burst = sub.drain()
        bus.unsubscribe(sub)
        return first, burst, bus.subscriber_count

    first, burst, remaining = asyncio.run(main())
    assert first == {"calls"}
    assert burst == {"phones", "phone_logs"}
    assert remaining == 0


def test_unknown_topics_are_rejected():
    with pytest.raises(ValueError):
        EventBus().publish("everything")


def test_next_times_out_quietly():
    async def main():
        return await EventBus().subscribe().next(timeout=0.05)

    assert asyncio.run(main()) is None


@pytest.mark.parametrize(
    ("path", "topics"),
    [
        ("/api/extensions", ("extensions", "phones", "config", "audit")),
        ("/api/extensions/3", ("extensions", "phones", "config", "audit")),
        ("/api/phones/1", ("phones", "audit")),
        ("/api/config/apply", ("config", "audit")),
        ("/api/auth/login", ()),
        ("/api/extensionsX", ()),
    ],
)
def test_changed_topics(path, topics):
    assert changed_topics(path) == topics


# ------------------------------------------------------------------ publishers


class Recorder:
    def __init__(self):
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, *topics):
        self.calls.append(topics)


def test_importer_publishes_only_when_calls_arrive(db: Database, tmp_path: Path):
    notify = Recorder()
    cdr = tmp_path / "calls.csv"
    importer = calls.CdrImporter(db, cdr, notify=notify)
    importer.import_new()
    assert notify.calls == []
    cdr.write_text(cdr_line(0))
    importer.import_new()
    importer.import_new()  # nothing new
    assert notify.calls == [("calls",)]


def test_provisioning_and_syslog_publish(db: Database, tmp_path: Path):
    notify = Recorder()
    prov = ProvisioningService(db, FirmwareStore(tmp_path / "fw"), notify=notify)
    fields = {"method": "GET", "path": "/46xxsettings.txt", "status": 200, "size": 1}
    prov.record(ip="10.0.0.9", mac=None, user_agent=None, **fields)
    prov.record(ip="10.0.0.9", mac="001b4f000001", user_agent=None, **fields)
    assert notify.calls == [("provisioning",), ("provisioning", "phones")]

    notify.calls.clear()
    SyslogReceiver(db, notify=notify).flush([(datetime.now(UTC), "10.0.0.9", "hello")])
    assert notify.calls == [("phone_logs",)]


def test_registration_watcher_publishes_changes(cfg, fake_asterisk, config_manager, db):
    app = create_app(cfg, db, config_manager)
    state = app.state.phonesystem

    async def main():
        sub = state.bus.subscribe()  # someone is watching
        watcher = asyncio.create_task(cli._watch_registrations(state, interval=0.05))
        await asyncio.sleep(0.2)
        assert sub.drain() == set()  # nothing changed yet
        fake_asterisk.endpoints = {"101": "Not in use"}  # a phone registered
        got = await sub.next(timeout=2)
        fake_asterisk.reachable = False  # Asterisk went away
        got2 = await sub.next(timeout=2)
        watcher.cancel()
        return got, got2

    assert asyncio.run(main()) == ({"status"}, {"status"})


# ------------------------------------------------------------------ the stream


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def live_server(cfg: AppConfig, db: Database, config_manager, monkeypatch):
    """The real app on a real port (the in-process test client can't stream)."""
    monkeypatch.setattr(events_api, "KEEPALIVE_SECONDS", 0.3)
    app = create_app(cfg, db, config_manager)
    port = free_port()
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning",
                       timeout_graceful_shutdown=1)
    )  # fmt: skip
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            httpx.get(base + "/api/health", timeout=0.2)
            break
        except httpx.HTTPError:
            time.sleep(0.05)
    client = httpx.Client(base_url=base, headers={CSRF_HEADER: CSRF_VALUE}, timeout=5)
    r = client.post("/api/auth/setup", json={"username": "admin", "password": "correct horse"})
    assert r.status_code == 200
    yield client
    client.close()
    server.should_exit = True
    thread.join(timeout=5)


def read_events(lines, until: str, timeout: float = 5):
    """(event, data) pairs from an SSE line iterator, up to and including ``until``."""
    got = []
    deadline = time.monotonic() + timeout
    event = None
    for line in lines:
        if line.startswith("event: "):
            event = line[len("event: ") :]
        elif line.startswith("data: ") and event:
            got.append((event, json.loads(line[len("data: ") :])))
            if event == until:
                return got
            event = None
        if time.monotonic() > deadline:
            break
    raise AssertionError(f"no {until!r} event; got {got}")


def test_stream_needs_sign_in(live_server: httpx.Client):
    anonymous = httpx.get(str(live_server.base_url) + "/api/events", timeout=5)
    assert anonymous.status_code == 401


def test_stream_announces_admin_changes(live_server: httpx.Client):
    with live_server.stream("GET", "/api/events") as stream:
        assert stream.headers["content-type"].startswith("text/event-stream")
        lines = stream.iter_lines()
        assert read_events(lines, "ready") == [("ready", {})]
        r = live_server.post("/api/extensions", json={"number": "101", "name": "Alice"})
        assert r.status_code == 201
        event, topics = read_events(lines, "change")[-1]
        assert {"extensions", "config", "audit"} <= set(topics)


def test_stream_ends_when_signed_out(live_server: httpx.Client):
    with live_server.stream("GET", "/api/events") as stream:
        lines = stream.iter_lines()
        read_events(lines, "ready")
        # Sign out from "another tab" (same session cookie).
        live_server.post("/api/auth/logout")
        assert read_events(lines, "signed-out")[-1] == ("signed-out", {})
