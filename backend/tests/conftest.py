from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from phonesystem.app import create_app
from phonesystem.config import AppConfig
from phonesystem.db import Database
from phonesystem.migrate import upgrade
from phonesystem.security import CSRF_HEADER, CSRF_VALUE
from phonesystem.services.ami import AmiError, AmiMessage, AmiResponse
from phonesystem.services.confgen import ConfigManager


class FakeAsterisk:
    """Stands in for Asterisk behind AMI.

    On a PJSIP reload it "loads" the endpoints from whatever the live config
    symlink points at, like the real thing. Set ``reachable = False`` to
    simulate Asterisk being down, or ``drop`` to make it silently skip
    endpoints (a broken config).
    """

    def __init__(self, link: Path):
        self.link = link
        self.reachable = True
        self.drop: set[str] = set()
        self.reject_reload = False
        self.endpoints: dict[str, str] = {}
        self.reloads: list[str] = []

    def __call__(self):
        return _FakeAmiConnection(self)

    def load(self) -> None:
        conf = self.link / "pjsip.generated.conf"
        lines = conf.read_text().splitlines() if conf.exists() else []
        names = {a[1:-1] for a, b in zip(lines, lines[1:], strict=False) if b == "type=endpoint"}
        self.endpoints = {n: "Unavailable" for n in sorted(names - self.drop)}


class _FakeAmiConnection:
    def __init__(self, fake: FakeAsterisk):
        self.fake = fake

    def __enter__(self):
        if not self.fake.reachable:
            raise AmiError("can't connect to Asterisk AMI at 127.0.0.1:5038: refused")
        return self

    def __exit__(self, *exc):
        return None

    def command(self, cli: str) -> str:
        return "Asterisk 20.6.0 (fake)" if "version" in cli else ""

    def action(self, name: str, **fields) -> AmiResponse:
        if name == "Reload":
            if self.fake.reject_reload:
                return AmiResponse(AmiMessage({"response": "Error", "message": "No such module"}))
            self.fake.reloads.append(fields.get("Module", ""))
            if fields.get("Module") == "res_pjsip.so":
                self.fake.load()
            return AmiResponse(AmiMessage({"response": "Success", "message": "Module Reloaded"}))
        if name == "PJSIPShowEndpoints":
            events = [
                AmiMessage({"event": "EndpointList", "objectname": n, "devicestate": s})
                for n, s in self.fake.endpoints.items()
            ]
            return AmiResponse(AmiMessage({"response": "Success"}), events)
        if name == "PJSIPShowContacts":
            return AmiResponse(AmiMessage({"response": "Error", "message": "No Contacts found"}))
        raise AssertionError(f"unexpected AMI action {name}")


@pytest.fixture
def cfg(tmp_path: Path) -> AppConfig:
    return AppConfig(data_dir=tmp_path / "data", secure_cookies=False, config_versions_kept=5)


@pytest.fixture
def db(cfg: AppConfig) -> Database:
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    upgrade(cfg.db_url)
    return Database(cfg.db_url)


@pytest.fixture
def fake_asterisk(cfg: AppConfig) -> FakeAsterisk:
    return FakeAsterisk(cfg.current_config_link)


@pytest.fixture
def config_manager(cfg: AppConfig, fake_asterisk: FakeAsterisk) -> ConfigManager:
    return ConfigManager(cfg, ami_factory=fake_asterisk)


@pytest.fixture
def client(cfg: AppConfig, db: Database, config_manager: ConfigManager) -> TestClient:
    app = create_app(cfg, db, config_manager)
    return TestClient(app, headers={CSRF_HEADER: CSRF_VALUE})


@pytest.fixture
def admin(client: TestClient) -> TestClient:
    """A client signed in as the first admin."""
    r = client.post("/api/auth/setup", json={"username": "admin", "password": "correct horse"})
    assert r.status_code == 200, r.text
    return client
