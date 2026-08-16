"""Nav consolidation + real-time auction updates (2026-08-16 increment).

Covers:
1. The "More" dropdown hides Scorer/Docs/Changelog and opens on click.
2. The pulsating "Live Auction" nav chip appears for a manager when their
   season's auction is in a bidding phase (and links to /manager).
3. Real-time: when the admin closes a lot, the manager's open dashboard
   updates on its own (socket push — no manual refresh).
4. The vendored socket.io client is loaded on every page.
"""
import socket
import threading
import time
import urllib.request

from playwright.sync_api import expect


def test_more_dropdown_and_socket_client(base_url, page):
    page.goto(base_url + "/")
    # Vendored socket.io client is always loaded
    assert page.locator('script[src*="socket.io.min.js"]').count() == 1
    # Secondary links are tucked inside the "More" dropdown
    more_btn = page.locator("#nav-more-btn")
    assert more_btn.is_visible()
    menu = page.locator("#nav-more-menu")
    assert not menu.evaluate("el => el.classList.contains('open')")
    more_btn.click()
    assert menu.evaluate("el => el.classList.contains('open')")
    assert menu.locator('a[href*="/scorer"]').is_visible()
    assert menu.locator('a[href*="/docs"]').is_visible()
    assert menu.locator('a[href*="/changelog"]').is_visible()
    # The drawer (mobile) lists them flat
    page.set_viewport_size({"width": 390, "height": 844})
    page.locator("#nav-toggle").click()
    drawer = page.locator("#drawer")
    assert drawer.locator('a[href*="/scorer"]').is_visible()


def test_manager_sees_live_chip_and_auto_updates(base_url, seed, login, page):
    """Full UI flow: admin moves the auction; manager sees chip + live updates.

    Phase change (phase_a_platinum → complete) is used as the realtime trigger
    so the test is deterministic regardless of how many players earlier tests
    in the shared session consumed."""
    sid = seed["season"]["id"]

    # --- admin drives the auction through the UI ---
    login("admin", "admin123")
    page.goto(base_url + f"/admin/auction?season={sid}")
    page.select_option("#phase-select", "phase_a_platinum")
    page.click('button[type="submit"]:has-text("Set phase")')
    page.wait_for_load_state("networkidle")

    # --- manager in a separate session (own browser context) ---
    ctx = page.context.browser.new_context()
    mgr_page = ctx.new_page()
    try:
        mgr_page.goto(base_url + "/auth/login")
        mgr_page.fill('input[name="username"]', "dave")
        mgr_page.fill('input[name="password"]', "davepw")
        mgr_page.click('button[type="submit"]')
        mgr_page.wait_for_load_state("networkidle")
        mgr_page.goto(base_url + "/manager")
        mgr_page.wait_for_load_state("networkidle")

        # Pulsating chip in the navbar, linking to /manager
        chip = mgr_page.locator(".navbar .nav-live")
        expect(chip).to_be_visible()
        expect(chip).to_have_attribute("href", "/manager")

        # Bid controls area always renders (even with no lot open)
        expect(mgr_page.locator("#bid-controls")).to_be_visible()
        # The phase badge shows the live phase (server-rendered value)
        badge = mgr_page.locator("#phase-badge")
        expect(badge).to_have_text("phase_a_platinum")

        # --- real-time: admin moves the auction; manager page updates alone ---
        page.bring_to_front()
        page.select_option("#phase-select", "complete")
        page.click('button[type="submit"]:has-text("Set phase")')
        page.wait_for_load_state("networkidle")

        # The manager's phase badge must flip to "complete" WITHOUT a reload
        # (socket push — the 4s poll is only a fallback).
        mgr_page.bring_to_front()
        deadline = time.time() + 12
        updated = False
        while time.time() < deadline:
            if badge.inner_text().strip() == "complete":
                updated = True
                break
            time.sleep(0.5)
        assert updated, "Manager phase badge did not update automatically"
    finally:
        ctx.close()


def _isolated_server(tmp_path_factory):
    """Boot a throwaway server on its own temp DB, isolated from the shared
    session server — so this test is deterministic no matter what other tests
    in the suite did to the shared auction."""
    from app import create_app, socketio
    from tests.e2e.conftest import _seed

    db_path = str(tmp_path_factory.mktemp("squad") / "squad.db")
    app = create_app({"SECRET_KEY": "e2e", "DB_PATH": db_path,
                      "ADMIN_USERNAME": "admin", "ADMIN_PASSWORD": "admin123"})
    seed = _seed(app)

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
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(base + "/", timeout=2) as resp:
                if resp.status == 200:
                    break
        except Exception:
            time.sleep(0.2)
    return base, seed, app


def test_squad_updates_live_after_lot_close(tmp_path_factory, page):
    """Auction-live visibility on the manager + admin pages, all socket-driven:
    1. the manager sees live bids on the lot and opponents' squads,
    2. the admin sees incoming bids without refreshing,
    3. after the lot closes, the manager's squad + wallet/spent update alone."""
    base, seed, _app = _isolated_server(tmp_path_factory)
    sid = seed["season"]["id"]

    # --- admin: phase + nominate ---
    page.goto(base + "/auth/login")
    page.fill('input[name="username"]', "admin")
    page.fill('input[name="password"]', "admin123")
    page.click('button[type="submit"]')
    page.wait_for_load_state("networkidle")
    page.goto(base + f"/admin/auction?season={sid}")
    page.select_option("#phase-select", "phase_a_platinum")
    page.click('button[type="submit"]:has-text("Set phase")')
    page.wait_for_load_state("networkidle")
    page.click('button[type="submit"]:has-text("Nominate next")')
    page.wait_for_load_state("networkidle")
    player_name = page.locator(".lot-box .lot-name").inner_text().split()[0]
    assert player_name

    # --- manager (dave → Thunder): bid the minimum ---
    ctx = page.context.browser.new_context()
    mgr = ctx.new_page()
    try:
        mgr.goto(base + "/auth/login")
        mgr.fill('input[name="username"]', "dave")
        mgr.fill('input[name="password"]', "davepw")
        mgr.click('button[type="submit"]')
        mgr.wait_for_load_state("networkidle")
        mgr.goto(base + "/manager")
        mgr.wait_for_load_state("networkidle")

        squad = mgr.locator("#squad-box")
        expect(squad).to_contain_text("No players bought yet")
        expect(mgr.locator("#stat-spent")).to_have_text("0")
        expect(mgr.locator("#stat-wallet")).to_have_text("10000")
        # opponents' squads section lists the other team
        expect(mgr.locator("#opponents-box")).to_contain_text("Blaze")

        # place the minimum bid
        bid_btn = mgr.locator("#bid-controls button.btn-primary")
        expect(bid_btn).to_be_visible()
        bid_btn.click()
        # the manager sees the live bid on the lot (socket push, no refresh)
        expect(mgr.locator("#current-lot")).to_contain_text("Current bid:")
        deadline = time.time() + 10
        while time.time() < deadline:
            if "Thunder" in mgr.locator("#lot-bids").inner_text():
                break
            time.sleep(0.5)
        assert "Thunder" in mgr.locator("#lot-bids").inner_text(), (
            "Manager does not see the live bid in the lot feed")

        # --- the leading bidder is locked out of bidding against themselves ---
        deadline = time.time() + 10
        while time.time() < deadline:
            if "You hold the highest bid" in mgr.locator("#bid-controls").inner_text():
                break
            time.sleep(0.5)
        assert "You hold the highest bid" in mgr.locator("#bid-controls").inner_text(), (
            "Manager can still bid against themselves")

        # --- admin sees the incoming bid WITHOUT refreshing, then deletes it ---
        # (phase/lot unchanged, so no reload happened — just the live feed)
        page.bring_to_front()
        deadline = time.time() + 10
        while time.time() < deadline:
            if "Thunder" in page.locator("#admin-bid-feed").inner_text():
                break
            time.sleep(0.5)
        assert "Thunder" in page.locator("#admin-bid-feed").inner_text(), (
            "Admin does not see the incoming bid without a refresh")

        # delete the mistaken bid from the admin UI (confirm dialog accepted)
        page.on("dialog", lambda d: d.accept())
        delete_btn = page.locator("#admin-bid-feed .js-delete-bid").first
        expect(delete_btn).to_be_visible()
        delete_btn.click()
        deadline = time.time() + 10
        while time.time() < deadline:
            if "No bids yet" in page.locator("#admin-bid-feed").inner_text():
                break
            time.sleep(0.5)
        assert "No bids yet" in page.locator("#admin-bid-feed").inner_text(), (
            "Deleted bid did not disappear from the admin feed")

        # the manager is unlocked again and places a fresh bid
        mgr.bring_to_front()
        deadline = time.time() + 10
        while time.time() < deadline:
            if "You hold the highest bid" not in mgr.locator("#bid-controls").inner_text():
                break
            time.sleep(0.5)
        assert "You hold the highest bid" not in mgr.locator("#bid-controls").inner_text(), (
            "Bid controls did not re-enable after the admin deleted the bid")
        expect(mgr.locator("#bid-controls button.btn-primary")).to_be_visible()
        mgr.locator("#bid-controls button.btn-primary").click()
        expect(mgr.locator("#current-lot")).to_contain_text("Current bid:")

        # --- admin sees the fresh bid, then closes the lot → sold to Thunder ---
        page.bring_to_front()
        deadline = time.time() + 10
        while time.time() < deadline:
            if "Thunder" in page.locator("#admin-bid-feed").inner_text():
                break
            time.sleep(0.5)
        assert "Thunder" in page.locator("#admin-bid-feed").inner_text()
        page.click('button[type="submit"]:has-text("Close lot")')
        page.wait_for_load_state("networkidle")

        # --- manager's squad must update WITHOUT a manual reload ---
        mgr.bring_to_front()
        deadline = time.time() + 12
        while time.time() < deadline:
            if player_name in squad.inner_text():
                break
            time.sleep(0.5)
        assert player_name in squad.inner_text(), (
            f"Squad did not update automatically after the lot closed "
            f"(expected '{player_name}' in: {squad.inner_text()})")
        # wallet + spent tiles updated too
        spent = int(mgr.locator("#stat-spent").inner_text())
        assert spent > 0, "Spent tile should reflect the sale"
        wallet = int(mgr.locator("#stat-wallet").inner_text())
        assert wallet < 10000, "Wallet tile should reflect the deduction"
        # opponents' section stays live (Blaze still empty)
        expect(mgr.locator("#opponents-box")).to_contain_text("Blaze")
    finally:
        ctx.close()


def test_trade_flow_updates_live(tmp_path_factory, page):
    """Trade flow end-to-end: dave buys a player, sends a trade to Blaze during
    the break, bob accepts — and NO page refresh is needed anywhere (the
    dashboard used to crash with 'sqlite3.Row has no attribute get' the moment
    a trade existed, and the form's rosters went stale until reload)."""
    import http.cookiejar
    import urllib.parse
    import urllib.request

    base, seed, app = _isolated_server(tmp_path_factory)
    sid = seed["season"]["id"]
    auth = app.extensions["auth_service"]
    bank = app.extensions["bank_service"]

    # bob = manager of Blaze (players[1] = Bob), with a funded wallet
    bob_user = auth.signup("bob", "bobpw", "Bob")
    bob_gp = seed["players"][1]["global_player_id"]
    auth.link_user_to_player(bob_user["id"], bob_gp)
    bob_acct = bank.get_or_create_account("player", bob_gp)
    bank.adjust(bob_acct["id"], 10000, "e2e trade funding", tx_type="funding")

    def http_session(uname, pw):
        cj = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
        def post(path, data=None):
            body = urllib.parse.urlencode(data or {}).encode()
            req = urllib.request.Request(base + path, data=body, headers={
                "Content-Type": "application/x-www-form-urlencoded"})
            with opener.open(req, timeout=10) as resp:
                return resp.status
        post("/auth/login", {"username": uname, "password": pw})
        return post

    admin = http_session("admin", "admin123")
    dave = http_session("dave", "davepw")
    bob = http_session("bob", "bobpw")

    # auction: dave wins Alice for 3100
    admin(f"/admin/season/{sid}/phase", {"phase": "phase_a_platinum"})
    admin(f"/admin/season/{sid}/nominate", {})
    dave("/manager/bid", {"amount": 3000})
    bob("/manager/bid", {"amount": 3050})
    dave("/manager/bid", {"amount": 3100})
    admin(f"/admin/season/{sid}/close", {})
    admin(f"/admin/season/{sid}/phase", {"phase": "break"})

    ctx = page.context.browser.new_context()
    dave_page, bob_page = ctx.new_page(), ctx.new_page()
    try:
        # dave's dashboard: break phase, trade form live, offer lists Alice
        dave_page.goto(base + "/auth/login")
        dave_page.fill('input[name="username"]', "dave")
        dave_page.fill('input[name="password"]', "davepw")
        dave_page.click('button[type="submit"]')
        dave_page.wait_for_load_state("networkidle")
        dave_page.goto(base + "/manager")
        dave_page.wait_for_load_state("networkidle")
        expect(dave_page.locator("#squad-box")).to_contain_text("Alice")
        offer_texts = dave_page.locator('select[name="offered_player_id"] option').evaluate_all(
            "els => els.map(e => e.textContent)")
        assert any("Alice" in t for t in offer_texts), f"offer options: {offer_texts}"

        # dave sends the trade via the UI (open the collapsible form first)
        dave_page.locator("#trade-section summary").click()
        dave_page.select_option('select[name="to_team_id"]', index=1)  # Blaze
        dave_page.select_option('select[name="offered_player_id"]', index=1)  # Alice
        dave_page.click('button[type="submit"]:has-text("Request trade")')

        # outgoing trade appears live (state_json no longer crashes on it)
        deadline = time.time() + 10
        while time.time() < deadline:
            if "Alice" in dave_page.locator("#trade-requests").inner_text():
                break
            time.sleep(0.5)
        assert "Alice" in dave_page.locator("#trade-requests").inner_text(), \
            "Outgoing trade did not appear (state_json likely crashed)"
        # a fresh server render of the dashboard also survives (was a 500)
        dave_page.goto(base + "/manager")
        dave_page.wait_for_load_state("networkidle")
        assert "Alice" in dave_page.locator("#trade-requests").inner_text()

        # bob sees the incoming trade and accepts it
        bob_page.goto(base + "/auth/login")
        bob_page.fill('input[name="username"]', "bob")
        bob_page.fill('input[name="password"]', "bobpw")
        bob_page.click('button[type="submit"]')
        bob_page.wait_for_load_state("networkidle")
        bob_page.goto(base + "/manager")
        bob_page.wait_for_load_state("networkidle")
        deadline = time.time() + 10
        while time.time() < deadline:
            if "Alice" in bob_page.locator("#trade-requests").inner_text():
                break
            time.sleep(0.5)
        assert "Alice" in bob_page.locator("#trade-requests").inner_text()
        bob_page.locator('#trade-requests button[data-action="accept"]').click()

        # bob's squad gains Alice live (no refresh)
        deadline = time.time() + 12
        while time.time() < deadline:
            if "Alice" in bob_page.locator("#squad-box").inner_text():
                break
            time.sleep(0.5)
        assert "Alice" in bob_page.locator("#squad-box").inner_text(), \
            "Blaze squad did not gain the traded player live"

        # dave's page: squad empty + offer select no longer lists Alice (live)
        dave_page.bring_to_front()
        deadline = time.time() + 12
        while time.time() < deadline:
            offer_texts = dave_page.locator('select[name="offered_player_id"] option').evaluate_all(
                "els => els.map(e => e.textContent)")
            if not any("Alice" in t for t in offer_texts):
                break
            time.sleep(0.5)
        assert not any("Alice" in t for t in offer_texts), \
            "Dave's offer select still lists the traded player (stale form)"
        assert "No players bought yet" in dave_page.locator("#squad-box").inner_text()
    finally:
        ctx.close()
