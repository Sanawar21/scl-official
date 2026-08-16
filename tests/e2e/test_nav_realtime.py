"""Nav consolidation + real-time auction updates (2026-08-16 increment).

Covers:
1. The "More" dropdown hides Scorer/Docs/Changelog and opens on click.
2. The pulsating "Live Auction" nav chip appears for a manager when their
   season's auction is in a bidding phase (and links to /manager).
3. Real-time: when the admin closes a lot, the manager's open dashboard
   updates on its own (socket push — no manual refresh).
4. The vendored socket.io client is loaded on every page.
"""
import time

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
