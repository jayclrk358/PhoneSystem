"""Storage for Avaya firmware files served to the phones.

Admins upload Avaya's 96x1 SIP firmware zip (or individual files). The files
are kept flat in ``<data_dir>/firmware/files`` and served by name. The
bundle's own ``96x1Supgrade.txt`` is served as the upgrade script; its sample
``46xxsettings.txt`` is never served (ours is generated).
"""

import re
import shutil
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
# Names we generate ourselves; uploaded copies are stored but never served.
GENERATED_NAMES = {"46xxsettings.txt"}
UPGRADE_SCRIPT = "96x1Supgrade.txt"

MAX_ZIP_BYTES = 300 * 1024 * 1024
MAX_EXTRACTED_BYTES = 1024 * 1024 * 1024
MAX_FILE_BYTES = 200 * 1024 * 1024


class FirmwareError(ValueError):
    pass


@dataclass
class FirmwareFile:
    name: str
    size: int
    modified: datetime
    served: bool


class FirmwareStore:
    def __init__(self, root: Path):
        self.files_dir = root / "files"

    def ensure(self) -> None:
        self.files_dir.mkdir(parents=True, exist_ok=True)

    def files(self) -> list[FirmwareFile]:
        if not self.files_dir.is_dir():
            return []
        out = []
        for p in sorted(self.files_dir.iterdir(), key=lambda p: p.name.lower()):
            if not p.is_file():
                continue
            st = p.stat()
            out.append(
                FirmwareFile(
                    name=p.name,
                    size=st.st_size,
                    modified=datetime.fromtimestamp(st.st_mtime, UTC),
                    served=p.name.lower() not in GENERATED_NAMES,
                )
            )
        return out

    def find(self, name: str) -> Path | None:
        """Find a servable file by name (case-insensitive, like the phones' server)."""
        if not SAFE_NAME.fullmatch(name) or name.lower() in GENERATED_NAMES:
            return None
        exact = self.files_dir / name
        if exact.is_file():
            return exact
        if not self.files_dir.is_dir():
            return None
        lower = name.lower()
        for p in self.files_dir.iterdir():
            if p.name.lower() == lower and p.is_file():
                return p
        return None

    def upgrade_script(self) -> Path | None:
        return self.find(UPGRADE_SCRIPT)

    def save_file(self, name: str, data: BinaryIO) -> str:
        name = Path(name).name
        if not SAFE_NAME.fullmatch(name):
            raise FirmwareError(f"file name not allowed: {name!r}")
        self.ensure()
        tmp = self.files_dir / f".upload-{name}"
        written = 0
        with tmp.open("wb") as out:
            while chunk := data.read(1024 * 1024):
                written += len(chunk)
                if written > MAX_FILE_BYTES:
                    out.close()
                    tmp.unlink(missing_ok=True)
                    raise FirmwareError(f"{name} is too large")
                out.write(chunk)
        tmp.replace(self.files_dir / name)
        return name

    def import_zip(self, path: Path) -> tuple[list[str], list[str]]:
        """Extract every file from a firmware zip (folders are flattened).

        Returns (saved, skipped); files with names the phones couldn't request
        (spaces, odd characters) are skipped.
        """
        if path.stat().st_size > MAX_ZIP_BYTES:
            raise FirmwareError("zip file is too large")
        try:
            zf = zipfile.ZipFile(path)
        except zipfile.BadZipFile as exc:
            raise FirmwareError("not a valid zip file") from exc
        with zf:
            members = [m for m in zf.infolist() if not m.is_dir()]
            if sum(m.file_size for m in members) > MAX_EXTRACTED_BYTES:
                raise FirmwareError("zip contents are too large")
            saved: list[str] = []
            skipped: list[str] = []
            for m in members:
                name = Path(m.filename.replace("\\", "/")).name
                if name.startswith(".") or "__MACOSX" in m.filename:
                    continue
                if not SAFE_NAME.fullmatch(name) or m.file_size > MAX_FILE_BYTES:
                    skipped.append(name)
                    continue
                with zf.open(m) as src:
                    saved.append(self.save_file(name, src))
        if not saved:
            raise FirmwareError("the zip didn't contain any usable files")
        return saved, skipped

    def delete(self, name: str) -> bool:
        if not SAFE_NAME.fullmatch(name):
            return False
        p = self.files_dir / name
        if p.is_file():
            p.unlink()
            return True
        return False

    def delete_all(self) -> None:
        if self.files_dir.is_dir():
            shutil.rmtree(self.files_dir)
        self.ensure()
