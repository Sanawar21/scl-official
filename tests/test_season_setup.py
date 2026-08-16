"""Season setup: picking managers + auction players from the global pool."""
import pytest

from tests.conftest import _setup


@pytest.fixture()
def auction(app):
    return app.extensions["auction_service"]


def _fresh_season(auction, name="Setup Season"):
    return auction.create_season(name)


# ----------------------------------------------------------------------
# season_setup_context
# ----------------------------------------------------------------------
def test_setup_context_lists_all_global_players_and_teams(app, auction):
    # Previous season with players + teams populates the global pool.
    season, players, teams = _setup(app, n_teams=2)
    sid = _fresh_season(auction)["id"]
    ctx = auction.season_setup_context(sid)
    assert ctx is not None
    assert len(ctx["players"]) >= 6  # global pool from the previous season
    assert len(ctx["teams"]) >= 2
    # Nothing is in the fresh season yet.
    assert all(not p["in_auction"] for p in ctx["players"])
    assert all(not p["is_manager"] for p in ctx["players"])
    assert all(not t["in_season"] for t in ctx["teams"])


def test_setup_context_shows_existing_team_owner(app, auction):
    season, players, teams = _setup(app, n_teams=2)
    # Owner of Thunder should be flagged with their team.
    gt = auction.get_global_team(teams[0]["global_team_id"])
    ctx = auction.season_setup_context(_fresh_season(auction)["id"])
    p = next(p for p in ctx["players"] if p["id"] == gt["manager_player_id"])
    assert p["team"] is not None
    assert p["team"]["id"] == gt["id"]


# ----------------------------------------------------------------------
# sync_season_setup
# ----------------------------------------------------------------------
def test_sync_adds_auction_players_and_team_for_manager(app, auction):
    season, players, teams = _setup(app, n_teams=2)
    sid = _fresh_season(auction)["id"]
    ctx = auction.season_setup_context(sid)
    gps = ctx["players"]
    # The first two players already own the season's teams; pick a teamless
    # player for the "create a new team" case.
    teamless = next(p for p in gps if not p["team"])

    auction.sync_season_setup(
        sid,
        auction_player_ids=[gps[0]["id"], gps[1]["id"], teamless["id"]],
        manager_team_names={teamless["id"]: "New Squad"},
    )
    ctx2 = auction.season_setup_context(sid)
    # teamless is a manager -> own roster slot, not an auction lot.
    assert sum(1 for p in ctx2["players"] if p["in_auction"]) == 2
    mgr = next(p for p in ctx2["players"] if p["id"] == teamless["id"])
    assert mgr["is_manager"] is True
    assert mgr["in_auction"] is False
    assert mgr["season_team_name"] == "New Squad"
    assert any(t["name"] == "New Squad" for t in ctx2["teams"] if t["in_season"])


def test_sync_reuses_existing_team_automatically(app, auction):
    season, players, teams = _setup(app, n_teams=2)
    # The previous season's team manager keeps their team automatically.
    gt = auction.get_global_team(teams[0]["global_team_id"])
    sid = _fresh_season(auction)["id"]
    auction.sync_season_setup(
        sid,
        auction_player_ids=[],
        manager_team_names={gt["manager_player_id"]: "ignored-name"},
    )
    ctx = auction.season_setup_context(sid)
    mgr = next(p for p in ctx["players"] if p["id"] == gt["manager_player_id"])
    assert mgr["is_manager"] is True
    assert mgr["season_team_name"] == gt["name"]  # existing name wins
    assert any(t["in_season"] and t["name"] == gt["name"] for t in ctx["teams"])


def test_sync_deselect_removes_players_and_teams(app, auction):
    season, players, teams = _setup(app, n_teams=2)
    sid = _fresh_season(auction)["id"]
    ctx = auction.season_setup_context(sid)
    gps = ctx["players"]
    teamless = next(p for p in gps if not p["team"])
    auction.sync_season_setup(
        sid, auction_player_ids=[teamless["id"]],
        manager_team_names={teamless["id"]: "Squad A"})
    # Now unselect everything.
    auction.sync_season_setup(sid, auction_player_ids=[], manager_team_names={})
    ctx2 = auction.season_setup_context(sid)
    assert all(not p["in_auction"] for p in ctx2["players"])
    assert all(not t["in_season"] for t in ctx2["teams"])


def test_sync_requires_team_name_for_teamless_manager(app, auction):
    season, players, teams = _setup(app, n_teams=2)
    sid = _fresh_season(auction)["id"]
    ctx = auction.season_setup_context(sid)
    gps = ctx["players"]
    # Pick a player that owns no team yet.
    teamless = next(p for p in gps if not p["team"])
    with pytest.raises(ValueError):
        auction.sync_season_setup(sid, auction_player_ids=[],
                                  manager_team_names={teamless["id"]: ""})


def test_managers_never_enter_auction_pool(app, auction):
    """Managers are their team's own roster slot, not auction lots — even if
    the form sends them in both lists, they stay out of the auction pool."""
    season, players, teams = _setup(app, n_teams=2)
    sid = _fresh_season(auction)["id"]
    ctx = auction.season_setup_context(sid)
    gps = ctx["players"]
    teamless = next(p for p in gps if not p["team"])
    # Deliberately send the manager in BOTH lists.
    auction.sync_season_setup(
        sid,
        auction_player_ids=[gps[0]["id"], teamless["id"]],
        manager_team_names={gps[0]["id"]: "Own Squad"},
    )
    ctx2 = auction.season_setup_context(sid)
    mgr = next(p for p in ctx2["players"] if p["id"] == gps[0]["id"])
    assert mgr["is_manager"] is True
    assert mgr["in_auction"] is False  # excluded from the auction pool
    assert sum(1 for p in ctx2["players"] if p["in_auction"]) == 1  # only teamless


def test_sync_locked_after_setup(app, auction):
    season, players, teams = _setup(app, n_teams=2)
    sid = _fresh_season(auction)["id"]
    with app.extensions["db"].write() as conn:
        conn.execute("UPDATE seasons SET status = 'running' WHERE id = ?", (sid,))
    with pytest.raises(ValueError):
        auction.sync_season_setup(sid, auction_player_ids=[], manager_team_names={})


# ----------------------------------------------------------------------
# reassign_team_manager
# ----------------------------------------------------------------------
def test_reassign_manager(app, auction):
    season, players, teams = _setup(app, n_teams=2)
    sid = _fresh_season(auction)["id"]
    ctx = auction.season_setup_context(sid)
    gps = ctx["players"]
    teamless = [p for p in gps if not p["team"]]
    # Two managers, two teams.
    auction.sync_season_setup(
        sid, auction_player_ids=[],
        manager_team_names={teamless[0]["id"]: "Alpha",
                            teamless[1]["id"]: "Beta"})
    st = auction.season_setup_context(sid)["season_teams"]
    alpha = next(t for t in st if t["name"] == "Alpha")
    # Reassign Alpha to a third player who manages nothing yet.
    spare = next(p for p in gps if not p["team"]
                 and p["id"] not in (teamless[0]["id"], teamless[1]["id"]))
    t = auction.reassign_team_manager(sid, alpha["id"], spare["id"])
    assert t["manager_player_id"] == spare["id"]
    # A player who already manages a team cannot take another.
    with pytest.raises(ValueError):
        auction.reassign_team_manager(sid, alpha["id"], teamless[1]["id"])


# ----------------------------------------------------------------------
# routes
# ----------------------------------------------------------------------
def test_setup_route_and_save(app, auction):
    season, players, teams = _setup(app, n_teams=2)
    sid = _fresh_season(auction)["id"]
    c = app.test_client()
    c.post("/auth/login", data={"username": "admin", "password": "admin123"})
    # Setup page renders.
    r = c.get(f"/admin/season/{sid}/setup")
    assert r.status_code == 200
    body = r.data.decode()
    assert "Auction players" in body
    assert "Managers" in body
    ctx = auction.season_setup_context(sid)
    gps = ctx["players"]
    teamless = next(p for p in gps if not p["team"])
    # Save via the form (checkbox lists + team name field). teamless is the
    # manager, so only gps[0] enters the auction pool.
    r = c.post(f"/admin/season/{sid}/setup/save", data={
        "auction_players": [gps[0]["id"], teamless["id"]],
        "managers": [teamless["id"]],
        f"team_name_{teamless['id']}": "Form Squad",
    })
    assert r.status_code == 302
    ctx2 = auction.season_setup_context(sid)
    assert sum(1 for p in ctx2["players"] if p["in_auction"]) == 1
    assert any(t["name"] == "Form Squad" for t in ctx2["teams"] if t["in_season"])
    # Change manager route.
    st = ctx2["season_teams"]
    r = c.post(f"/admin/season/{sid}/team/{st[0]['id']}/manager",
               data={"manager_player_id": gps[0]["id"]})
    assert r.status_code == 302
    assert auction._get_team(sid, st[0]["id"])["manager_player_id"] == gps[0]["id"]
    # Setup tab appears in the admin nav.
    body = c.get(f"/admin/season/{sid}/setup").data.decode()
    assert "/season/{}/setup".format(sid) in body


def test_season_create_redirects_to_setup(app, auction):
    _setup(app, n_teams=2)
    c = app.test_client()
    c.post("/auth/login", data={"username": "admin", "password": "admin123"})
    r = c.post("/admin/season/create", data={"name": "Brand New Season"})
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/admin/season/brand-new-season/setup")
