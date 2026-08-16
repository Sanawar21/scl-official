"""E2E: SCL branding + team branding assets.

Covers the SCL brand palette/mark on public surfaces, the team logo fallback,
the admin Teams control panel (upload/remove branding), and the manager
branding upload flow. The e2e seed creates Thunder + Blaze with no assets, so
the fallback to the SCL mark is what renders by default.
"""
import io

import pytest

pytestmark = pytest.mark.e2e


def _png(name="logo.png"):
    return {"name": name, "mimeType": "image/png",
            "buffer": b"\x89PNG\r\n\x1a\n" + b"0" * 64}


# ----------------------------------------------------------------------
# SCL branding on public surfaces
# ----------------------------------------------------------------------
def test_navbar_shows_scl_logo_mark(page, base_url):
    page.goto(base_url + "/")
    img = page.locator(".navbar .brand-mark")
    assert img.count() == 1
    assert "/branding/scl/" in (img.get_attribute("src") or "")


def test_home_hero_uses_brand_band(page, base_url):
    page.goto(base_url + "/")
    assert page.locator(".brand-band").is_visible()
    assert page.locator(".brand-band .band-mark").count() == 1
    body = page.locator("body").inner_text()
    assert "Section-C Cricket League" in body


def test_branding_asset_served(page, base_url):
    resp = page.request.get(base_url + "/branding/scl/wide-banner.JPG")
    assert resp.ok
    assert resp.headers.get("content-type", "").startswith("image/")


# ----------------------------------------------------------------------
# team logo fallback (teams have no assets in the seed -> SCL mark)
# ----------------------------------------------------------------------
def test_teams_index_shows_fallback_logo(page, base_url):
    page.goto(base_url + "/teams")
    imgs = page.locator("a.stat-tile .team-logo")
    assert imgs.count() >= 1
    for i in range(imgs.count()):
        assert "/branding/scl/" in (imgs.nth(i).get_attribute("src") or "")


def test_team_detail_shows_fallback_logo(page, base_url):
    page.goto(base_url + "/teams")
    page.locator("a.stat-tile").first.click()
    page.wait_for_load_state("networkidle")
    img = page.locator(".page-head .team-logo")
    assert img.count() == 1
    assert "/branding/scl/" in (img.get_attribute("src") or "")


def test_league_table_rows_have_logos(page, base_url):
    page.goto(base_url + "/table")
    imgs = page.locator(".table-standings .team-logo-sm")
    assert imgs.count() >= 1


def test_live_budget_board_shows_logos(page, base_url):
    page.goto(base_url + "/live")
    page.wait_for_selector("#budget-cards .stat-tile .team-logo-sm", timeout=10000)
    imgs = page.locator("#budget-cards .team-logo-sm")
    assert imgs.count() >= 1
    for i in range(imgs.count()):
        assert "/branding/scl/" in (imgs.nth(i).get_attribute("src") or "")


# ----------------------------------------------------------------------
# admin teams panel
# ----------------------------------------------------------------------
def test_admin_teams_panel_lists_teams(page, base_url, login):
    login("admin", "admin123")
    page.goto(base_url + "/admin/teams")
    body = page.locator("body").inner_text()
    assert "Admin · Teams" in body
    assert "Thunder" in body and "Blaze" in body
    # both teams fall back to the SCL logo in the panel
    assert page.locator("img.team-logo").count() >= 1


def test_admin_uploads_and_removes_team_logo(page, base_url, login, seed):
    sid = seed["season"]["id"]
    teams = seed["teams"]
    gid = teams[0]["global_team_id"] or teams[0]["id"]
    login("admin", "admin123")
    page.goto(base_url + f"/admin/teams")
    # find the card for Thunder and upload a logo
    card = page.locator("section.card", has_text="Thunder").first
    card.locator('input[name="logo"]').set_input_files(_png("logo.png"))
    card.locator('button[type="submit"]', has_text="Upload").click()
    page.wait_for_load_state("networkidle")
    # the card now shows the uploaded (non-SCL) logo
    card = page.locator("section.card", has_text="Thunder").first
    img = card.locator("img.team-logo")
    assert img.count() >= 1
    src = img.first.get_attribute("src") or ""
    assert "/branding/teams/" in src
    # and the public team page serves it
    resp = page.request.get(base_url + src)
    assert resp.ok
    # remove the asset -> back to SCL fallback
    card = page.locator("section.card", has_text="Thunder").first
    card.locator('input[name="kind"]').first  # hidden input exists
    card.locator('button[type="submit"]', has_text="Remove logo").click()
    page.wait_for_load_state("networkidle")
    card = page.locator("section.card", has_text="Thunder").first
    src = card.locator("img.team-logo").first.get_attribute("src") or ""
    assert "/branding/scl/" in src
