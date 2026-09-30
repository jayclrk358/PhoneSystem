import asyncio
import io
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest
from sqlalchemy import select

from phonesystem.db import Database
from phonesystem.models import Extension, Phone, ProvisioningRequest
from phonesystem.services import system_settings
from phonesystem.services.firmware import FirmwareError, FirmwareStore
from phonesystem.services.provisioning import identify
from phonesystem.services.provisioning.content import (
    ProvisioningService,
    generated_dialplan,
)
from phonesystem.services.provisioning.server import ProvisioningServer, requested_name

ARP_HEADER = "IP address       HW type     Flags       HW address            Mask     Device\n"


@pytest.mark.parametrize(
    ("numbers", "expected"),
    [
        ([], ""),
        (["101", "102", "250"], "[12]XX"),
        (["101"], "1XX"),
        (["10", "101"], "1XX"),  # "10" can't auto-dial: 101 starts the same way
        (["20", "101"], "2X|1XX"),
        (["1001", "2001", "301"], "3XX|[12]XXX"),
    ],
)
def test_generated_dialplan(numbers, expected):
    assert generated_dialplan(numbers) == expected


@pytest.mark.parametrize(
    ("ua", "mac"),
    [
        ("AVAYA/96x1-IPT-SIP-R7_1_15_0_4 (MAC:00:1b:4f:aa:bb:cc)", "001b4faabbcc"),
        ("Avaya/J179-4.0.10 (MAC=001B4FAABBCC)", "001b4faabbcc"),
        ("curl/8.5.0", None),
        (None, None),
    ],
)
def test_mac_from_user_agent(ua, mac):
    assert identify.mac_from_user_agent(ua) == mac


def test_mac_from_arp(tmp_path: Path):
    arp = tmp_path / "arp"
    arp.write_text(
        ARP_HEADER
        + "10.0.0.20        0x1         0x2         00:1b:4f:aa:bb:cc     *        eth0\n"
        + "10.0.0.21        0x1         0x0         00:00:00:00:00:00     *        eth0\n"
    )
    assert identify.mac_from_arp("10.0.0.20", arp) == "001b4faabbcc"
    assert identify.mac_from_arp("10.0.0.21", arp) is None  # incomplete
    assert identify.mac_from_arp("192.168.9.9", arp) is None  # not on our LAN
    assert identify.mac_from_arp("10.0.0.20", tmp_path / "missing") is None


def test_model_and_firmware_from_user_agent():
    ua = "AVAYA/96x1-IPT-SIP-R7_1_15_0_4 9608G"
    assert identify.model_from_user_agent(ua) == "9608G"
    assert identify.firmware_from_user_agent(ua) == "7.1.15.0.4"


@pytest.mark.parametrize(
    ("target", "name"),
    [
        ("/46xxsettings.txt", "46xxsettings.txt"),
        ("/avaya/96x1Supgrade.txt", "96x1Supgrade.txt"),  # HTTPDIR prefix
        ("/S96x1_SALIB7_1_15.bin?x=1", "S96x1_SALIB7_1_15.bin"),
        ("/%2e%2e/%2e%2e/etc/passwd", "passwd"),  # only a name, never a path
        ("/../..", None),
        ("/", None),
        ("/.hidden", None),
        ("/a\\b", None),
    ],
)
def test_requested_name(target, name):
    assert requested_name(target) == name


# --------------------------------------------------------------- the server


@pytest.fixture
def prov(db: Database, tmp_path: Path):
    arp = tmp_path / "arp"
    arp.write_text(
        ARP_HEADER + "127.0.0.1        0x1         0x2         00:1b:4f:aa:bb:cc     *        lo\n"
    )
    with db.session() as s:
        st = system_settings.load(s)
        st.server_ip = "10.0.0.5"
        system_settings.save(s, st)
    firmware = FirmwareStore(tmp_path / "fw")
    firmware.ensure()
    return ProvisioningService(db, firmware, arp_table=arp)


async def raw_request(port: int, request: bytes) -> bytes:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(request)
    await writer.drain()
    data = await reader.read()  # server closes the connection when done
    writer.close()
    return data


def run_with_server(service: ProvisioningService, coro_factory):
    async def main():
        server = await ProvisioningServer(service).start("127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            return await coro_factory(port)
        finally:
            server.close()
            await server.wait_closed()

    return asyncio.run(main())


def split_response(data: bytes) -> tuple[list[str], bytes]:
    head, _, body = data.partition(b"\r\n\r\n")
    return head.decode().split("\r\n"), body


def test_response_is_classic_http(prov: ProvisioningService):
    data = run_with_server(
        prov,
        lambda port: raw_request(port, b"GET /96x1Supgrade.txt HTTP/1.1\r\nHost: x\r\n\r\n"),
    )
    lines, body = split_response(data)
    assert lines[0] == "HTTP/1.1 200 OK"
    headers = dict(line.split(": ", 1) for line in lines[1:])
    assert headers["Content-Type"] == "text/plain"
    assert int(headers["Content-Length"]) == len(body)
    assert headers["Connection"] == "close"
    assert body.rstrip().endswith(b"GET 46xxsettings.txt")


def test_head_request_has_no_body(prov: ProvisioningService):
    data = run_with_server(
        prov, lambda port: raw_request(port, b"HEAD /46xxsettings.txt HTTP/1.1\r\n\r\n")
    )
    lines, body = split_response(data)
    assert lines[0] == "HTTP/1.1 200 OK"
    assert body == b""


@pytest.mark.parametrize(
    ("request_bytes", "status_line"),
    [
        (b"POST /46xxsettings.txt HTTP/1.1\r\n\r\n", "HTTP/1.1 405 Method Not Allowed"),
        (b"GET /nothing.bin HTTP/1.1\r\n\r\n", "HTTP/1.1 404 Not Found"),
        (b"GET /../../etc/passwd HTTP/1.1\r\n\r\n", "HTTP/1.1 404 Not Found"),
        (b"garbage\r\n\r\n", "HTTP/1.1 400 Bad Request"),
    ],
)
def test_error_responses(prov: ProvisioningService, request_bytes, status_line):
    data = run_with_server(prov, lambda port: raw_request(port, request_bytes))
    assert split_response(data)[0][0] == status_line


def test_settings_file_logs_in_only_the_assigned_phone(prov: ProvisioningService, db: Database):
    with db.session() as s:
        ext = Extension(number="101", name="Alice", sip_password="abcdefgh12345")
        s.add(ext)
        s.flush()
        s.add(Phone(mac="001b4faabbcc", label="", model="", extension_id=ext.id))

    # 127.0.0.1 resolves (via our fake ARP table) to the assigned phone.
    data = run_with_server(
        prov, lambda port: raw_request(port, b"GET /46xxsettings.txt HTTP/1.1\r\n\r\n")
    )
    body = split_response(data)[1].decode()
    assert "SET SIP_CONTROLLER_LIST 10.0.0.5:5060;transport=tcp" in body
    assert "SET FORCE_SIP_EXTENSION 101" in body
    assert "SET FORCE_SIP_PASSWORD abcdefgh12345" in body


def test_claimed_mac_never_gets_credentials(prov: ProvisioningService, db: Database, tmp_path):
    """A MAC in the User-Agent is the client's word only; credentials need ARP proof."""
    with db.session() as s:
        ext = Extension(number="101", name="Alice", sip_password="abcdefgh12345")
        s.add(ext)
        s.flush()
        s.add(Phone(mac="001b4f999999", label="", model="", extension_id=ext.id))
    # 127.0.0.1 is 00:1b:4f:aa:bb:cc in our ARP table, but claims to be the assigned phone.
    spoof = b"GET /46xxsettings.txt HTTP/1.1\r\nUser-Agent: AVAYA (MAC:00:1b:4f:99:99:99)\r\n\r\n"
    body = split_response(run_with_server(prov, lambda port: raw_request(port, spoof)))[1]
    assert b"abcdefgh12345" not in body
    assert b"SET FORCE_SIP_" not in body

    # Same when the ARP table has nothing for the address (e.g. a routed client).
    prov.arp_table = tmp_path / "empty-arp"
    prov.arp_table.write_text(ARP_HEADER)
    body = split_response(run_with_server(prov, lambda port: raw_request(port, spoof)))[1]
    assert b"abcdefgh12345" not in body
    # ...but the phone is still listed as seen.
    with db.session() as s:
        assert s.scalar(select(Phone).where(Phone.mac == "001b4f999999")).last_ip == "127.0.0.1"


def test_requests_are_logged_and_phones_discovered(prov: ProvisioningService, db: Database):
    ua = b"User-Agent: AVAYA/96x1-IPT-SIP-R7_1_15_0_4 9608\r\n"
    run_with_server(
        prov, lambda port: raw_request(port, b"GET /96x1Supgrade.txt HTTP/1.1\r\n" + ua + b"\r\n")
    )
    with db.session() as s:
        req = s.scalar(select(ProvisioningRequest))
        assert (req.mac, req.path, req.status) == ("001b4faabbcc", "/96x1Supgrade.txt", 200)
        phone = s.scalar(select(Phone))
        assert phone.mac == "001b4faabbcc"
        assert phone.discovered
        assert phone.last_ip == "127.0.0.1"
        assert phone.model == "9608"
        assert phone.firmware_version == "7.1.15.0.4"


def test_firmware_files_are_served(prov: ProvisioningService):
    payload = bytes(range(256)) * 4000
    (prov.firmware.files_dir / "S96x1_SALIB7_1_15.bin").write_bytes(payload)
    data = run_with_server(
        prov, lambda port: raw_request(port, b"GET /s96x1_salib7_1_15.bin HTTP/1.1\r\n\r\n")
    )
    lines, body = split_response(data)
    assert lines[0] == "HTTP/1.1 200 OK"
    assert "Content-Type: application/octet-stream" in lines
    assert any(line.startswith("Last-Modified: ") for line in lines)
    assert body == payload


def test_uploaded_sample_settings_file_is_never_served(prov: ProvisioningService):
    (prov.firmware.files_dir / "46xxsettings.txt").write_text("SET SIP_CONTROLLER_LIST 6.6.6.6\n")
    data = run_with_server(
        prov, lambda port: raw_request(port, b"GET /46xxsettings.txt HTTP/1.1\r\n\r\n")
    )
    assert b"6.6.6.6" not in data


def test_bundle_upgrade_script_replaces_ours(prov: ProvisioningService):
    (prov.firmware.files_dir / "96x1Supgrade.txt").write_text("SET APPNAME S96x1.bin\n")
    for name in (b"96x1Supgrade.txt", b"96x1Hupgrade.txt"):
        data = run_with_server(
            prov, lambda port, n=name: raw_request(port, b"GET /" + n + b" HTTP/1.1\r\n\r\n")
        )
        assert split_response(data)[1] == b"SET APPNAME S96x1.bin\n"


@pytest.mark.skipif(shutil.which("curl") is None, reason="curl not installed")
def test_curl_accepts_responses(prov: ProvisioningService):
    """The phones use libcurl, so curl must be happy with every response."""

    async def fetch(port):
        proc = await asyncio.create_subprocess_exec(
            "curl", "-sS", "--fail", "-o", "/dev/null", "-w", "%{http_code} %{size_download}",
            f"http://127.0.0.1:{port}/46xxsettings.txt",
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )  # fmt: skip
        out, err = await proc.communicate()
        return proc.returncode, out.decode(), err.decode()

    code, out, err = run_with_server(prov, fetch)
    assert code == 0, err
    assert out.startswith("200 ")


# --------------------------------------------------------------- firmware store


def make_zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


def test_import_zip_flattens_and_skips_bad_names(tmp_path: Path):
    store = FirmwareStore(tmp_path / "fw")
    zpath = tmp_path / "fw.zip"
    zpath.write_bytes(
        make_zip(
            {
                "96x1-IPT-SIP-R7_1_15/96x1Supgrade.txt": b"GET 46xxsettings.txt\n",
                "96x1-IPT-SIP-R7_1_15/S96x1_SALIB7_1_15.bin": b"\x01\x02",
                "96x1-IPT-SIP-R7_1_15/Release Notes.pdf": b"%PDF",
                "__MACOSX/._junk": b"",
                "../../escape.txt": b"nope",
            }
        )
    )
    saved, skipped = store.import_zip(zpath)
    assert sorted(saved) == ["96x1Supgrade.txt", "S96x1_SALIB7_1_15.bin", "escape.txt"]
    assert skipped == ["Release Notes.pdf"]
    assert (store.files_dir / "escape.txt").exists()  # flattened into files/, not outside
    assert not (tmp_path / "escape.txt").exists()
    assert store.upgrade_script() is not None


def test_import_rejects_non_zip(tmp_path: Path):
    store = FirmwareStore(tmp_path / "fw")
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"not a zip")
    with pytest.raises(FirmwareError):
        store.import_zip(bad)


def test_firmware_api(admin, tmp_path: Path):
    r = admin.get("/api/firmware").json()
    assert r["files"] == []
    assert r["upgrade_script_source"] == "generated"

    blob = make_zip({"96x1Supgrade.txt": b"GET 46xxsettings.txt\n", "S96x1.bin": b"\x00"})
    r = admin.post("/api/firmware", files={"file": ("fw.zip", blob, "application/zip")})
    assert r.status_code == 200, r.text
    assert sorted(r.json()["saved"]) == ["96x1Supgrade.txt", "S96x1.bin"]
    r = admin.get("/api/firmware").json()
    assert r["upgrade_script_source"] == "uploaded"
    assert {f["name"] for f in r["files"]} == {"96x1Supgrade.txt", "S96x1.bin"}

    assert admin.delete("/api/firmware/S96x1.bin").status_code == 204
    assert admin.delete("/api/firmware/S96x1.bin").status_code == 404
    assert admin.delete("/api/firmware").status_code == 204
    assert admin.get("/api/firmware").json()["files"] == []
