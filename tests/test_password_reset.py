"""Admin-driven password reset flow (2026-08-16).

The app has no email system, so recovery routes through the admin: the player
confirms their username on /auth/forgot, and the admin sets/generates a new
password from Admin → Link accounts.
"""
import re

from werkzeug.security import check_password_hash


def _admin_client(app):
    client = app.test_client()
    client.post("/auth/login", data={"username": "admin", "password": "admin123"})
    return client


def _signup(app, username="bob", password="pass1234"):
    auth = app.extensions["auth_service"]
    return auth.signup(username, password, username.title())


# --- forgot-password page --------------------------------------------------
def test_forgot_page_renders(app):
    body = _admin_client(app).get("/auth/forgot").data.decode()
    assert "Forgot password" in body
    assert 'name="username"' in body


def test_forgot_unknown_username_flashes_error(app):
    client = app.test_client()
    r = client.post("/auth/forgot", data={"username": "nobody"},
                    follow_redirects=True)
    assert "No account found" in r.data.decode()


def test_forgot_existing_user_points_to_admin(app):
    _signup(app)
    client = app.test_client()
    r = client.post("/auth/forgot", data={"username": "bob"},
                    follow_redirects=True)
    body = r.data.decode()
    assert "Found the account" in body
    assert "bob" in body
    assert "Reset password" in body


def test_forgot_admin_username_points_to_env(app):
    client = app.test_client()
    r = client.post("/auth/forgot", data={"username": "admin"},
                    follow_redirects=True)
    assert ".env" in r.data.decode()


# --- reset service ---------------------------------------------------------
def test_reset_password_service_changes_hash(app):
    auth = app.extensions["auth_service"]
    user = _signup(app)
    auth.reset_password(user["id"], "newpass99")
    assert auth.login("bob", "newpass99") is not None
    assert auth.login("bob", "pass1234") is None
    with app.extensions["db"].read() as conn:
        row = conn.execute("SELECT password_hash FROM users WHERE id = ?",
                           (user["id"],)).fetchone()
    assert check_password_hash(row["password_hash"], "newpass99")


def test_reset_password_service_rejects_short(app):
    auth = app.extensions["auth_service"]
    user = _signup(app)
    try:
        auth.reset_password(user["id"], "abc")
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "at least 4 characters" in str(exc)


def test_reset_password_service_rejects_admin(app):
    auth = app.extensions["auth_service"]
    admin = auth.get_by_username("admin")
    try:
        auth.reset_password(admin["id"], "hackedpw1")
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "configuration" in str(exc)


# --- admin route -----------------------------------------------------------
def test_admin_reset_route_sets_password(app):
    user = _signup(app)
    client = _admin_client(app)
    r = client.post(f"/auth/admin/reset-password/{user['id']}",
                    data={"new_password": "freshpass1", "action": "set"},
                    follow_redirects=True)
    assert "Password reset for bob" in r.data.decode()
    auth = app.extensions["auth_service"]
    assert auth.login("bob", "freshpass1") is not None
    assert auth.login("bob", "pass1234") is None


def test_admin_reset_route_generates_password(app):
    user = _signup(app)
    client = _admin_client(app)
    r = client.post(f"/auth/admin/reset-password/{user['id']}",
                    data={"action": "generate"}, follow_redirects=True)
    body = r.data.decode()
    m = re.search(r"Generated a new password for bob: (\S+)", body)
    assert m, "generated password should be flashed for the admin to copy"
    auth = app.extensions["auth_service"]
    assert auth.login("bob", m.group(1)) is not None


def test_admin_reset_route_validates_length(app):
    user = _signup(app)
    client = _admin_client(app)
    r = client.post(f"/auth/admin/reset-password/{user['id']}",
                    data={"new_password": "x", "action": "set"},
                    follow_redirects=True)
    assert "at least 4 characters" in r.data.decode()


def test_admin_reset_route_requires_admin(app):
    user = _signup(app)
    auth = app.extensions["auth_service"]
    client = app.test_client()  # not logged in
    r = client.post(f"/auth/admin/reset-password/{user['id']}",
                    data={"new_password": "hackedpw1", "action": "set"})
    assert r.status_code == 302
    assert "/auth/login" in r.headers["Location"]
    # password unchanged
    assert auth.login("bob", "pass1234") is not None


def test_admin_reset_unknown_user_flashes_error(app):
    client = _admin_client(app)
    r = client.post("/auth/admin/reset-password/nope",
                    data={"new_password": "freshpass1", "action": "set"},
                    follow_redirects=True)
    assert "User not found" in r.data.decode()
