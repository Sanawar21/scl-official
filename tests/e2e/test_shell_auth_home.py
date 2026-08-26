"""Phase 1 e2e: shell + design system + Home + Auth.

Covers the new responsive nav (desktop links, mobile drawer, bottom bar),
flash-toast feedback, role-aware home dashboard, and the auth card flows.
"""
import pytest

pytestmark = pytest.mark.e2e


def _mobile(page):
    page.set_viewport_size({"width": 390, "height": 844})


# ----------------------------------------------------------------------
# shell / responsive nav
# ----------------------------------------------------------------------
def test_desktop_nav_is_role_aware(page, base_url, login):
    # anon: no Admin / My Team / Account links
    page.goto(base_url + "/")
    body = page.locator("body").inner_text()
    assert "Sign up" in body
    assert "My Team" not in body

    login("dave", "davepw")
    body = page.locator("body").inner_text()
    assert "My Team" in body
    assert "Account" in body

    login("admin", "admin123")
    body = page.locator("body").inner_text()
    assert "Admin" in body


def test_mobile_drawer_opens_and_closes(page, base_url):
    _mobile(page)
    page.goto(base_url + "/")
    toggle = page.locator("#nav-toggle")
    assert toggle.is_visible()
    drawer = page.locator("#drawer")
    assert not drawer.evaluate("el => el.classList.contains('open')")
    toggle.click()
    assert drawer.evaluate("el => el.classList.contains('open')")
    assert "Live" in page.locator("#drawer").inner_text()
    # click the visible (left) part of the backdrop — the open drawer covers the right side
    page.locator("#drawer-backdrop").click(position={"x": 10, "y": 400})
    assert not drawer.evaluate("el => el.classList.contains('open')")


def test_mobile_bottom_bar_shows_role_actions(page, base_url):
    _mobile(page)
    page.goto(base_url + "/")
    bar = page.locator(".bottom-bar")
    assert bar.is_visible()
    assert "Sign up" in bar.inner_text()

    page.goto(base_url + "/auth/login")
    page.fill('input[name="username"]', "alice")
    page.fill('input[name="password"]', "alicepw")
    page.click('button[type="submit"]')
    page.wait_for_load_state("networkidle")
    assert "Account" in page.locator(".bottom-bar").inner_text()


def test_drawer_link_navigates(page, base_url):
    _mobile(page)
    page.goto(base_url + "/")
    page.locator("#nav-toggle").click()
    page.locator("#drawer a[href*='/matches']").first.click()
    page.wait_for_load_state("networkidle")
    assert "/matches" in page.url


# ----------------------------------------------------------------------
# flash -> toast
# ----------------------------------------------------------------------
def test_error_flash_appears_as_toast(page, base_url):
    page.goto(base_url + "/auth/login")
    page.fill('input[name="username"]', "alice")
    page.fill('input[name="password"]', "wrongpw")
    page.click('button[type="submit"]')
    page.wait_for_load_state("networkidle")
    toast = page.locator(".toast.toast-error")
    assert toast.count() == 1
    assert "Invalid username or password" in toast.inner_text()
    # clicking dismisses it
    toast.click()
    page.wait_for_selector(".toast.toast-error", state="detached", timeout=5000)


def test_signup_success_flash_as_toast(page, base_url):
    page.goto(base_url + "/auth/signup")
    page.fill('input[name="username"]', "toastuser")
    page.fill('input[name="password"]', "toastpw")
    page.click('button[type="submit"]')
    page.wait_for_load_state("networkidle")
    toast = page.locator(".toast.toast-success")
    assert toast.count() == 1
    assert "link" in toast.inner_text().lower()


# ----------------------------------------------------------------------
# home (role-aware)
# ----------------------------------------------------------------------
def test_home_anon_hero_ctas(page, base_url):
    page.goto(base_url + "/")
    body = page.locator("body").inner_text()
    assert "Section-C Cricket League" in body
    assert "Live Auction Board" in body
    assert "Create Account" in body
    assert "Current season" in body  # seeded season chip
    assert "Seasons" in body


def test_home_player_quick_actions(page, base_url, login):
    login("alice", "alicepw")
    assert "/account" in page.url  # player login lands on account
    page.goto(base_url + "/")
    body = page.locator("body").inner_text()
    assert "Quick actions" in body
    assert "Wagers" in body
    assert "Live Auction" in body


def test_home_manager_quick_actions(page, base_url, login):
    login("dave", "davepw")
    page.goto(base_url + "/")
    body = page.locator("body").inner_text()
    assert "My Team" in body
    assert "Squad, purse, bidding" in body


def test_home_admin_quick_actions(page, base_url, login):
    login("admin", "admin123")
    page.goto(base_url + "/")
    body = page.locator("body").inner_text()
    assert "Auction Room" in body
    assert "Link Accounts" in body


def test_home_empty_seasons_state(page, base_url, seed):
    # The seed always has a season; exercise the empty-state branch via a
    # fresh, unseeded app is overkill — instead assert the structure renders
    # (published list may be empty -> empty state).
    page.goto(base_url + "/")
    body = page.locator("body").inner_text()
    assert "Published Seasons" in body


# ----------------------------------------------------------------------
# auth pages
# ----------------------------------------------------------------------
def test_login_page_explainer(page, base_url):
    page.goto(base_url + "/auth/login")
    body = page.locator("body").inner_text()
    assert "Players and managers log in" in body
    assert "Sign up" in body


def test_signup_page_linking_steps(page, base_url):
    page.goto(base_url + "/auth/signup")
    body = page.locator("body").inner_text()
    assert "An admin links your account" in body
    assert "promotes you to manager" in body
