"""Branding: SCL asset serving, team asset resolution + fallback, uploads,
and the admin teams control panel."""
import io

import pytest

from tests.conftest import _setup
from tests.test_wager import _linked_user


@pytest.fixture()
def bank(app):
    return app.extensions["bank_service"]


def _png(filename="logo.png"):
    buf = io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
    buf.filename = filename
    return buf


def _login_admin(app):
    client = app.test_client()
    r = client.post("/auth/login", data={"username": "admin", "password": "admin123"})
    assert r.status_code == 302
    return client


def _global_id(team):
    return team["global_team_id"]


# ----------------------------------------------------------------------
# branding service
# ----------------------------------------------------------------------
def test_scl_asset_urls(app):
    branding = app.extensions["branding_service"]
    url = branding.scl_url("logo")
    assert url.startswith("/branding/scl/")


def test_team_logo_fallback_to_scl(app):
    branding = app.extensions["branding_service"]
    # No assets -> SCL square mark.
    assert branding.team_logo({}) == branding.scl_url("logo")
    assert branding.team_banner({}) == branding.scl_url("banner")
    # External URL passes through.
    assert branding.team_logo({"logo": "https://x/logo.png"}) == "https://x/logo.png"
    # Relative key resolves under /branding/.
    assert branding.team_logo({"logo": "teams/abc/logo.png"}) == "/branding/teams/abc/logo.png"


def test_branding_asset_route_serves_and_blocks_traversal(app):
    client = app.test_client()
    r = client.get("/branding/scl/wide-banner.JPG")
    assert r.status_code == 200
    assert r.content_type.startswith("image/")
    assert client.get("/branding/../scl.db").status_code == 404
    assert client.get("/branding/teams/nope/logo.png").status_code == 404


# ----------------------------------------------------------------------
# admin teams panel + uploads
# ----------------------------------------------------------------------
def test_admin_teams_page_lists_teams(app):
    season, _, teams = _setup(app, n_teams=2)
    client = _login_admin(app)
    body = client.get("/admin/teams").data.decode("utf-8")
    assert "Admin · Teams" in body
    for t in teams:
        assert t["name"] in body
    # Teams have the SCL fallback logo in the list.
    assert "/branding/scl/" in body


def test_admin_upload_logo_and_remove(app, bank):
    season, _, teams = _setup(app, n_teams=2)
    sid = season["id"]
    gid = _global_id(teams[0])
    client = _login_admin(app)
    r = client.post(f"/admin/teams/{gid}/branding",
                    data={"logo": (_png(), "logo.png")},
                    content_type="multipart/form-data", follow_redirects=True)
    assert r.status_code == 200
    team = app.extensions["auction_service"].get_global_team(gid)
    assert team["logo"].startswith(f"teams/{gid}/logo.")
    # The uploaded file is servable.
    assert client.get(f"/branding/{team['logo']}").status_code == 200
    # Remove restores the fallback.
    client.post(f"/admin/teams/{gid}/branding/remove", data={"kind": "logo"},
                follow_redirects=True)
    assert app.extensions["auction_service"].get_global_team(gid)["logo"] == ""
    # The season row's wallet is untouched by branding ops.
    acct = bank.account_for_owner("player", teams[0]["manager_player_id"])
    assert acct["liquid_cash"] == 10000


def test_admin_upload_rejects_bad_extension(app):
    season, _, teams = _setup(app, n_teams=2)
    client = _login_admin(app)
    gid = _global_id(teams[0])
    bad = io.BytesIO(b"<script>alert(1)</script>")
    bad.filename = "logo.html"
    r = client.post(f"/admin/teams/{gid}/branding",
                    data={"logo": (bad, "logo.html")},
                    content_type="multipart/form-data", follow_redirects=True)
    assert r.status_code == 200
    assert "Unsupported file type" in r.data.decode("utf-8")
    assert app.extensions["auction_service"].get_global_team(gid)["logo"] == ""


def test_admin_delete_team_keeps_wallet(app, bank):
    season, _, teams = _setup(app, n_teams=2)
    gid = _global_id(teams[0])
    client = _login_admin(app)
    client.post(f"/admin/teams/{gid}/delete", follow_redirects=True)
    assert app.extensions["auction_service"].get_global_team(gid) is None
    acct = bank.account_for_owner("player", teams[0]["manager_player_id"])
    assert acct["liquid_cash"] == 10000


def test_admin_create_team_via_panel(app):
    season, players, _ = _setup(app, n_teams=1)
    # players[4] (Eve) is unassigned.
    gp = players[4]["global_player_id"]
    client = _login_admin(app)
    r = client.post("/admin/teams/create",
                    data={"name": "Osprey", "manager_player_id": gp},
                    follow_redirects=True)
    assert r.status_code == 200
    assert "Osprey" in r.data.decode("utf-8")


# ----------------------------------------------------------------------
# manager branding upload (own team only)
# ----------------------------------------------------------------------
def _login_user(app, username, password="pass1234"):
    client = app.test_client()
    client.post("/auth/login", data={"username": username, "password": password})
    return client


def test_manager_uploads_banner_for_own_team(app):
    season, players, teams = _setup(app, n_teams=2)
    gid = _global_id(teams[0])
    _linked_user(app, "mgr1", players[0]["global_player_id"])
    client = _login_user(app, "mgr1")
    r = client.post("/account/team/branding",
                    data={"team_id": gid, "banner": (_png("banner.png"), "banner.png")},
                    content_type="multipart/form-data")
    assert r.status_code == 200
    assert r.get_json()["ok"] is True
    team = app.extensions["auction_service"].get_global_team(gid)
    assert team["banner"].startswith(f"teams/{gid}/banner.")


def test_manager_cannot_upload_other_teams(app):
    season, players, teams = _setup(app, n_teams=2)
    _linked_user(app, "mgr1", players[0]["global_player_id"])
    other_gid = _global_id(teams[1])
    client = _login_user(app, "mgr1")
    r = client.post("/account/team/branding",
                    data={"team_id": other_gid, "logo": (_png(), "logo.png")},
                    content_type="multipart/form-data")
    assert r.status_code == 403


def test_account_page_shows_scl_fallback_logo(app):
    season, players, teams = _setup(app, n_teams=2)
    _linked_user(app, "mgr1", players[0]["global_player_id"])
    client = _login_user(app, "mgr1")
    body = client.get("/account").data.decode("utf-8")
    assert "/branding/scl/" in body
    assert "Upload branding" in body
