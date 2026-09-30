"""Process-level configuration, read from environment variables (PHONESYSTEM_*).

These are things that must be known before the database is open (paths, ports,
AMI credentials). Everything an admin edits in the web UI lives in the database
instead (see ``services/system_settings.py``).
"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class AppConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PHONESYSTEM_", env_file=None)

    # Where the database, generated Asterisk configs and firmware live.
    data_dir: Path = Path("/var/lib/phonesystem")
    database_url: str | None = None

    # How many old generated config versions to keep on disk for rollback.
    config_versions_kept: int = 20

    ami_host: str = "127.0.0.1"
    ami_port: int = 5038
    ami_username: str = "phonesystem"
    ami_secret: str = ""
    ami_timeout: float = 5.0

    # Admin web UI / API.
    admin_host: str = "0.0.0.0"
    admin_port: int = 8443
    tls_cert: Path | None = None
    tls_key: Path | None = None
    # Mark the session cookie Secure. Turn off only for plain-HTTP development.
    secure_cookies: bool = True
    session_hours: int = 12
    # Built frontend (frontend/dist). Served at / when present.
    frontend_dist: Path | None = None

    # Phone-facing provisioning HTTP server (plain HTTP; the 96x1 SIP firmware
    # only trusts Avaya certificate authorities, so HTTPS isn't an option).
    provisioning_host: str = "0.0.0.0"
    provisioning_port: int = 80
    # UDP syslog receiver for phone logs. 0 disables it.
    syslog_port: int = 514

    # Asterisk's cdr_custom writes one line per call leg here (asterisk/cdr_custom.conf);
    # we import it into the call log. Asterisk 20 only writes inside its log dir.
    cdr_file: Path = Path("/var/log/asterisk/cdr-custom/phonesystem-calls.csv")
    # How often to check for new call records, in seconds (a cheap stat() call
    # when nothing changed).
    cdr_import_interval: float = 1.0

    @property
    def db_url(self) -> str:
        return self.database_url or f"sqlite:///{self.data_dir / 'phonesystem.db'}"

    @property
    def config_versions_dir(self) -> Path:
        return self.data_dir / "asterisk-configs"

    @property
    def current_config_link(self) -> Path:
        """Symlink to the live config version. The installer points
        /etc/asterisk/phonesystem at this path, so Asterisk's #include lines
        follow it; we swap it atomically on apply."""
        return self.data_dir / "asterisk-current"

    @property
    def firmware_dir(self) -> Path:
        return self.data_dir / "firmware"


@lru_cache
def get_config() -> AppConfig:
    return AppConfig()
