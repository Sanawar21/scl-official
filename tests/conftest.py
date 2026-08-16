import pytest

from app import create_app


@pytest.fixture()
def app(tmp_path):
    db_path = str(tmp_path / "test.db")
    app = create_app({"SECRET_KEY": "test", "DB_PATH": db_path,
                      "ADMIN_USERNAME": "admin", "ADMIN_PASSWORD": "admin123"})
    return app


@pytest.fixture()
def svc(app):
    return app.extensions["auction_service"]


def _setup(app, n_teams=4, players=None, phase_order=None):
    """Create a season with players and teams (managers from the player pool)."""
    svc = app.extensions["auction_service"]
    season = svc.create_season("Test Season", ruleset_overrides={"phase_order": phase_order} if phase_order else None)
    sid = season["id"]

    default_players = players or [
        ("Alice", "platinum", "BATTER"),
        ("Bob", "gold", "ALL_ROUNDER"),
        ("Cara", "silver", "BOWLER"),
        ("Dave", "platinum", "ALL_ROUNDER"),
        ("Eve", "gold", "BATTER"),
        ("Fay", "silver", "ALL_ROUNDER"),
        ("Gil", "platinum", "BOWLER"),
        ("Hana", "gold", "ALL_ROUNDER"),
        ("Ivo", "silver", "BATTER"),
        ("Jay", "gold", "BOWLER"),
        ("Kit", "silver", "ALL_ROUNDER"),
        ("Lia", "silver", "BATTER"),
    ]
    player_rows = []
    for name, tier, spec in default_players:
        player_rows.append(svc.add_player(sid, name, tier, spec))

    team_names = ["Thunder", "Blaze", "Falcon", "Storm", "Viper", "Osprey"][:n_teams]
    teams = []
    for i, tname in enumerate(team_names):
        manager_gp = player_rows[i]["global_player_id"]
        teams.append(svc.create_team(sid, tname, manager_gp))
    # S2 economy: no tier purses — fund each manager's wallet directly.
    bank = app.extensions["bank_service"]
    for i, tname in enumerate(team_names):
        manager_gp = player_rows[i]["global_player_id"]
        acct = bank.get_or_create_account("player", manager_gp)
        bank.adjust(acct["id"], 10000, "test funding (10k)", tx_type="funding")
    # Refresh the returned team dicts so `wallet` reflects the funding.
    teams = [svc._get_team(sid, t["id"]) for t in teams]
    return season, player_rows, teams


@pytest.fixture()
def season(app):
    season, _, _ = _setup(app)
    return season
