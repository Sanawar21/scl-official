"""Manager status is DERIVED from the player→team links, never stored.

users.role only distinguishes admin from player; users.team_id is legacy and
ignored. An account becomes a manager the moment it is linked to a player who
manages a team (global_teams.manager_player_id / per-season teams rows).
"""
import pytest

from tests.conftest import _setup


@pytest.fixture()
def auction(app):
    return app.extensions["auction_service"]


@pytest.fixture()
def auth(app):
    return app.extensions["auth_service"]


def test_linked_manager_is_derived_without_assign_step(app, auth, auction):
    season, players, teams = _setup(app, n_teams=1)
    u = auth.signup("mgr1", "password1", "Manager One")
    u = auth.link_user_to_player(u["id"], teams[0]["manager_player_id"])

    view = auth.user_view(auth.get_user(u["id"]))
    assert view["role"] == "manager"
    assert view["is_manager"] is True
    assert view["team_id"] == teams[0]["id"]
    assert view["season_id"] == season["id"]
    assert view["team_name"] == teams[0]["name"]


def test_plain_player_stays_player(app, auth, auction):
    season, players, teams = _setup(app, n_teams=1)
    # A player who does NOT manage a team.
    non_manager = next(p for p in players if p["global_player_id"] != teams[0]["manager_player_id"])
    u = auth.signup("ply1", "password1", "Player One")
    u = auth.link_user_to_player(u["id"], non_manager["global_player_id"])

    view = auth.user_view(auth.get_user(u["id"]))
    assert view["role"] == "player"
    assert view["is_manager"] is False
    assert view["team_id"] is None


def test_unlinked_account_stays_player(app, auth, auction):
    u = auth.signup("nobody", "password1", "Nobody")
    view = auth.user_view(auth.get_user(u["id"]))
    assert view["role"] == "player"
    assert view["is_manager"] is False


def test_admin_role_is_preserved(app, auth, auction):
    u = auth.get_by_username("admin")
    view = auth.user_view(u)
    assert view["role"] == "admin"
    assert view["is_manager"] is False


def test_manager_tracks_latest_season(app, auth, auction):
    """Same player manages a team in two seasons -> resolves to the newest."""
    season1, players, teams1 = _setup(app, n_teams=1)
    mgr_gp = teams1[0]["manager_player_id"]
    season2 = auction.create_season("Season Two")
    teams2 = [auction.create_team(season2["id"], teams1[0]["name"], mgr_gp)]
    u = auth.signup("mgr2", "password1", "Manager Two")
    u = auth.link_user_to_player(u["id"], mgr_gp)

    view = auth.user_view(auth.get_user(u["id"]))
    assert view["role"] == "manager"
    assert view["season_id"] == season2["id"]
    assert view["team_id"] == teams2[0]["id"]


def test_team_deleted_from_season_keeps_manager_status(app, auth, auction):
    season, players, teams = _setup(app, n_teams=1)
    u = auth.signup("mgr3", "password1", "Manager Three")
    u = auth.link_user_to_player(u["id"], teams[0]["manager_player_id"])
    # The manager stays a manager after the season's team row is removed,
    # because the global team still names them.
    auction.delete_team(season["id"], teams[0]["id"])

    view = auth.user_view(auth.get_user(u["id"]))
    assert view["role"] == "manager"
    assert view["team_id"] is None  # no per-season row, but still a manager
