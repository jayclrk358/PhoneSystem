"""Command line: ``phonesystem serve | migrate | create-admin | apply | show-config``."""

import argparse
import asyncio
import errno
import getpass
import logging
import os
import socket
import sys
from pathlib import Path

from sqlalchemy import select

from . import security
from .config import AppConfig, get_config
from .db import Database
from .migrate import upgrade
from .models import AdminUser

log = logging.getLogger("phonesystem")


def _prepare(cfg: AppConfig) -> Database:
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    upgrade(cfg.db_url)
    _restrict_db_file(cfg.db_url)
    return Database(cfg.db_url)


def _restrict_db_file(db_url: str) -> None:
    """The database holds SIP passwords: keep it private to the service user."""
    if not db_url.startswith("sqlite:///"):
        return
    path = Path(db_url.removeprefix("sqlite:///"))
    for p in (path, path.with_name(path.name + "-wal"), path.with_name(path.name + "-shm")):
        if p.exists():
            p.chmod(0o600)


class PortError(SystemExit):
    """Exit with a plain explanation when a port can't be used."""


def port_error(exc: OSError, port: int, proto: str, what: str, env_var: str) -> PortError:
    if exc.errno == errno.EADDRINUSE:
        reason = "another program is already using it"
    elif exc.errno == errno.EACCES:
        reason = (
            "ports below 1024 need the CAP_NET_BIND_SERVICE capability (the systemd unit grants it)"
        )
    else:
        reason = exc.strerror or str(exc)
    return PortError(
        f"PhoneSystem can't listen on port {port}/{proto} ({what}): {reason}. "
        f"Stop the other program, or set {env_var} to a free port in "
        "/etc/phonesystem/phonesystem.env and restart (sudo systemctl restart phonesystem)."
    )


async def _serve(cfg: AppConfig) -> None:
    import uvicorn

    from .app import create_app
    from .services.firmware import FirmwareStore
    from .services.provisioning.content import ProvisioningService
    from .services.provisioning.server import ProvisioningServer
    from .services.syslog import SyslogReceiver

    db = _prepare(cfg)
    app = create_app(cfg, db)

    # uvicorn only reports a busy port after startup; check the web port first
    # so every port problem gets the same clear message.
    _check_tcp_port(cfg.admin_host, cfg.admin_port, "web UI", "PHONESYSTEM_ADMIN_PORT")
    prov = ProvisioningServer(ProvisioningService(db, FirmwareStore(cfg.firmware_dir)))
    try:
        prov_server = await prov.start(cfg.provisioning_host, cfg.provisioning_port)
    except OSError as exc:
        raise port_error(
            exc, cfg.provisioning_port, "tcp", "phone settings", "PHONESYSTEM_PROVISIONING_PORT"
        ) from exc
    syslog_transport = None
    if cfg.syslog_port:
        try:
            syslog_transport = await SyslogReceiver(db).start(
                cfg.provisioning_host, cfg.syslog_port
            )
        except OSError as exc:
            prov_server.close()
            raise port_error(
                exc, cfg.syslog_port, "udp", "phone logs", "PHONESYSTEM_SYSLOG_PORT"
            ) from exc

    web = uvicorn.Server(
        uvicorn.Config(
            app,
            host=cfg.admin_host,
            port=cfg.admin_port,
            ssl_certfile=str(cfg.tls_cert) if cfg.tls_cert else None,
            ssl_keyfile=str(cfg.tls_key) if cfg.tls_key else None,
            log_level="info",
            proxy_headers=False,
            server_header=False,
        )
    )
    scheme = "https" if cfg.tls_cert else "http"
    log.info("web UI on %s://%s:%s", scheme, cfg.admin_host, cfg.admin_port)
    try:
        await web.serve()
    finally:
        prov_server.close()
        if syslog_transport:
            syslog_transport.close()


def _check_tcp_port(host: str, port: int, what: str, env_var: str) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError as exc:
            raise port_error(exc, port, "tcp", what, env_var) from exc


def cmd_serve(cfg: AppConfig, _args) -> int:
    asyncio.run(_serve(cfg))
    return 0


def cmd_migrate(cfg: AppConfig, _args) -> int:
    _prepare(cfg)
    print("database is up to date")
    return 0


def cmd_create_admin(cfg: AppConfig, args) -> int:
    """Create an admin, or reset the password of an existing one."""
    db = _prepare(cfg)
    username = args.username or input("Username: ").strip()
    if args.password_stdin:
        password = sys.stdin.readline().rstrip("\n")
    else:
        password = getpass.getpass("Password: ")
        if getpass.getpass("Repeat password: ") != password:
            print("passwords don't match", file=sys.stderr)
            return 1
    try:
        security.check_password_policy(password)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1
    with db.session() as session:
        user = session.scalar(select(AdminUser).where(AdminUser.username == username))
        if user is None:
            session.add(
                AdminUser(username=username, password_hash=security.hash_password(password))
            )
            print(f"created admin {username}")
        else:
            user.password_hash = security.hash_password(password)
            security.end_all_sessions(session, user)
            print(f"reset password for {username}")
    return 0


def cmd_apply(cfg: AppConfig, _args) -> int:
    """Generate the Asterisk config and reload Asterisk (used by the installer)."""
    from .services.confgen import ConfigManager

    db = _prepare(cfg)
    with db.session() as session:
        version = ConfigManager(cfg).apply(session, "cli")
        print(f"config version {version.id}: {version.status} {version.message}".rstrip())
        return 0 if version.status == "applied" else 1


def cmd_show_config(cfg: AppConfig, _args) -> int:
    from .services.confgen import render

    db = _prepare(cfg)
    with db.session() as session:
        for name, text in render(session).files.items():
            print(f";;;;;;;;;; {name}\n{text}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="phonesystem", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("serve", help="run the web UI, phone provisioning and syslog servers")
    sub.add_parser("migrate", help="create or upgrade the database")
    p = sub.add_parser("create-admin", help="create an admin or reset their password")
    p.add_argument("--username")
    p.add_argument("--password-stdin", action="store_true", help="read the password from stdin")
    sub.add_parser("apply", help="generate the Asterisk config and reload Asterisk")
    sub.add_parser("show-config", help="print the generated Asterisk config")
    args = parser.parse_args(argv)

    # Nothing we create (database, generated configs, firmware) should be
    # world-readable, however we're started.
    os.umask(0o027)

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    cfg = get_config()
    handler = {
        "serve": cmd_serve,
        "migrate": cmd_migrate,
        "create-admin": cmd_create_admin,
        "apply": cmd_apply,
        "show-config": cmd_show_config,
    }[args.command]
    return handler(cfg, args)


if __name__ == "__main__":
    sys.exit(main())
