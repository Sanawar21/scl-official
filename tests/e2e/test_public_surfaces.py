"""Phase 2 e2e: public surfaces in the new design system.

Live auction board, published season, matches index/summary, league table,
leaderboards (tabs + podium), team/player profiles, and public finances.
The e2e seed includes one finalized match (M1) and a published snapshot.
"""
import pytest

pytestmark = pytest.mark.e2e


# ----------------------------------------------------------------------
# live auction board
# ----------------------------------------------------------------------
def test_live_board_renders_stepper_and_budget(page, base_url):
    page.goto(base_url + "/live")
    assert page.locator("#phase-stepper").is_visible()
    stepper = page.locator("#phase-stepper").inner_text().lower()
    assert "trade break" in stepper  # phase order includes the break
    # budget board as cards; toggle to table
    assert page.locator("#budget-cards .stat-tile").count() >= 1
    page.click("#budget-toggle")
    assert page.locator("#budget-table-wrap").is_visible()
    page.click("#budget-toggle")
    assert page.locator("#budget-cards").is_visible()


def test_live_board_lot_empty_state(page, base_url):
    # no player nominated in the seeded season -> empty state
    page.goto(base_url + "/live")
    body = page.locator("body").inner_text().lower()
    assert "no player nominated" in body


# ----------------------------------------------------------------------
# matches
# ----------------------------------------------------------------------
def test_matches_index_result_cards(page, base_url):
    page.goto(base_url + "/matches")
    cards = page.locator(".result-card")
    assert cards.count() >= 1
    assert "Thunder vs Blaze" in page.locator("body").inner_text()


def test_match_summary_scorecard(page, base_url, seed):
    sid = seed["season"]["id"]
    page.goto(base_url + f"/matches/{sid}/M1")
    body = page.locator("body").inner_text()
    assert "Thunder" in body and "Blaze" in body
    assert "3/1" in body and "6/0" in body
    # call-up order: Alice (1) before Dave (2)
    assert "Alice" in body and "Dave" in body
    # fall of wickets from delivery_log
    assert "1-1 (Alice" in body
    # result banner + PDF action
    assert "Thunder won by 3 runs" in body
    assert page.locator("a[href*='scorecard']").count() >= 1


def test_match_summary_batting_order(page, base_url, seed):
    """Batsmen appear in call-up order (Batter Order), not by runs."""
    sid = seed["season"]["id"]
    page.goto(base_url + f"/matches/{sid}/M1")
    first_batter = page.locator("tbody tr").first.inner_text()
    assert "Alice" in first_batter  # Alice opened (order 1); Dave hit 2nd


# ----------------------------------------------------------------------
# league table
# ----------------------------------------------------------------------
def test_league_table_zones_and_nrr(page, base_url):
    page.goto(base_url + "/table")
    assert page.locator(".table-standings").is_visible()
    # rank 1 row carries the champion-zone class
    first_row = page.locator(".table-standings tbody tr").first
    assert "zone-champion" in (first_row.get_attribute("class") or "")
    body = page.locator("body").inner_text()
    assert "champion zone" in body
    assert "NRR" in body


# ----------------------------------------------------------------------
# leaderboards
# ----------------------------------------------------------------------
def test_leaderboards_tabs_and_podium(page, base_url):
    page.goto(base_url + "/leaderboards")
    # default tab (Runs) visible with podium badges
    assert page.locator("#panel-bat").is_visible()
    assert page.locator("#panel-bat .podium-list .podium").count() >= 1
    # switch to Wickets tab
    page.click("label[for='lb-bowl']")
    assert page.locator("#panel-bowl").is_visible()
    assert not page.locator("#panel-bat").is_visible()
    body = page.locator("#panel-bowl").inner_text()
    assert "wickets" in body.lower()


# ----------------------------------------------------------------------
# profiles
# ----------------------------------------------------------------------
def test_teams_index_and_detail(page, base_url):
    page.goto(base_url + "/teams")
    first = page.locator("a.stat-tile").first
    assert first.is_visible()
    first.click()
    page.wait_for_load_state("networkidle")
    body = page.locator("body").inner_text().lower()
    assert "record" in body and "played" in body


def test_player_profile_stats(page, base_url):
    page.goto(base_url + "/leaderboards")
    page.click("label[for='lb-fantasy']")
    link = page.locator("#panel-fantasy a").first
    link.click()
    page.wait_for_load_state("networkidle")
    body = page.locator("body").inner_text()
    assert "Career" in body and "Matches" in body


# ----------------------------------------------------------------------
# public finances
# ----------------------------------------------------------------------
def test_finances_budget_board_cards(page, base_url):
    page.goto(base_url + "/finances")
    assert page.locator(".stat-tile").count() >= 1  # team budget cards
    body = page.locator("body").inner_text().lower()
    assert "budget board" in body
    assert "credits" in body


# ----------------------------------------------------------------------
# published season
# ----------------------------------------------------------------------
def test_published_season_page(page, base_url, seed):
    sid = seed["season"]["id"]
    page.goto(base_url + f"/season/{sid}")
    body = page.locator("body").inner_text()
    assert "SEASON COMPLETE" in body.upper()
    assert "Final squads" in body
    assert "All players" in body


def test_published_player_filter(page, base_url, seed):
    sid = seed["season"]["id"]
    page.goto(base_url + f"/season/{sid}")
    # filter by name — only matching rows remain
    page.fill("#player-filter", "Alice")
    visible = page.locator("#players-table tbody tr:visible").count()
    assert visible >= 1
    hidden = page.locator("#players-table tbody tr[hidden]").count()
    assert hidden >= 1
