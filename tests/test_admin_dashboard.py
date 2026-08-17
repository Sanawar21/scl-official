"""Admin dashboard consolidation tests: shared tab shell + overview page."""
import re

import pytest
from werkzeug.security import check_password_hash

from tests.conftest import _setup
from tests.test_wager import _linked_user


@pytest.fixture()
def auth(app):
    return app.extensions["auth_service"]


def test_seed_admin_syncs_stale_password(app, auth):
    """An existing admin with an outdated password is updated to the configured
    credentials on boot, so .env stays authoritative (the seed used to create
    the admin only if missing, silently ignoring the configured password)."""
    # Create the admin with a stale password (as if from an old default).
    auth.seed_admin_if_missing("admin", "oldpass")
    # Re-seed with the configured password: it must be updated, not skipped.
    auth.seed_admin_if_missing("admin", "admin123")
    user = auth.login("admin", "admin123")
    assert user is not None and user["role"] == "admin"
    assert auth.login("admin", "oldpass") is None
    # Username change is synced too.
    auth.seed_admin_if_missing("root", "admin123")
    assert auth.login("root", "admin123") is not None


def test_seed_admin_syncs_password_in_db(app, auth):
    """The hash stored in the DB matches the configured password after boot."""
    auth.seed_admin_if_missing("admin", "freshpw")
    with app.extensions["db"].read() as conn:
        row = conn.execute("SELECT password_hash FROM users WHERE role='admin'").fetchone()
    assert check_password_hash(row["password_hash"], "freshpw")
    assert not check_password_hash(row["password_hash"], "admin123")


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


def test_finances_fund_all_route(app):
    season, players, _ = _setup(app, n_teams=2)
    bank = app.extensions["bank_service"]
    with app.extensions["db"].read() as conn:
        n_players = conn.execute("SELECT COUNT(*) FROM global_players").fetchone()[0]
    client = _login(app)
    r = client.post(f"/admin/finances/fund-all?season={season['id']}",
                    data={"amount": "10000"})
    assert r.status_code == 302
    # Everyone (incl. the two managers) got funded once.
    with app.extensions["db"].read() as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM bank_transactions t JOIN bank_accounts a ON a.id = t.account_id "
            "WHERE a.owner_type='player' AND t.type='season_funding'").fetchone()[0]
    assert n == n_players
    # The button is visible on the finances page.
    body = client.get(f"/admin/finances?season={season['id']}").data.decode()
    assert "fund all players" in body.lower()


def test_finances_levy_route(app):
    season, _, teams = _setup(app, n_teams=2)
    finance = app.extensions["finance_service"]
    with app.extensions["db"].write() as conn:
        conn.execute("UPDATE teams SET spent = 2000 WHERE id = ?", (teams[0]["id"],))
    client = _login(app)
    r = client.post(f"/admin/finances/levy?season={season['id']}", data={})
    assert r.status_code == 302
    entries = finance.list_finance_entries(season["id"])
    assert any(e["type"] == "squad_levy" for e in entries)
    # Second run is a no-op.
    r = client.post(f"/admin/finances/levy?season={season['id']}", data={})
    assert r.status_code == 302
    assert len([e for e in finance.list_finance_entries(season["id"]) if e["type"] == "squad_levy"]) == 1


def test_admin_grant_lands_liquid_and_sweeps_at_release(app):
    """Bank adjust is the only deposit path; grants land in liquid cash for
    everyone (auto accounts are swept into the vault at a yield release)."""
    season, players, _ = _setup(app, n_teams=2)
    bank = app.extensions["bank_service"]
    gp = players[0]["global_player_id"]
    acct = bank.get_or_create_account("player", gp)
    bank.set_auto(acct["id"], True)
    liquid_before = bank.account_for_owner("player", gp)["liquid_cash"]
    client = _login(app)
    r = client.post(f"/admin/bank/adjust?season={season['id']}",
                    data={"account_id": f"player:{gp}", "amount": "500",
                          "comment": "credit saved"})
    assert r.status_code == 302
    fresh = bank.account_for_owner("player", gp)
    # The grant lands liquid (vault sweep happens at the yield release).
    assert fresh["liquid_cash"] == liquid_before + 500
    assert fresh["locked_capital"] == 0

    # Manual accounts keep plain liquid credit.
    gp2 = players[2]["global_player_id"]  # a non-manager player
    acct2 = bank.get_or_create_account("player", gp2)
    bank.set_auto(acct2["id"], False)
    r = client.post(f"/admin/bank/adjust?season={season['id']}",
                    data={"account_id": f"player:{gp2}", "amount": "300",
                          "comment": "manual grant"})
    assert r.status_code == 302
    fresh2 = bank.account_for_owner("player", gp2)
    assert fresh2["liquid_cash"] == 300
    assert fresh2["locked_capital"] == 0

    # Negative amounts (fines) always come from liquid.
    r = client.post(f"/admin/bank/adjust?season={season['id']}",
                    data={"account_id": f"player:{gp2}", "amount": "-100",
                          "comment": "fine"})
    assert r.status_code == 302
    assert bank.account_for_owner("player", gp2)["liquid_cash"] == 200


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
