import socket

import pytest

from phonesystem import cli
from phonesystem.config import AppConfig


def busy_port() -> socket.socket:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen()
    return s


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.parametrize("which", ["admin", "provisioning"])
def test_busy_port_gives_a_plain_explanation(tmp_path, which):
    blocker = busy_port()
    port = blocker.getsockname()[1]
    ports = {"admin_port": free_port(), "provisioning_port": free_port()}
    ports[f"{which}_port"] = port
    cfg = AppConfig(
        data_dir=tmp_path,
        admin_host="127.0.0.1",
        provisioning_host="127.0.0.1",
        syslog_port=0,
        **ports,
    )
    with blocker, pytest.raises(SystemExit) as info:
        cli.cmd_serve(cfg, None)
    message = str(info.value)
    assert f"port {port}/tcp" in message
    assert "another program is already using it" in message
    assert "phonesystem.env" in message
