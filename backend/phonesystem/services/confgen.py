"""Turn the database into Asterisk config files, and apply them safely.

Each apply writes a complete, numbered copy of the generated files into
``<data_dir>/asterisk-configs/vNNNNNN/``, then atomically repoints the
``asterisk-current`` symlink at it and tells Asterisk to reload. If the reload
fails, the symlink goes back to the previous version.
"""

import hashlib
import logging
import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import validation
from ..config import AppConfig
from ..models import ConfigVersion, Extension
from . import system_settings
from .ami import AmiClient, AmiError, list_endpoints
from .templating import env

log = logging.getLogger(__name__)

GENERATED_FILES = ("pjsip.generated.conf", "extensions.generated.conf")
CHECKSUM_FILE = ".checksum"
# Asterisk modules to reload after new config is in place.
RELOAD_MODULES = ("res_pjsip.so", "pbx_config.so")


@dataclass(frozen=True)
class ExtensionView:
    number: str
    name: str
    sip_password: str


@dataclass(frozen=True)
class RenderedConfig:
    files: dict[str, str]

    @property
    def checksum(self) -> str:
        h = hashlib.sha256()
        for name in sorted(self.files):
            h.update(name.encode() + b"\0" + self.files[name].encode() + b"\0")
        return h.hexdigest()


def enabled_extensions(session: Session) -> list[ExtensionView]:
    rows = session.scalars(
        select(Extension).where(Extension.enabled.is_(True)).order_by(Extension.number)
    ).all()
    # Re-check stored values: the database is the input to a config file.
    return [
        ExtensionView(
            number=validation.check_extension_number(r.number),
            name=validation.check_display_name(r.name),
            sip_password=validation.check_sip_password(r.sip_password),
        )
        for r in rows
    ]


def render(session: Session) -> RenderedConfig:
    context = {
        "settings": system_settings.load(session),
        "extensions": enabled_extensions(session),
    }
    files = {
        name: env.get_template(f"asterisk/{name}.j2").render(context) for name in GENERATED_FILES
    }
    return RenderedConfig(files)


class ApplyError(Exception):
    pass


class ConfigManager:
    def __init__(self, cfg: AppConfig, ami_factory: Callable[[], AmiClient] | None = None):
        self.cfg = cfg
        self.ami_factory = ami_factory or (
            lambda: AmiClient(
                cfg.ami_host, cfg.ami_port, cfg.ami_username, cfg.ami_secret, cfg.ami_timeout
            )
        )

    # -- state --------------------------------------------------------------

    @property
    def link(self) -> Path:
        return self.cfg.current_config_link

    def live_version_dir(self) -> Path | None:
        if not self.link.is_symlink():
            return None
        return self.link.resolve()

    def live_checksum(self) -> str | None:
        live = self.live_version_dir()
        if live is None:
            return None
        try:
            return (live / CHECKSUM_FILE).read_text().strip()
        except OSError:
            return None

    def has_pending_changes(self, session: Session) -> bool:
        return render(session).checksum != self.live_checksum()

    # -- apply / rollback ---------------------------------------------------

    def apply(self, session: Session, username: str) -> ConfigVersion:
        rendered = render(session)
        return self._activate(session, username, rendered, "")

    def rollback(self, session: Session, version_id: int, username: str) -> ConfigVersion:
        source = self._version_dir(version_id)
        if not source.is_dir():
            raise ApplyError(f"config version {version_id} is no longer on disk")
        files = {name: (source / name).read_text() for name in GENERATED_FILES}
        return self._activate(
            session, username, RenderedConfig(files), f"rolled back to version {version_id}"
        )

    def _activate(
        self, session: Session, username: str, rendered: RenderedConfig, note: str
    ) -> ConfigVersion:
        version = ConfigVersion(
            created_by=username, checksum=rendered.checksum, status="pending", message=""
        )
        session.add(version)
        session.flush()  # assigns version.id

        new_dir = self._write_version(version.id, rendered)
        previous = self.live_version_dir()
        self._point_link(new_dir)

        ok, message = self._reload(_endpoint_names(rendered))
        if ok:
            version.status = "applied"
            version.message = message or note
            for old in session.scalars(
                select(ConfigVersion).where(
                    ConfigVersion.status == "applied", ConfigVersion.id != version.id
                )
            ):
                old.status = "superseded"
        else:
            version.status = "failed"
            if previous is not None:
                self._point_link(previous)
                restored_ok, restore_msg = self._reload(None)
                message += " Previous config restored." if restored_ok else f" {restore_msg}"
            else:
                # Nothing to go back to: take the broken config out entirely.
                self.link.unlink()
                self._reload(None)
            version.message = message
            log.error("config apply failed: %s", message)
        session.flush()
        self._prune(keep_dirs={new_dir, previous} if previous else {new_dir})
        return version

    # -- filesystem ---------------------------------------------------------

    def _version_dir(self, version_id: int) -> Path:
        return self.cfg.config_versions_dir / f"v{version_id:06d}"

    def _write_version(self, version_id: int, rendered: RenderedConfig) -> Path:
        target = self._version_dir(version_id)
        tmp = target.with_name(target.name + ".tmp")
        if tmp.exists():
            shutil.rmtree(tmp)
        tmp.mkdir(parents=True, mode=0o750)
        for name, content in rendered.files.items():
            path = tmp / name
            path.write_text(content)
            path.chmod(0o640)
        (tmp / CHECKSUM_FILE).write_text(rendered.checksum + "\n")
        os.replace(tmp, target)
        return target

    def _point_link(self, target: Path) -> None:
        self.link.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.link.with_name(self.link.name + ".new")
        if tmp.is_symlink() or tmp.exists():
            tmp.unlink()
        tmp.symlink_to(target)
        os.replace(tmp, self.link)  # atomic on POSIX

    def _prune(self, keep_dirs: set[Path]) -> None:
        base = self.cfg.config_versions_dir
        dirs = sorted(p for p in base.glob("v[0-9]*") if p.is_dir() and not p.name.endswith(".tmp"))
        excess = dirs[: max(0, len(dirs) - self.cfg.config_versions_kept)]
        live = self.live_version_dir()
        for d in excess:
            if d in keep_dirs or d == live:
                continue
            shutil.rmtree(d, ignore_errors=True)

    # -- Asterisk -----------------------------------------------------------

    def _reload(self, expected_endpoints: set[str] | None) -> tuple[bool, str]:
        """Reload Asterisk. Returns (ok, message).

        If Asterisk isn't reachable the new config stays in place (it's read
        when Asterisk starts), and this counts as success with a warning.
        """
        try:
            with self.ami_factory() as ami:
                # Reloads requested while Asterisk is still booting are only
                # queued, so the check below would see the old config.
                ami.command("core waitfullybooted")
                for module in RELOAD_MODULES:
                    resp = ami.action("Reload", Module=module)
                    if not resp.ok:
                        return False, f"Asterisk rejected reload of {module}: {resp.message}"
                if expected_endpoints is None:
                    return True, ""
                loaded = set(list_endpoints(ami))
        except AmiError as exc:
            return True, (
                f"Config saved, but Asterisk wasn't reachable ({exc}). "
                "It will be used when Asterisk starts."
            )
        missing = sorted(expected_endpoints - loaded)
        if missing:
            return False, (
                "Asterisk didn't load these extensions after reload: "
                + ", ".join(missing[:10])
                + ". Check the Asterisk log for config errors."
            )
        return True, ""


def _endpoint_names(rendered: RenderedConfig) -> set[str]:
    """Names of the endpoint objects in the generated pjsip config."""
    lines = rendered.files["pjsip.generated.conf"].splitlines()
    return {
        line[1:-1]
        for line, following in zip(lines, lines[1:], strict=False)
        if line.startswith("[") and line.endswith("]") and following == "type=endpoint"
    }
