"""Run a private Asterisk instance for integration tests.

It uses the repo's base configs from ``asterisk/`` and our generated configs
(through the same ``phonesystem`` symlink layout as a real install), with every
directory under a temp dir and free ports for SIP and AMI.
"""

import shutil
import socket
import subprocess
import time
from pathlib import Path

from phonesystem.services.ami import AmiClient, AmiError

REPO = Path(__file__).resolve().parents[3]
BASE_CONFIGS = REPO / "asterisk"
MODULE_DIRS = [
    Path("/usr/lib/x86_64-linux-gnu/asterisk/modules"),
    Path("/usr/lib/aarch64-linux-gnu/asterisk/modules"),
    Path("/usr/lib/asterisk/modules"),
]
AMI_USER = "phonesystem"
AMI_SECRET = "test-secret"


def free_port(kind: int = socket.SOCK_STREAM) -> int:
    with socket.socket(socket.AF_INET, kind) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def free_tcp_udp_port() -> int:
    """A port that is free for both TCP and UDP (SIP binds both)."""
    for _ in range(50):
        port = free_port()
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.bind(("127.0.0.1", port))
            return port
        except OSError:
            continue
    raise RuntimeError("no free port")


class AsteriskInstance:
    def __init__(self, root: Path, current_config_link: Path):
        self.root = root
        self.etc = root / "etc"
        self.ami_port = free_port()
        self.proc: subprocess.Popen | None = None
        self._setup(current_config_link)

    def _setup(self, link: Path) -> None:
        moddir = next((d for d in MODULE_DIRS if d.is_dir()), None)
        if moddir is None:
            raise RuntimeError("Asterisk modules directory not found")
        self.etc.mkdir(parents=True)
        for name in (
            "pjsip.conf",
            "extensions.conf",
            "modules.conf",
            "logger.conf",
            "rtp.conf",
            "cdr.conf",
            "cdr_custom.conf",
        ):
            shutil.copy(BASE_CONFIGS / name, self.etc / name)
        manager = (BASE_CONFIGS / "manager.conf.in").read_text()
        manager = manager.replace("@AMI_SECRET@", AMI_SECRET).replace(
            "port = 5038", f"port = {self.ami_port}"
        )
        (self.etc / "manager.conf").write_text(manager)
        # Same layout as an install: /etc/asterisk/phonesystem -> data_dir/asterisk-current
        (self.etc / "phonesystem").symlink_to(link)
        dirs = {}
        for name in ("run", "log", "spool", "db", "cache"):
            (self.root / name).mkdir()
            dirs[name] = self.root / name
        # Asterisk's CDR backends don't create their folders.
        for sub in ("cdr-csv", "cdr-custom"):
            (dirs["log"] / sub).mkdir()
        (self.etc / "asterisk.conf").write_text(
            "[directories]\n"
            f"astetcdir => {self.etc}\n"
            f"astmoddir => {moddir}\n"
            "astvarlibdir => /var/lib/asterisk\n"
            f"astdbdir => {dirs['db']}\n"
            f"astkeydir => {dirs['db']}\n"
            "astdatadir => /usr/share/asterisk\n"
            f"astspooldir => {dirs['spool']}\n"
            f"astrundir => {dirs['run']}\n"
            f"astlogdir => {dirs['log']}\n"
            f"astcachedir => {dirs['cache']}\n"
            "[options]\n"
            "nofork = yes\n"
            "hideconnect = yes\n"
        )

    def start(self, timeout: float = 30) -> None:
        self.proc = subprocess.Popen(  # noqa: S603
            ["asterisk", "-f", "-C", str(self.etc / "asterisk.conf")],  # noqa: S607
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"asterisk exited early: {self.log_tail()}")
            try:
                with self.ami() as ami:
                    ami.command("core waitfullybooted")
                    return
            except AmiError:
                time.sleep(0.3)
        raise RuntimeError(f"asterisk didn't come up: {self.log_tail()}")

    @property
    def cdr_file(self) -> Path:
        """Where cdr_custom writes PhoneSystem's call records for this instance."""
        return self.root / "log" / "cdr-custom" / "phonesystem-calls.csv"

    def ami(self) -> AmiClient:
        return AmiClient("127.0.0.1", self.ami_port, AMI_USER, AMI_SECRET, timeout=5)

    def stop(self) -> None:
        if self.proc is None:
            return
        try:
            with self.ami() as ami:
                ami.command("core stop now")
        except AmiError:
            pass
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
        self.proc = None

    def log_tail(self, lines: int = 60) -> str:
        log = self.root / "log" / "messages.log"
        if not log.exists():
            return "(no log)"
        return "\n".join(log.read_text(errors="replace").splitlines()[-lines:])
