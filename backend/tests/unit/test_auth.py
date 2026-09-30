from fastapi.testclient import TestClient

from phonesystem.security import CSRF_HEADER


def test_first_run_setup_then_locked(client: TestClient):
    assert client.get("/api/auth/status").json() == {"setup_required": True, "user": None}

    r = client.post("/api/auth/setup", json={"username": "admin", "password": "correct horse"})
    assert r.status_code == 200
    assert client.get("/api/auth/status").json() == {
        "setup_required": False,
        "user": {"username": "admin"},
    }

    # A second setup attempt is refused.
    r = client.post("/api/auth/setup", json={"username": "evil", "password": "correct horse 2"})
    assert r.status_code == 409


def test_setup_rejects_short_password(client: TestClient):
    r = client.post("/api/auth/setup", json={"username": "admin", "password": "short"})
    assert r.status_code == 422


def test_api_requires_login(client: TestClient):
    assert client.get("/api/extensions").status_code == 401
    assert client.get("/api/settings").status_code == 401


def test_login_logout(admin: TestClient):
    admin.post("/api/auth/logout")
    assert admin.get("/api/extensions").status_code == 401

    r = admin.post("/api/auth/login", json={"username": "admin", "password": "wrong password"})
    assert r.status_code == 401
    r = admin.post("/api/auth/login", json={"username": "admin", "password": "correct horse"})
    assert r.status_code == 200
    assert admin.get("/api/extensions").status_code == 200


def test_login_throttled_after_repeated_failures(admin: TestClient):
    admin.post("/api/auth/logout")
    for _ in range(5):
        r = admin.post("/api/auth/login", json={"username": "admin", "password": "nope nope"})
        assert r.status_code == 401
    r = admin.post("/api/auth/login", json={"username": "admin", "password": "correct horse"})
    assert r.status_code == 429


def test_failed_login_is_audited(admin: TestClient):
    admin.post("/api/auth/login", json={"username": "admin", "password": "nope nope"})
    actions = [e["action"] for e in admin.get("/api/audit").json()]
    assert "admin.login_failed" in actions


def test_mutations_need_csrf_header(admin: TestClient):
    r = admin.post(
        "/api/extensions",
        json={"number": "101", "name": "Alice"},
        headers={CSRF_HEADER: ""},
    )
    assert r.status_code == 403


def test_change_password(admin: TestClient):
    r = admin.post(
        "/api/auth/password",
        json={"current_password": "wrong", "new_password": "another good one"},
    )
    assert r.status_code == 400
    r = admin.post(
        "/api/auth/password",
        json={"current_password": "correct horse", "new_password": "another good one"},
    )
    assert r.status_code == 200
    assert admin.get("/api/extensions").status_code == 200  # new session cookie issued
    admin.post("/api/auth/logout")
    r = admin.post("/api/auth/login", json={"username": "admin", "password": "another good one"})
    assert r.status_code == 200


def test_security_headers(admin: TestClient):
    r = admin.get("/api/extensions")
    assert r.headers["X-Frame-Options"] == "DENY"
    assert r.headers["X-Content-Type-Options"] == "nosniff"


def test_session_cookie_flags(client: TestClient):
    r = client.post("/api/auth/setup", json={"username": "admin", "password": "correct horse"})
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "samesite=strict" in cookie
