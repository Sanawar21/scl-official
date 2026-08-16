"""Phase 3 e2e: player/manager surfaces.

Banking/vault (balance hero, deposit, vault position cards, transaction
filter), wagers (market cards with pool bars + fair odds, stake flow with the
live "you'd win X" preview), and the manager dashboard (team hub, squad,
bid controls, trades).
"""
import pytest

pytestmark = pytest.mark.e2e


# ----------------------------------------------------------------------
# banking / account
# ----------------------------------------------------------------------
def test_account_balance_hero(page, base_url, login):
    login("alice", "alicepw")
    body = page.locator("body").inner_text().lower()
    assert "liquid cash" in body
    assert "locked (vault)" in body
    assert "4500" in body  # 5000 seed minus the 500 opening stake


def test_account_manager_callout_only_for_managers(page, base_url, login):
    login("alice", "alicepw")
    assert "your wallet is the team's money" not in page.locator("body").inner_text().lower()
    login("dave", "davepw")
    page.goto(base_url + "/account")
    assert "your wallet is the team's money" in page.locator("body").inner_text().lower()


def test_player_creates_and_edits_team_account(page, base_url, login):
    """A linked player starts a team account (no season), then edits it."""
    login("alice", "alicepw")
    page.goto(base_url + "/account")
    page.fill("#team-create-form input[name='name']", "Alice All-Stars")
    page.click("#team-create-form button[type='submit']")
    page.wait_for_selector("#team-update-form", timeout=10000)
    body = page.locator("body").inner_text().lower()
    assert "my team" in body
    assert "isn't registered for a season yet" in body
    # Edit the profile: rename + add about.
    page.fill("#team-update-form input[name='name']", "Alice All-Stars FC")
    page.fill("#team-update-form textarea[name='about']", "Managed by Alice")
    page.click("#team-update-form button[type='submit']")
    # The update reloads the page; wait for the persisted name in the input.
    page.wait_for_function(
        "document.querySelector(\"#team-update-form input[name='name']\")?.value === 'Alice All-Stars FC'",
        timeout=10000)
    # The page has already reloaded with the saved profile.
    assert page.input_value("#team-update-form input[name='name']") == "Alice All-Stars FC"
    assert page.input_value("#team-update-form textarea[name='about']") == "Managed by Alice"
    # Let the form-triggered reload finish before navigating away.
    page.wait_for_load_state("networkidle")
    # The public team page shows the team (global-only, no season).
    page.goto(base_url + "/teams")
    assert "Alice All-Stars FC" in page.locator("body").inner_text()


def test_unlinked_account_banner(page, base_url):
    page.goto(base_url + "/auth/signup")
    page.fill('input[name="username"]', "unlinked1")
    page.fill('input[name="password"]', "unlinkedpw")
    page.click('button[type="submit"]')
    page.wait_for_load_state("networkidle")
    assert "isn't linked to a player yet" in page.locator("body").inner_text()


def test_vault_position_card_and_reinvest(page, base_url, login, seed):
    login("alice", "alicepw")
    page.select_option("#vault-lock-form select[name='season_id']", seed["season"]["id"])
    page.fill("#vault-lock-form input[name='amount']", "1000")
    page.click("#vault-lock-form button[type='submit']")
    page.wait_for_selector("text=Locked until M12", timeout=10000)
    body = page.locator("body").inner_text().lower()
    assert "vault positions" in body
    assert "compound" in body  # reinvest mode shown
    # the reinvest toggle button exists
    assert page.locator(".js-reinvest").count() >= 1


def test_transactions_filter(page, base_url, login):
    login("alice", "alicepw")
    page.fill("#txn-filter", "opening stake")
    visible = page.locator("#txn-table tbody tr:visible").count()
    assert visible == 1
    page.fill("#txn-filter", "zzz-nothing")
    assert page.locator("#txn-table tbody tr:visible").count() == 0


# ----------------------------------------------------------------------
# wagers board + detail
# ----------------------------------------------------------------------
def test_wagers_board_market_card(page, base_url, seed):
    page.goto(base_url + "/wagers")
    card = page.locator(".card", has_text="Will Thunder win Match 1?").first
    assert card.is_visible()
    body = card.inner_text().lower()
    assert "vetted" in body
    assert "pot" in body
    assert "500" in body
    assert card.locator(".pool-bar").count() == 1
    # fair odds shown once calibrated
    assert "fair" in body


def test_wager_detail_pools_and_fair_odds(page, base_url, seed):
    page.goto(base_url + f"/wagers/{seed['wager']['id']}")
    body = page.locator("body").inner_text()
    assert "Will Thunder win Match 1?" in body
    assert "vetted" in body
    assert "2.50x" in body or "2.5x" in body  # Yes fair = 100/(100-60)
    assert "Place a stake" in body


def test_wager_stake_you_would_win_preview(page, base_url, login, seed):
    login("cara", "carapw")
    page.goto(base_url + f"/wagers/{seed['wager']['id']}")
    page.fill("#stake-amount", "100")
    # side Yes fair odds = 2.5x -> you'd win 250
    preview = page.locator("#win-preview").inner_text()
    assert "250" in preview and "2.5" in preview
    # switch to No -> fair 100/60 = 1.67x -> you'd win 167
    page.select_option("#stake-side", "No")
    preview = page.locator("#win-preview").inner_text()
    assert "167" in preview


# ----------------------------------------------------------------------
# manager dashboard
# ----------------------------------------------------------------------
def test_manager_team_hub(page, base_url, login):
    login("dave", "davepw")
    assert "/manager" in page.url
    body = page.locator("body").inner_text().lower()
    assert "thunder" in body
    assert "wallet (purse)" in body
    assert "credits left" in body
    assert "spent" in body
    assert "current lot" in body
    assert "my squad" in body
    assert "trades" in body


def test_manager_squad_sections(page, base_url, login):
    login("dave", "davepw")
    body = page.locator("body").inner_text().upper()
    assert "XI" in body
    # the seeded team has no bought players yet -> empty state, no bench block
    assert "NO PLAYERS BOUGHT YET" in body
    assert "TRADES" in body


def test_manager_bid_controls_render(page, base_url, login):
    login("dave", "davepw")
    # bid controls are populated by JS from the state endpoint
    page.wait_for_selector("#bid-controls", timeout=10000)
    # in the seeded setup phase there is no nominated player -> empty state
    body = page.locator("#current-lot").inner_text().lower()
    assert "no player nominated" in body
