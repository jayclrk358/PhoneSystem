import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from phonesystem.config import AppConfig
from phonesystem.db import Database
from phonesystem.models import Extension
from phonesystem.services import confgen, system_settings
from phonesystem.services.confgen import ConfigManager
from phonesystem.services.templating import UnsafeValueError, env

GOLDEN = Path(__file__).resolve().parent.parent / "golden"


def seed(db: Database) -> None:
    with db.session() as s:
        st = system_settings.load(s)
        st.phone_admin_password = "123456"
        st.server_ip = "10.0.0.5"
        system_settings.save(s, st)
        s.add(Extension(number="101", name="Alice Jones", sip_password="abcdefgh12345"))
        s.add(Extension(number="102", name="Bob", sip_password="zyxwvuts98765"))
        s.add(Extension(number="199", name="Disabled", sip_password="qqqqqqqq11111", enabled=False))


@pytest.mark.parametrize("name", confgen.GENERATED_FILES)
def test_render_matches_golden(db: Database, name: str):
    """Compare with tests/golden/. Regenerate with UPDATE_GOLDEN=1 after an intended change."""
    seed(db)
    with db.session() as s:
        text = confgen.render(s).files[name]
    golden = GOLDEN / name
    if os.environ.get("UPDATE_GOLDEN"):
        golden.write_text(text)
    assert text == golden.read_text()


def test_disabled_extensions_are_left_out(db: Database):
    seed(db)
    with db.session() as s:
        files = confgen.render(s).files
    assert "[199]" not in files["pjsip.generated.conf"]
    assert "199" not in files["extensions.generated.conf"]


def test_template_refuses_multiline_values():
    tmpl = env.from_string("name={{ v }}\n")
    with pytest.raises(UnsafeValueError):
        tmpl.render(v="ok\n[evil]\ntype=endpoint")


def test_bad_value_in_database_is_caught(db: Database):
    # Bypass API validation, as if the database had been edited by hand.
    with db.session() as s:
        s.add(Extension(number="101", name="Bad;name", sip_password="abcdefgh12345"))
    with db.session() as s, pytest.raises(ValueError):
        confgen.render(s)


def test_apply_writes_version_and_reloads(
    db: Database, cfg: AppConfig, config_manager: ConfigManager, fake_asterisk
):
    seed(db)
    with db.session() as s:
        assert config_manager.has_pending_changes(s)
        v = config_manager.apply(s, "admin")
        assert v.status == "applied", v.message
        assert not config_manager.has_pending_changes(s)
    live = cfg.current_config_link.resolve()
    assert live.name == f"v{v.id:06d}"
    assert (live / "pjsip.generated.conf").read_text().count("type=endpoint") == 2
    assert fake_asterisk.reloads == list(confgen.RELOAD_MODULES)
    assert set(fake_asterisk.endpoints) == {"101", "102"}


def test_apply_when_asterisk_down_keeps_new_config(
    db: Database, cfg: AppConfig, config_manager: ConfigManager, fake_asterisk
):
    seed(db)
    fake_asterisk.reachable = False
    with db.session() as s:
        v = config_manager.apply(s, "admin")
    assert v.status == "applied"
    assert "wasn't reachable" in v.message
    assert cfg.current_config_link.resolve().name == f"v{v.id:06d}"


def test_failed_reload_restores_previous_config(
    db: Database, cfg: AppConfig, config_manager: ConfigManager, fake_asterisk
):
    seed(db)
    with db.session() as s:
        first = config_manager.apply(s, "admin")
    with db.session() as s:
        s.add(Extension(number="103", name="Carol", sip_password="cccccccc33333"))
    fake_asterisk.drop = {"103"}  # Asterisk "fails" to load the new extension
    with db.session() as s:
        second = config_manager.apply(s, "admin")
        assert second.status == "failed"
        assert "103" in second.message
        assert config_manager.has_pending_changes(s)
    assert cfg.current_config_link.resolve().name == f"v{first.id:06d}"


def test_rejected_reload_restores_previous_config(
    db: Database, cfg: AppConfig, config_manager: ConfigManager, fake_asterisk
):
    seed(db)
    with db.session() as s:
        first = config_manager.apply(s, "admin")
    with db.session() as s:
        s.add(Extension(number="103", name="Carol", sip_password="cccccccc33333"))
    fake_asterisk.reject_reload = True
    with db.session() as s:
        assert config_manager.apply(s, "admin").status == "failed"
    assert cfg.current_config_link.resolve().name == f"v{first.id:06d}"


def test_rollback_restores_old_files(
    db: Database, cfg: AppConfig, config_manager: ConfigManager, fake_asterisk
):
    seed(db)
    with db.session() as s:
        first = config_manager.apply(s, "admin")
    with db.session() as s:
        s.add(Extension(number="103", name="Carol", sip_password="cccccccc33333"))
    with db.session() as s:
        config_manager.apply(s, "admin")
    assert "103" in fake_asterisk.endpoints
    with db.session() as s:
        rolled = config_manager.rollback(s, first.id, "admin")
        assert rolled.status == "applied"
        assert "rolled back" in rolled.message
        # The database still has 103, so the UI shows unapplied changes.
        assert config_manager.has_pending_changes(s)
    assert "103" not in fake_asterisk.endpoints


def test_old_versions_are_pruned(
    db: Database, cfg: AppConfig, config_manager: ConfigManager, fake_asterisk
):
    seed(db)
    for i in range(8):
        with db.session() as s:
            s.add(Extension(number=f"2{i:02d}", name=f"User {i}", sip_password="abcdefgh12345"))
        with db.session() as s:
            config_manager.apply(s, "admin")
    dirs = sorted(p.name for p in cfg.config_versions_dir.iterdir())
    assert len(dirs) == cfg.config_versions_kept
    assert cfg.current_config_link.resolve().name == dirs[-1]


def test_config_api(admin: TestClient, fake_asterisk):
    admin.post("/api/extensions", json={"number": "101", "name": "Alice"})
    assert admin.get("/api/config/status").json()["pending"] is True

    preview = admin.get("/api/config/preview").json()
    assert "password=********" in preview["pjsip.generated.conf"]

    v = admin.post("/api/config/apply").json()
    assert v["status"] == "applied"
    status = admin.get("/api/config/status").json()
    assert status["pending"] is False
    assert status["latest"]["id"] == v["id"]

    admin.post("/api/extensions", json={"number": "102", "name": "Bob"})
    admin.post("/api/config/apply")
    r = admin.post(f"/api/config/versions/{v['id']}/rollback")
    assert r.json()["status"] == "applied"
    assert set(fake_asterisk.endpoints) == {"101"}
    assert len(admin.get("/api/config/versions").json()) == 3
    assert admin.post("/api/config/versions/9999/rollback").status_code == 404


def test_status_api(admin: TestClient, fake_asterisk):
    body = admin.get("/api/status").json()
    assert body["asterisk"]["reachable"] is True
    assert body["registrations"] == []


def test_failed_first_apply_leaves_nothing_live(
    db: Database, cfg: AppConfig, config_manager: ConfigManager, fake_asterisk
):
    seed(db)
    fake_asterisk.reject_reload = True
    with db.session() as s:
        assert config_manager.apply(s, "admin").status == "failed"
    assert not cfg.current_config_link.is_symlink()
    with db.session() as s:
        assert config_manager.has_pending_changes(s)
