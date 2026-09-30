import re

import pytest
from fastapi.testclient import TestClient


def make_ext(client: TestClient, number="101", name="Alice", **kw) -> dict:
    r = client.post("/api/extensions", json={"number": number, "name": name, **kw})
    assert r.status_code == 201, r.text
    return r.json()


def test_create_extension_generates_13_char_password(admin: TestClient):
    ext = make_ext(admin)
    assert re.fullmatch(r"[A-Za-z0-9]{13}", ext["sip_password"])
    # The list view doesn't expose passwords.
    listed = admin.get("/api/extensions").json()
    assert listed[0]["number"] == "101"
    assert "sip_password" not in listed[0]


def test_duplicate_extension_rejected(admin: TestClient):
    make_ext(admin)
    r = admin.post("/api/extensions", json={"number": "101", "name": "Other"})
    assert r.status_code == 409


@pytest.mark.parametrize(
    "body",
    [
        {"number": "1", "name": "Too short"},
        {"number": "0123", "name": "Leading zero"},
        {"number": "12a", "name": "Letters"},
        {"number": "101", "name": 'Quote"Injection'},
        {"number": "101", "name": "New\nline"},
        {"number": "101", "name": "Semi;colon"},
        {"number": "101", "name": ""},
        {"number": "101", "name": "Ok", "sip_password": "short"},
        {"number": "101", "name": "Ok", "sip_password": "fourteenchars1"},
        {"number": "101", "name": "Ok", "sip_password": "has space 12"},
    ],
)
def test_extension_validation(admin: TestClient, body):
    assert admin.post("/api/extensions", json=body).status_code == 422


def test_update_and_regenerate_password(admin: TestClient):
    ext = make_ext(admin)
    r = admin.patch(f"/api/extensions/{ext['id']}", json={"name": "Alice B", "enabled": False})
    assert r.json()["name"] == "Alice B"
    assert r.json()["enabled"] is False
    r = admin.patch(f"/api/extensions/{ext['id']}", json={"regenerate_password": True})
    assert r.json()["sip_password"] != ext["sip_password"]
    r = admin.patch(f"/api/extensions/{ext['id']}", json={"sip_password": "MyPass12345"})
    assert r.json()["sip_password"] == "MyPass12345"
    # The audit log records the change without the password itself.
    entries = admin.get("/api/audit").json()
    assert not any("MyPass12345" in str(e) for e in entries)


def test_renumber_conflict(admin: TestClient):
    make_ext(admin, "101")
    b = make_ext(admin, "102", "Bob")
    assert admin.patch(f"/api/extensions/{b['id']}", json={"number": "101"}).status_code == 409


def test_phone_assign_and_settings_file(admin: TestClient):
    ext = make_ext(admin)
    r = admin.post("/api/phones", json={"mac": "00-1B-4F-AA-BB-CC", "label": "Front desk"})
    assert r.status_code == 201, r.text
    phone = r.json()
    assert phone["mac"] == "00:1b:4f:aa:bb:cc"

    # Unassigned: no auto-login lines.
    text = admin.get(f"/api/phones/{phone['id']}/settings-file").json()["content"]
    assert "SET FORCE_SIP_" not in text

    r = admin.patch(f"/api/phones/{phone['id']}", json={"extension_id": ext["id"]})
    assert r.json()["extension_number"] == "101"

    text = admin.get(f"/api/phones/{phone['id']}/settings-file").json()["content"]
    assert "SET FORCE_SIP_EXTENSION 101" in text
    assert "SET FORCE_SIP_PASSWORD ********" in text
    assert ext["sip_password"] not in text
    revealed = admin.get(f"/api/phones/{phone['id']}/settings-file?reveal=true").json()
    assert f"SET FORCE_SIP_PASSWORD {ext['sip_password']}" in revealed["content"]

    # Changing only the label keeps the assignment; null unassigns.
    r = admin.patch(f"/api/phones/{phone['id']}", json={"label": "Lobby"})
    assert r.json()["extension_number"] == "101"
    r = admin.patch(f"/api/phones/{phone['id']}", json={"extension_id": None})
    assert r.json()["extension_number"] is None


def test_extension_can_only_be_on_one_phone(admin: TestClient):
    ext = make_ext(admin)
    admin.post("/api/phones", json={"mac": "001b4f000001", "extension_id": ext["id"]})
    r = admin.post("/api/phones", json={"mac": "001b4f000002", "extension_id": ext["id"]})
    assert r.status_code == 409


def test_bad_mac_and_duplicate_phone(admin: TestClient):
    assert admin.post("/api/phones", json={"mac": "not-a-mac"}).status_code == 422
    assert admin.post("/api/phones", json={"mac": "001b4f000001"}).status_code == 201
    assert admin.post("/api/phones", json={"mac": "00:1b:4f:00:00:01"}).status_code == 409


def test_deleting_extension_unassigns_phone(admin: TestClient):
    ext = make_ext(admin)
    phone = admin.post("/api/phones", json={"mac": "001b4f000001", "extension_id": ext["id"]})
    assert admin.delete(f"/api/extensions/{ext['id']}").status_code == 204
    phones = admin.get("/api/phones").json()
    assert phones[0]["id"] == phone.json()["id"]
    assert phones[0]["extension_id"] is None


def test_settings_roundtrip_and_validation(admin: TestClient):
    s = admin.get("/api/settings").json()
    assert re.fullmatch(r"\d{6}", s["phone_admin_password"])
    for readonly in ("detected_server_ip", "provisioning_port", "syslog_port"):
        s.pop(readonly)
    s["server_ip"] = "10.0.0.5"
    s["extra_46xx_settings"] = "SET BAKLIGHTOFF 60\n## comment"
    r = admin.put("/api/settings", json=s)
    assert r.status_code == 200, r.text
    assert admin.get("/api/settings").json()["server_ip"] == "10.0.0.5"

    for field, bad in [
        ("server_ip", "pbx.example.com"),  # phones need a numeric IP
        ("gmt_offset", "five"),
        ("phone_admin_password", "12ab"),
        ("extra_46xx_settings", "rm -rf /"),
        ("dialplan_override", "1XX\nSET X 1"),
        ("ntp_server", "bad host;"),
    ]:
        r = admin.put("/api/settings", json={**s, field: bad})
        assert r.status_code == 422, field
