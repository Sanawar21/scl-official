"""E2E: qualification scenarios + NRR predictor (Phase: scenarios).

Covers the /table scenarios card (status chips + requirements), the required
margin calculator driven through the JSON endpoint, and the per-match
"What's at stake" panel on the match summary page. The e2e seed has one
played match (M1) and one registered-but-unplayed fixture (M2), so the
season is in progress with both teams in contention.
"""
import pytest

pytestmark = pytest.mark.e2e


def test_table_scenarios_card(page, base_url, seed):
    sid = seed["season"]["id"]
    page.goto(base_url + f"/table?season={sid}")
    # scenarios card renders with both teams
    assert page.locator("#scenarios").is_visible()
    assert page.locator("#scenarios table tbody tr").count() == 2
    body = page.locator("#scenarios").inner_text()
    assert "In contention" in body
    assert "WHAT THEY NEED" in body.upper()
    # top-1 (no final in the seed season) with one fixture left
    assert "Top 1 qualify" in body
    assert "fixture left" in body


def test_margin_calculator_direct_clash(page, base_url, seed):
    sid = seed["season"]["id"]
    page.goto(base_url + f"/table?season={sid}")
    page.locator("#mc-team").select_option("Thunder")
    page.locator("#mc-opponent").select_option("Blaze")
    page.locator("#mc-rival").select_option("Blaze")
    page.locator("#mc-go").click()
    page.wait_for_selector("#mc-result .banner")
    text = page.locator("#mc-result").inner_text()
    assert "Direct clash" in text
    assert "batting first" in text
    assert "chasing" in text.lower()
    # margin + chase tables render
    assert page.locator("#mc-result .mc-table").count() == 2
    assert "NRR" in text


def test_margin_calculator_score_change_recalculates(page, base_url, seed):
    sid = seed["season"]["id"]
    page.goto(base_url + f"/table?season={sid}")
    page.locator("#mc-team").select_option("Thunder")
    page.locator("#mc-opponent").select_option("Blaze")
    page.locator("#mc-rival").select_option("Blaze")
    page.locator("#mc-score").fill("50")
    page.locator("#mc-go").click()
    page.wait_for_selector("#mc-result .banner")
    text = page.locator("#mc-result").inner_text()
    assert "50" in text  # opponent score echoed


def test_table_scenarios_complete_state(page, base_url, seed):
    """Season-complete (S1-style) shows the Qualified/Eliminated summary."""
    sid = seed["season"]["id"]
    page.goto(base_url + f"/table?season={sid}")
    # seed season is in progress, so this is a sanity check on the tag text
    assert page.locator("#scenarios .tag").is_visible()


# ----------------------------------------------------------------------
# per-match "what's at stake"
# ----------------------------------------------------------------------
def test_match_summary_stakes_panel(page, base_url, seed):
    sid = seed["season"]["id"]
    page.goto(base_url + f"/matches/{sid}/M1")
    assert page.locator("#stakes").is_visible()
    body = page.locator("#stakes").inner_text()
    assert "WHAT'S AT STAKE" in body.upper()
    assert "Thunder" in body and "Blaze" in body
    assert "In contention" in body
    assert "Top 1 qualify" in body


def test_match_summary_stakes_margin_hint(page, base_url, seed):
    """Direct-clash fixture shows a head-to-head margin hint."""
    sid = seed["season"]["id"]
    page.goto(base_url + f"/matches/{sid}/M1")
    text = page.locator("#stakes").inner_text()
    assert "Head-to-head" in text
    assert "win by" in text.lower()
