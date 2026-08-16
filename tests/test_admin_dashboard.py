"""Admin dashboard consolidation tests: shared tab shell + overview page."""
import re

import pytest

from tests.conftest import _setup
from tests.test_wager import _linked_user


@pytest.fixture()
def wager(app):
    return app.extensions["wager_service"]


def _login(app):
    client = app.test_client()
    r = client.post("/auth/login", data={"username": "admin", "password": "admin123"})
    assert r.status_code == 302
    return client


def _active_tab(body: str):
    nav = body.split('<nav class="admin-tabs')[1].split("</nav>")[0]
    m = re.search(r'class="tag tag-active"\s*href="[^"]*">([^<]+)<', nav)
    return m.group(1).strip() if m else None


PAGES = [
    ("/admin", "Overview"),
    ("/admin/auction", "Auction"),
    ("/admin/scorer", "Scorer"),
    ("/admin/finances", "Finances"),
    ("/wagers/admin", "Wagers"),
    ("/auth/admin/link", "Link"),
]


def test_tab_shell_on_all_admin_pages(app):
    _setup(app, n_teams=2)
    client = _login(app)
    for path, expect in PAGES:
        body = client.get(path).data.decode("utf-8")
        assert 'admin-tabs' in body, path
        assert _active_tab(body) == expect, path


def test_overview_numbers(app):
    season, _, teams = _setup(app, n_teams=2)
    sid = season["id"]
    # Registered walkover -> finalized match with no finance entry yet.
    app.extensions["scorer_service"].upsert_match_registry_entry(
        sid, "M1", between="Thunder vs Blaze", walkover=True,
        walkover_winner_team_id=teams[0]["id"],
        team_a_global_id=teams[0]["id"], team_b_global_id=teams[1]["id"])
    client = _login(app)
    body = client.get(f"/admin?season={sid}").data.decode("utf-8")
    # Phase 4 restyle: overview cards use stat tiles (label + value divs).
    assert '<div class="stat-label">Teams</div><div class="stat-value">2</div>' in body
    assert '<div class="stat-label">Registry</div><div class="stat-value">1</div>' in body
    assert '<div class="stat-label">Finalized</div><div class="stat-value">1</div>' in body
    assert '<div class="stat-label">Pending fin.</div><div class="stat-value">1</div>' in body
    # wallet_total = the two managers' funding (2 x 10k; no tier purses in S2).
    assert '<div class="stat-label">Team wallets</div><div class="stat-value">20000</div>' in body
    assert "Wagers" in body


def test_overview_wager_card(app, wager):
    season, players, _ = _setup(app, n_teams=2)
    gp = players[0]["global_player_id"]
    user = _linked_user(app, "alice", gp)
    wager.create_wager(user, "Royales win Match 1", "", "Yes", "No", "Yes", 100)
    client = _login(app)
    body = client.get(f"/admin?season={season['id']}").data.decode("utf-8")
    assert '<div class="stat-label">Open</div><div class="stat-value">1</div>' in body


def test_auction_post_redirects_to_auction(app):
    season, _, _ = _setup(app, n_teams=2)
    client = _login(app)
    r = client.post(f"/admin/season/{season['id']}/phase",
                    data={"phase": "phase_a_platinum"})
    assert r.status_code == 302
    assert "/admin/auction" in r.headers.get("Location", "")


def test_overview_empty_state(app):
    client = _login(app)
    body = client.get("/admin").data.decode("utf-8")
    assert "Create your first season" in body


def test_public_pages_have_no_tabs(app):
    _setup(app, n_teams=2)
    client = _login(app)
    for path in ("/", "/matches", "/finances"):
        body = client.get(path).data.decode("utf-8")
        assert "admin-tabs" not in body, path
