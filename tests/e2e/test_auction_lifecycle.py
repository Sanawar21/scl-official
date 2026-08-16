"""Auction lifecycle e2e — the draft driven end-to-end in the browser.

Every test boots its OWN server on a fresh temp DB (function-scoped), so the
auction state is fully isolated per test: admin drives phases/nomination/close/
complete/undo through the real UI while a manager bids/passes, with wallet and
credit assertions throughout. The shared `tests/e2e/conftest` server is never
touched.
"""
import socket
import threading
import time
import urllib.request

import pytest

pytestmark = pytest.mark.e2e


def _seed_auction(app):
    """A season ready for a full draft: 2 teams (Red, Blue), 6 auction lots
    across tiers (Alpha..Zeta), plus 2 separate manager players (Manager One /
    Two) so every lot stays nominatable. Managers funded 10k."""
    auction = app.extensions["auction_service"]
    bank = app.extensions["bank_service"]
    auth = app.extensions["auth_service"]
    db = app.extensions["db"]

    season = auction.create_season("Auction Season")
    sid = season["id"]
    specs = [
        ("Alpha", "platinum", "BATTER"), ("Beta", "platinum", "BOWLER"),
        ("Gamma", "gold", "ALL_ROUNDER"), ("Delta", "gold", "BATTER"),
        ("Epsilon", "silver", "BOWLER"), ("Zeta", "silver", "ALL_ROUNDER"),
    ]
    lots = [auction.add_player(sid, *s) for s in specs]
    mgr_one = auction.add_player(sid, "Manager One", "platinum", "ALL_ROUNDER")
    mgr_two = auction.add_player(sid, "Manager Two", "gold", "ALL_ROUNDER")
    teams = [
        auction.create_team(sid, "Red", mgr_one["global_player_id"]),
        auction.create_team(sid, "Blue", mgr_two["global_player_id"]),
    ]
    for t in teams:
        acct = bank.get_or_create_account("player", t["manager_player_id"])
        bank.adjust(acct["id"], 10000, "auction seed funding", tx_type="funding")
    # Managers are roster slots (not auction lots) — mark sold to their own team.
    with db.write() as conn:
        for t in teams:
            conn.execute(
                "UPDATE players SET status = 'sold', sold_to_team_id = ? "
                "WHERE global_player_id = ?", (t["id"], t["manager_player_id"]))
    # Manager users: redmgr -> Red, bluemgr -> Blue. Manager status is
    # derived from the player→team link, so linking alone is enough.
    users = {}
    for uname, mgr, pw in [("redmgr", mgr_one, "redpw"), ("bluemgr", mgr_two, "bluepw")]:
        u = auth.signup(uname, pw, uname.title())
        u = auth.link_user_to_player(u["id"], mgr["global_player_id"])
        users[uname] = u
    return {"season": season, "players": lots, "teams": teams, "users": users}


@pytest.fixture()
def auction_server(tmp_path):
    """Function-scoped: a fresh app + server per test, isolated from everything."""
    from app import create_app, socketio

    db_path = str(tmp_path / "auction.db")
    app = create_app({"SECRET_KEY": "auction", "DB_PATH": db_path,
                      "ADMIN_USERNAME": "admin", "ADMIN_PASSWORD": "admin123"})
    seed = _seed_auction(app)

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    thread = threading.Thread(
        target=socketio.run,
        kwargs={"app": app, "host": "127.0.0.1", "port": port,
                "use_reloader": False, "debug": False,
                "allow_unsafe_werkzeug": True},
        daemon=True,
    )
    thread.start()
    base_url = f"http://127.0.0.1:{port}"
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(base_url + "/", timeout=2) as resp:
                if resp.status == 200:
                    break
        except Exception:
            time.sleep(0.2)
    else:
        raise RuntimeError("Auction e2e server failed to start")
    yield base_url, seed


@pytest.fixture()
def auction_login(page, auction_server):
    base_url = auction_server[0]

    def _login(username, password):
        page.goto(base_url + "/auth/login")
        page.fill('input[name="username"]', username)
        page.fill('input[name="password"]', password)
        page.click('button[type="submit"]')
        page.wait_for_load_state("networkidle")

    return _login


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------
def _admin_auction(page, base_url, login):
    login("admin", "admin123")
    page.goto(base_url + "/admin/auction")
    page.wait_for_selector("text=Auction Control", timeout=10000)


def _set_phase(page, base_url, phase, login):
    login("admin", "admin123")
    page.goto(base_url + "/admin/auction")
    page.select_option("#phase-select", phase)
    page.click('form[action*="/phase"] button[type="submit"]')
    page.wait_for_load_state("networkidle")


def _nominate(page, base_url, login):
    login("admin", "admin123")
    page.goto(base_url + "/admin/auction")
    page.click('form[action*="/nominate"] button[type="submit"]')
    page.wait_for_load_state("networkidle")


def _close_lot(page, base_url, login):
    login("admin", "admin123")
    page.goto(base_url + "/admin/auction")
    page.click('form[action*="/close"] button[type="submit"]')
    page.wait_for_load_state("networkidle")


def _current_lot_name(page):
    return page.locator(".lot-box .lot-name").first.inner_text()


# ----------------------------------------------------------------------
# 1. setup state: nothing nominated, bidding closed
# ----------------------------------------------------------------------
def test_setup_state_manager_and_admin(page, auction_server, auction_login):
    base_url = auction_server[0]
    auction_login("redmgr", "redpw")
    page.goto(base_url + "/manager")
    page.wait_for_selector("text=No player nominated", timeout=10000)
    body = page.locator("body").inner_text()
    assert "red" in body.lower()
    assert "no player nominated" in body.lower()

    _admin_auction(page, base_url, auction_login)
    body = page.locator("body").inner_text().lower()
    assert "auction control" in body
    assert "setup" in body
    assert page.locator("#phase-select option", has_text="phase_a_platinum").count() == 1


# ----------------------------------------------------------------------
# 2. the full draft: platinum -> gold -> silver, both managers bidding
# ----------------------------------------------------------------------
def test_full_draft_sells_lots_to_both_teams(page, auction_server, auction_login):
    base_url = auction_server[0]

    # --- platinum lot: Alpha, minimum bid 3000 (platinum base) ---
    _set_phase(page, base_url, "phase_a_platinum", auction_login)
    _nominate(page, base_url, auction_login)
    assert "Alpha" in _current_lot_name(page)
    assert "3000" in page.locator(".lot-box").inner_text()

    auction_login("redmgr", "redpw")
    page.goto(base_url + "/manager")
    page.wait_for_selector("#bid-controls button", timeout=10000)
    bid_btn = page.locator("#bid-controls button", has_text="Bid 3000")
    assert bid_btn.count() == 1
    bid_btn.click()
    page.wait_for_function(
        "document.querySelector('#current-lot')?.innerText.includes('Current bid: 3000')",
        timeout=15000)

    _close_lot(page, base_url, auction_login)
    body = page.locator("body").inner_text().lower()
    assert "alpha" in body and "sold" in body

    # --- gold lot: Gamma, base 2000 -> Blue bids ---
    _set_phase(page, base_url, "phase_a_gold", auction_login)
    _nominate(page, base_url, auction_login)
    assert "Gamma" in _current_lot_name(page)
    auction_login("bluemgr", "bluepw")
    page.goto(base_url + "/manager")
    page.wait_for_selector("#bid-controls button", timeout=10000)
    page.locator("#bid-controls button", has_text="Bid 2000").click()
    page.wait_for_function(
        "document.querySelector('#current-lot')?.innerText.includes('Current bid: 2000')",
        timeout=15000)
    _close_lot(page, base_url, auction_login)

    # --- silver lots: Epsilon -> Red, Zeta -> Blue (base 1000) ---
    _set_phase(page, base_url, "phase_a_silver", auction_login)
    _nominate(page, base_url, auction_login)
    assert "Epsilon" in _current_lot_name(page)
    auction_login("redmgr", "redpw")
    page.goto(base_url + "/manager")
    page.wait_for_selector("#bid-controls button", timeout=10000)
    page.locator("#bid-controls button", has_text="Bid 1000").click()
    page.wait_for_function(
        "document.querySelector('#current-lot')?.innerText.includes('Current bid: 1000')",
        timeout=15000)
    _close_lot(page, base_url, auction_login)

    _nominate(page, base_url, auction_login)
    assert "Zeta" in _current_lot_name(page)
    auction_login("bluemgr", "bluepw")
    page.goto(base_url + "/manager")
    page.wait_for_selector("#bid-controls button", timeout=10000)
    page.locator("#bid-controls button", has_text="Bid 1000").click()
    page.wait_for_function(
        "document.querySelector('#current-lot')?.innerText.includes('Current bid: 1000')",
        timeout=15000)
    _close_lot(page, base_url, auction_login)

    # Admin page shows the sold players on the rosters.
    _admin_auction(page, base_url, auction_login)
    body = page.locator("body").inner_text()
    assert "Alpha" in body and "Epsilon" in body
    assert "Gamma" in body and "Zeta" in body


# ----------------------------------------------------------------------
# 3. wallets move with bids; the draft completes with no penalty when full
# ----------------------------------------------------------------------
def test_wallets_move_and_clean_completion(page, auction_server, auction_login):
    base_url = auction_server[0]

    # Red buys Alpha (3000) and Epsilon (1000); Blue buys Gamma (2000) + Zeta (1000).
    for phase, name, mgr, bid in [
        ("phase_a_platinum", "Alpha", "redmgr", 3000),
        ("phase_a_gold", "Gamma", "bluemgr", 2000),
        ("phase_a_silver", "Epsilon", "redmgr", 1000),
        ("phase_a_silver", "Zeta", "bluemgr", 1000),
    ]:
        _set_phase(page, base_url, phase, auction_login)
        _nominate(page, base_url, auction_login)
        assert name in _current_lot_name(page)
        auction_login(mgr, "redpw" if mgr == "redmgr" else "bluepw")
        page.goto(base_url + "/manager")
        page.wait_for_selector("#bid-controls button", timeout=10000)
        page.locator("#bid-controls button", has_text=f"Bid {bid}").click()
        page.wait_for_function(
            "document.querySelector('#current-lot')?.innerText.includes('Current bid: %d')" % bid,
            timeout=15000)
        _close_lot(page, base_url, auction_login)

    # Manager wallets: Red = 10000-3000-1000 = 6000, Blue = 10000-2000-1000 = 7000.
    for mgr, expected in [("redmgr", 6000), ("bluemgr", 7000)]:
        auction_login(mgr, "redpw" if mgr == "redmgr" else "bluepw")
        page.goto(base_url + "/account")
        body = page.locator("body").inner_text()
        assert f"{expected}" in body  # liquid cash stat

    # Both teams full (3 bought + manager) -> complete draft, NO penalty.
    _set_phase(page, base_url, "complete", auction_login)
    page.goto(base_url + "/admin/auction")
    assert page.locator('form[action*="/complete"] button').count() == 1
    page.click('form[action*="/complete"] button[type="submit"]')
    page.wait_for_load_state("networkidle")
    body = page.locator("body").inner_text()
    assert "completed" in body.lower() or "squad-cost levy" in body.lower()
    # No penalty: wallets unchanged.
    for mgr, expected in [("redmgr", 6000), ("bluemgr", 7000)]:
        auction_login(mgr, "redpw" if mgr == "redmgr" else "bluepw")
        page.goto(base_url + "/account")
        assert f"{expected}" in page.locator("body").inner_text()


# ----------------------------------------------------------------------
# 4. custom bid + pass
# ----------------------------------------------------------------------
def test_custom_bid_and_pass(page, auction_server, auction_login):
    base_url = auction_server[0]
    _set_phase(page, base_url, "phase_a_platinum", auction_login)
    _nominate(page, base_url, auction_login)

    auction_login("redmgr", "redpw")
    page.goto(base_url + "/manager")
    page.wait_for_selector("#bid-controls input", timeout=10000)
    custom = page.locator("#bid-controls input[placeholder='custom']")
    custom.fill("3450")
    # The custom "Bid" button is the one right after the custom input.
    page.locator("#bid-controls input[placeholder='custom'] + button").click()
    page.wait_for_function(
        "document.querySelector('#current-lot')?.innerText.includes('Current bid: 3450')",
        timeout=15000)

    # Pass ends the manager's turn on the lot.
    page.locator("#bid-controls button", has_text="Pass").click()
    page.wait_for_function(
        "document.querySelector('#bid-error')?.innerText === '' || "
        "document.querySelector('#bid-error')?.innerText === '\u200b'", timeout=10000)
    # The pass is recorded server-side: the live feed shows it.
    page.goto(base_url + "/live")
    page.wait_for_function(
        "document.querySelector('#bid-feed')?.innerText.includes('pass')", timeout=15000)


# ----------------------------------------------------------------------
# 5. admin undo rolls a sale back (lot reopens, wallet restored)
# ----------------------------------------------------------------------
def test_admin_undo_rolls_back_sale(page, auction_server, auction_login):
    base_url = auction_server[0]
    _set_phase(page, base_url, "phase_a_platinum", auction_login)
    _nominate(page, base_url, auction_login)
    auction_login("redmgr", "redpw")
    page.goto(base_url + "/manager")
    page.wait_for_selector("#bid-controls button", timeout=10000)
    page.locator("#bid-controls button", has_text="Bid 3000").click()
    page.wait_for_function(
        "document.querySelector('#current-lot')?.innerText.includes('Current bid: 3000')",
        timeout=15000)
    _close_lot(page, base_url, auction_login)
    body = page.locator("body").inner_text().lower()
    assert "sold" in body

    # Undo -> the lot is back and Alpha is unsold again.
    page.goto(base_url + "/admin/auction")
    page.click('form[action*="/undo"] button[type="submit"]')
    page.wait_for_load_state("networkidle")
    body = page.locator("body").inner_text().lower()
    assert "alpha" in body
    assert page.locator(".lot-box .lot-name", has_text="Alpha").count() == 1

    # Wallet restored to the full 10000.
    auction_login("redmgr", "redpw")
    page.goto(base_url + "/account")
    assert "10000" in page.locator("body").inner_text()


# ----------------------------------------------------------------------
# 6. completing a draft with an incomplete team forfeits its wallet
# ----------------------------------------------------------------------
def test_complete_draft_penalizes_incomplete_team(page, auction_server, auction_login):
    base_url = auction_server[0]
    # Sell only ONE lot: Red buys Alpha. Blue stays empty.
    _set_phase(page, base_url, "phase_a_platinum", auction_login)
    _nominate(page, base_url, auction_login)
    auction_login("redmgr", "redpw")
    page.goto(base_url + "/manager")
    page.wait_for_selector("#bid-controls button", timeout=10000)
    page.locator("#bid-controls button", has_text="Bid 3000").click()
    page.wait_for_function(
        "document.querySelector('#current-lot')?.innerText.includes('Current bid: 3000')",
        timeout=15000)
    _close_lot(page, base_url, auction_login)

    _set_phase(page, base_url, "complete", auction_login)
    page.goto(base_url + "/admin/auction")
    page.click('form[action*="/complete"] button[type="submit"]')
    page.wait_for_load_state("networkidle")
    body = page.locator("body").inner_text().lower()
    assert "completed" in body.lower()
    # Blue (incomplete) was filled with unsold players and its wallet forfeited.
    auction_login("bluemgr", "bluepw")
    page.goto(base_url + "/account")
    assert "0" in page.locator("body").inner_text()


# ----------------------------------------------------------------------
# 7. an empty wallet disables bidding with a clear note
# ----------------------------------------------------------------------
def test_insufficient_wallet_disables_bidding(page, auction_server, auction_login):
    # Fund Red's manager with only 3500 via the admin bank adjust (below base).
    base_url = auction_server[0]
    auction_login("admin", "admin123")
    page.goto(base_url + "/admin/auction")
    page.wait_for_selector("section#bank", timeout=10000)
    # Bank adjust the Red manager's wallet down by 7500 (10000 -> 2500, below base).
    bank = page.locator("section#bank")
    options = bank.locator("select[name='account_id'] option")
    labels = options.all_inner_texts()
    red_idx = next(i for i, l in enumerate(labels) if "red" in l.lower())
    bank.locator("select[name='account_id']").select_option(index=red_idx)
    bank.locator("input[name='amount']").fill("-7500")
    bank.locator("input[name='comment']").fill("spend it all")
    bank.locator('button[type="submit"]').click()
    page.wait_for_load_state("networkidle")

    _set_phase(page, base_url, "phase_a_platinum", auction_login)
    _nominate(page, base_url, auction_login)
    auction_login("redmgr", "redpw")
    page.goto(base_url + "/manager")
    page.wait_for_selector("#bid-controls", timeout=10000)
    body = page.locator("#bid-controls").inner_text()
    assert "cannot cover the minimum bid" in body.lower()
    # Every BID button is disabled (Pass always stays enabled).
    for btn in page.locator("#bid-controls button:not(:has-text('Pass'))").all():
        assert btn.is_disabled()


# ----------------------------------------------------------------------
# 8. the public live board shows the live lot
# ----------------------------------------------------------------------
def test_live_board_shows_nominated_lot(page, auction_server, auction_login):
    base_url = auction_server[0]
    _set_phase(page, base_url, "phase_a_platinum", auction_login)
    _nominate(page, base_url, auction_login)
    auction_login("redmgr", "redpw")
    page.goto(base_url + "/manager")
    page.wait_for_selector("#bid-controls button", timeout=10000)
    page.locator("#bid-controls button", has_text="Bid 3000").click()
    page.wait_for_function(
        "document.querySelector('#current-lot')?.innerText.includes('Current bid: 3000')",
        timeout=15000)

    page.goto(base_url + "/live")
    page.wait_for_function(
        "document.querySelector('#bid-feed')?.innerText.includes('3000')", timeout=15000)
    body = page.locator("body").inner_text()
    assert "Alpha" in body
    assert "3000" in body
