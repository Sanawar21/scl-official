"""Deleting a season: cascade-removes everything scoped to it.

Vault money is released back to liquid (never destroyed), global players/teams
persist for future seasons, and manager user accounts are unassigned.
"""
import pytest

from tests.conftest import _setup


@pytest.fixture()
def auction(app):
    return app.extensions["auction_service"]


def _fill_season(app, auction, sid):
    """Add data a season accumulates: bids, a wager, a finalized match, vault lock."""
    bank = app.extensions["bank_service"]
    wager = app.extensions["wager_service"]
    scorer = app.extensions["scorer_service"]
    db = app.extensions["db"]

    state = auction.get_state(sid)
    teams = [t for t in state["teams"]]
    players = [p for p in state["players"] if p["status"] == "unsold"]
    # One bid on the first player by the first team (needs a live lot).
    auction.set_phase(sid, "phase_a_platinum")
    auction.nominate_next(sid)
    auction.place_bid(sid, teams[0]["id"], players[0]["base_price"], actor="manager")
    # Lock some money into the vault for the first manager.
    acct = bank.get_or_create_account("player", teams[0]["manager_player_id"])
    bank.lock_to_vault(acct["id"], sid, 1000, reinvest=True)
    # A wager tied to this season (initiator must be a linked player).
    auth = app.extensions["auth_service"]
    u = auth.signup("wagerer", "password1", "Wagerer")
    u = auth.link_user_to_player(u["id"], teams[0]["manager_player_id"])
    wager.create_wager(u, "Will it rain?", "test", "Yes", "No", "Yes", 100,
                       season_id=sid)
    # A finalized match in the registry + stats.
    scorer.upsert_match_registry_entry(
        sid, "M1", match_number="Match 1", between="Thunder vs Blaze",
        venue="Ground", match_date="2026-08-01",
        team_a_global_id=teams[0]["global_team_id"], team_b_global_id=teams[1]["global_team_id"])
    return None


def test_delete_removes_all_season_scoped_rows(app, auction):
    season, players, teams = _setup(app, n_teams=2)
    sid = season["id"]
    _fill_season(app, auction, sid)

    result = auction.delete_season(sid)
    assert result["ok"] is True
    assert auction.get_season(sid) is None
    assert sid not in {s["id"] for s in auction.list_seasons()}

    db = app.extensions["db"]
    with db.read() as conn:
        for tbl, col in [("players", "season_id"), ("teams", "season_id"),
                         ("bids", "season_id"), ("trade_requests", "season_id"),
                         ("transfers", "season_id"), ("auction_action_log", "season_id"),
                         ("auction_meta", "season_id"), ("season_snapshots", "season_id"),
                         ("rulesets", "season_id"), ("wagers", "season_id"),
                         ("match_registry", "season_id"), ("match_stats", "season_id"),
                         ("match_team_stats", "season_id"), ("match_player_stats", "season_id"),
                         ("season_finance_entries", "season_id"),
                         ("vault_positions", "season_id")]:
            n = conn.execute(f"SELECT COUNT(*) FROM {tbl} WHERE {col} = ?", (sid,)).fetchone()[0]
            assert n == 0, f"{tbl} still has {n} rows for {sid}"


def test_delete_releases_vault_capital_to_liquid(app, auction):
    season, players, teams = _setup(app, n_teams=1)
    sid = season["id"]
    bank = app.extensions["bank_service"]
    acct = bank.get_or_create_account("player", teams[0]["manager_player_id"])
    bank.set_auto(acct["id"], False)
    bank.adjust(acct["id"], 10000, "funding", tx_type="funding")
    bank.lock_to_vault(acct["id"], sid, 4000, reinvest=True)

    before = bank.get_account(acct["id"])
    assert int(before["locked_capital"]) == 4000

    auction.delete_season(sid)
    after = bank.get_account(acct["id"])
    # Locked capital moved back to liquid; nothing destroyed.
    assert int(after["locked_capital"]) == 0
    assert int(after["liquid_cash"]) == int(before["liquid_cash"]) + 4000


def test_delete_keeps_global_players_and_teams(app, auction):
    season, players, teams = _setup(app, n_teams=2)
    sid = season["id"]
    gp_ids = {p["global_player_id"] for p in players}
    gt_ids = {t["global_team_id"] for t in teams}

    auction.delete_season(sid)

    db = app.extensions["db"]
    with db.read() as conn:
        remaining_gp = {r["id"] for r in conn.execute("SELECT id FROM global_players").fetchall()}
        remaining_gt = {r["id"] for r in conn.execute("SELECT id FROM global_teams").fetchall()}
    assert gp_ids <= remaining_gp
    assert gt_ids <= remaining_gt
    # The manager link on the persistent team survives too.
    for t in teams:
        gt = auction.get_global_team(t["global_team_id"])
        assert gt["manager_player_id"] == t["manager_player_id"]


def test_delete_keeps_manager_link_via_global_team(app, auction):
    """Manager status is derived (global_teams), so deleting a season never
    strips it — nothing is stored on the user row to reset."""
    season, players, teams = _setup(app, n_teams=1)
    sid = season["id"]
    auth = app.extensions["auth_service"]
    u = auth.signup("mgr1", "password1", "Manager One")
    u = auth.link_user_to_player(u["id"], teams[0]["manager_player_id"])
    # Derived: linked to a player who manages a team -> manager, no assign step.
    view = auth.user_view(auth.get_user(u["id"]))
    assert view["role"] == "manager"
    assert view["team_id"] == teams[0]["id"]

    auction.delete_season(sid)

    view = auth.user_view(auth.get_user(u["id"]))
    # Still a manager (the global team persists), just no season participation.
    assert view["role"] == "manager"
    assert view["team_id"] is None
    assert view["season_id"] is None


def test_delete_unknown_season_raises(app, auction):
    with pytest.raises(ValueError):
        auction.delete_season("does-not-exist")
