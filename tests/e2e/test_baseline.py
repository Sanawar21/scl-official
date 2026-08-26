"""Baseline smoke over the CURRENT UI before the redesign (Phase 0).

Locks in that every current page renders and the primary flows work, so the
redesign cannot silently break them. Deep lifecycle coverage (auction bidding,
wager calibration/resolution, match imports, etc.) ships with each phase's
own suite once the redesign lands.
"""
import pytest

pytestmark = pytest.mark.e2e


# ----------------------------------------------------------------------
# public pages
# ----------------------------------------------------------------------
def test_home_public(page, base_url):
    page.goto(base_url + "/")
    assert page.locator("a.brand").is_visible()
    body = page.locator("body").inner_text()
    for label in ("Live", "Matches", "Table", "Top", "Finances"):
        assert label in body


def test_public_pages_render(page, base_url, seed):
    sid = seed["season"]["id"]
    for path in ("/matches", f"/matches/{sid}", "/table", "/leaderboards",
                 "/finances", "/teams", "/live", "/scorer"):
        page.goto(base_url + path)
        assert page.locator("body").inner_text().strip() != "", f"blank: {path}"


def test_scorer_download(page, base_url):
    resp = page.request.get(base_url + "/scorer/download")
    assert resp.ok
    assert resp.headers.get("content-type", "").startswith("text/html")
    assert "attachment" in resp.headers.get("content-disposition", "")
    assert resp.headers["content-disposition"].endswith('.html"')
    assert len(resp.body()) > 1000


# ----------------------------------------------------------------------
# auth
# ----------------------------------------------------------------------
def test_login_admin_redirects_to_overview(page, base_url, login):
    login("admin", "admin123")
    assert "/admin" in page.url
    assert "Admin Overview" in page.locator("body").inner_text()


def test_login_manager_redirects_to_team(page, base_url, login):
    login("dave", "davepw")
    assert "/manager" in page.url
    body = page.locator("body").inner_text()
    assert "Thunder" in body
    assert "current lot" in body.lower()


def test_login_player_redirects_to_account(page, base_url, login):
    login("alice", "alicepw")
    assert "/account" in page.url
    body = page.locator("body").inner_text()
    assert "Net worth" in body
    assert "4500" in body  # 5000 seed minus the 500 wager opening stake


def test_signup_creates_unlinked_account(page, base_url):
    page.goto(base_url + "/auth/signup")
    page.fill('input[name="username"]', "newbie")
    page.fill('input[name="display_name"]', "Newbie")
    page.fill('input[name="password"]', "newbiepw")
    page.click('button[type="submit"]')
    page.wait_for_load_state("networkidle")
    assert "/account" in page.url
    assert "isn't linked to a player yet" in page.locator("body").inner_text()


def test_logout(page, base_url, login):
    login("alice", "alicepw")
    page.goto(base_url + "/auth/logout")
    page.wait_for_load_state("networkidle")
    body = page.locator("body").inner_text()
    assert "Login" in body and "Sign up" in body


# ----------------------------------------------------------------------
# banking flows (player)
# ----------------------------------------------------------------------
def test_deposit_is_admin_only(page, base_url, login):
    """Players have no deposit form — only the admin adds balance (with comment)."""
    login("alice", "alicepw")
    # No deposit form on the player account page.
    assert page.locator('form[action*="/deposit"]').count() == 0
    # Player has no deposit form — admin adds balance via bank-adjust.
    body = page.locator("body").inner_text().lower()
    assert "liquid cash" in body
    # The admin's bank-adjust form is the deposit mechanism.
    login("admin", "admin123")
    page.goto(base_url + "/admin/auction")
    body = page.locator("body").inner_text().lower()
    assert "bank adjust" in body


def test_vault_lock_flow(page, base_url, login, seed):
    login("alice", "alicepw")
    page.select_option("#vault-lock-form select[name='season_id']",
                       seed["season"]["id"])
    page.fill("#vault-lock-form input[name='amount']", "1000")
    page.click("#vault-lock-form button[type='submit']")
    page.wait_for_selector("text=Locked until M12", timeout=10000)
    body = page.locator("body").inner_text().lower()
    assert "vault positions" in body and "locked until m12" in body


# ----------------------------------------------------------------------
# admin flows
# ----------------------------------------------------------------------
def test_admin_bank_adjust(page, base_url, login, seed):
    login("admin", "admin123")
    page.goto(base_url + "/admin/auction")
    alice_gp = seed["players"][0]["global_player_id"]
    page.select_option("section#bank select[name='account_id']", f"player:{alice_gp}")
    page.fill("section#bank input[name='amount']", "100")
    page.fill("section#bank input[name='comment']", "e2e bonus")
    with page.expect_navigation():
        page.click("section#bank button[type='submit']")
    page.wait_for_load_state("networkidle")
    assert "Account adjusted" in page.locator("body").inner_text()


# ----------------------------------------------------------------------
# wagers
# ----------------------------------------------------------------------
def test_wagers_board_shows_seeded_market(page, base_url, seed):
    page.goto(base_url + "/wagers")
    body = page.locator("body").inner_text()
    assert "Will Thunder win Match 1?" in body


def test_wager_detail_renders_pools(page, base_url, seed):
    page.goto(base_url + f"/wagers/{seed['wager']['id']}")
    body = page.locator("body").inner_text()
    assert "Will Thunder win Match 1?" in body
    assert "Yes" in body and "No" in body
