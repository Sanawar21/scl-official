"""Phase 4 e2e: admin polish.

Overview status cards with counts + primary links, the labeled bank-adjust
form, wager lifecycle steppers on the admin page, and the link-accounts page.
"""
import pytest

pytestmark = pytest.mark.e2e


def test_admin_overview_stat_cards(page, base_url, login):
    login("admin", "admin123")
    assert "/admin" in page.url
    body = page.locator("body").inner_text().lower()
    assert "admin overview" in body
    # status tiles for each section
    assert "phase" in body and "teams" in body and "sold" in body
    assert "registry" in body and "finalized" in body
    assert "team wallets" in body
    assert "resolved" in body  # wagers card
    assert "unlinked" in body
    # primary link buttons
    assert page.locator("a.btn-secondary", has_text="Open control").count() >= 1
    assert page.locator("a.btn-secondary", has_text="Scorer admin").count() >= 1


def test_admin_overview_recent_activity(page, base_url, login):
    login("admin", "admin123")
    body = page.locator("body").inner_text().lower()
    assert "recent activity" in body


def test_admin_bank_adjust_labeled_form(page, base_url, login):
    login("admin", "admin123")
    page.goto(base_url + "/admin/auction")
    bank = page.locator("section#bank")
    labels = bank.locator("label").all_inner_texts()
    assert any("account" in l.lower() for l in labels)
    assert any("amount" in l.lower() for l in labels)
    assert any("reason" in l.lower() for l in labels)


def test_admin_auction_action_log_and_undo(page, base_url, login):
    login("admin", "admin123")
    page.goto(base_url + "/admin/auction")
    body = page.locator("body").inner_text().lower()
    assert "action log & undo" in body
    assert page.locator("button", has_text="Undo last action").count() >= 1


def test_wagers_admin_lifecycle_stepper(page, base_url, login, seed):
    login("admin", "admin123")
    page.goto(base_url + "/wagers/admin")
    card = page.locator(".card", has_text="Will Thunder win Match 1?").first
    stepper = card.locator(".stepper")
    assert stepper.count() == 1
    labels = stepper.inner_text().lower()
    assert "proposed" in labels and "calibrating" in labels and "vetted" in labels
    # current step is vetted (seed market was calibrated + finalized)
    current = stepper.locator("li.current").inner_text().lower()
    assert "vetted" in current


def test_link_page_empty_state(page, base_url, login):
    login("admin", "admin123")
    page.goto(base_url + "/auth/admin/link")
    body = page.locator("body").inner_text().lower()
    assert "no unlinked signups" in body  # e2e seed links all users
    assert "all accounts" in body


def test_link_page_after_signup(page, base_url, login):
    """A fresh signup shows up in the admin unlinked list and can be linked."""
    page.goto(base_url + "/auth/signup")
    page.fill('input[name="username"]', "linkme")
    page.fill('input[name="password"]', "linkmepw")
    page.click('button[type="submit"]')
    page.wait_for_load_state("networkidle")
    login("admin", "admin123")
    page.goto(base_url + "/auth/admin/link")
    body = page.locator("body").inner_text()
    assert "linkme" in body
    # link them to the first player (the row holds the form, the name is in a sibling cell)
    row = page.locator("tbody tr", has_text="linkme").first
    row.locator("select[name='global_player_id']").select_option(index=1)
    with page.expect_navigation():
        row.locator("button[type='submit']").click()
    page.wait_for_load_state("networkidle")
    assert "Linked" in page.locator("body").inner_text()
