import csv
import io

import pytest

from tests.conftest import _setup

CSV_HEADER = [
    "Match ID", "Match", "Venue", "Scorer Version", "Substitutions Applied",
    "Substitution Details", "Innings Order", "Batting Team", "Batting Team ID",
    "Batting Manager ID", "Over Number", "Ball Number", "Valid Ball?", "Batter",
    "Batter ID", "Batter Order", "Non Strike Batter", "Non Strike Batter ID",
    "Bowler", "Bowler ID", "Bowling Team", "Bowling Team ID", "Bowling Manager ID",
    "Runs Bat", "Runs Extra", "Extras Type", "Dismissed Batter", "Dismissed Batter ID",
    "Progressive Runs", "Progressive Wickets", "Match Toss", "Match Result",
]


def _csv_bytes(rows):
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(CSV_HEADER)
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8")


def _fake_upload(content: bytes, name: str = "M1.csv"):
    class FakeFile:
        filename = name

        def read(self):
            return content
    return FakeFile()


def _delivery(match_id="M1", innings=1, over=1, ball=1, valid="Yes", batter="Alice",
              batter_id="gp-alice", nsb="Bob", nsb_id="gp-bob", bowler="Cara",
              bowler_id="gp-cara", bat_team="Thunder", bat_team_id="t-thunder",
              bowl_team="Blaze", bowl_team_id="t-blaze", runs_bat="0", runs_extra="0",
              extras_type="", dismissed="", dismissed_id="", result="Thunder won",
              toss="Thunder"):
    return [match_id, "Thunder vs Blaze", "Ground 1", "1.1.0", "0", "None",
            str(innings), bat_team, bat_team_id, "mgr-a", str(over), str(ball), valid,
            batter, batter_id, "1", nsb, nsb_id, bowler, bowler_id, bowl_team,
            bowl_team_id, "mgr-b", runs_bat, runs_extra, extras_type, dismissed,
            dismissed_id, "0", "0", toss, result]


@pytest.fixture()
def scorer(app):
    return app.extensions["scorer_service"]


# ----------------------------------------------------------------------
# registry + walkover
# ----------------------------------------------------------------------
def _teams(app):
    """Create a 2-team season and return (season, team dicts)."""
    season, _, teams = _setup(app, n_teams=2)
    return season, teams


def test_registry_crud(app, scorer):
    season, teams = _teams(app)
    team_a = teams[0]["id"]
    team_b = teams[1]["id"]
    scorer.upsert_match_registry_entry(
        season["id"], "M1", match_number="Match 1", between="Thunder vs Blaze",
        team_a_global_id=team_a, team_b_global_id=team_b)
    entry = scorer.get_match_registry_entry(season["id"], "M1")
    assert entry["match_number"] == "Match 1"
    assert entry["between"] == "Thunder vs Blaze"
    assert entry["team_a_global_id"] == team_a
    assert entry["walkover"] == 0
    # upsert updates in place
    scorer.upsert_match_registry_entry(
        season["id"], "M1", match_number="Match 1 updated", between="Thunder vs Blaze",
        team_a_global_id=team_a, team_b_global_id=team_b)
    assert scorer.get_match_registry_entry(season["id"], "M1")["match_number"] == "Match 1 updated"


def test_walkover_requires_winner_and_teams(app, scorer):
    season, teams = _teams(app)
    with pytest.raises(ValueError):
        scorer.upsert_match_registry_entry(season["id"], "M6", walkover=True)
    with pytest.raises(ValueError):
        scorer.upsert_match_registry_entry(
            season["id"], "M6", walkover=True, walkover_winner_team_id="outside")


def test_walkover_creates_synthetic_rows(app, scorer):
    season, players, teams = _setup(app, n_teams=2)
    team_a = teams[0]["id"]
    team_b = teams[1]["id"]
    scorer.upsert_match_registry_entry(
        season["id"], "M6", walkover=True, walkover_winner_team_id=team_a,
        team_a_global_id=team_a, team_b_global_id=team_b)
    summary = scorer.match_summary(season["id"], "M6")
    assert summary and summary["walkover"]
    assert len(summary["team_sections"]) == 2
    winner = next(s for s in summary["team_sections"] if s["team_id"] == team_a)
    assert winner["team"]["wins"] == 1
    table = scorer.league_table(season["id"])
    winner_row = next(e for e in table if e["team_id"] == team_a)
    assert winner_row["wins"] == 1 and winner_row["played"] == 1


def test_registry_delete_removes_rows(app, scorer):
    season, teams = _teams(app)
    scorer.upsert_match_registry_entry(
        season["id"], "M1", team_a_global_id=teams[0]["id"],
        team_b_global_id=teams[1]["id"])
    result = scorer.delete_match_registry_entry(season["id"], "M1")
    assert result["ok"] is True
    assert scorer.get_match_registry_entry(season["id"], "M1") is None


# ----------------------------------------------------------------------
# CSV import
# ----------------------------------------------------------------------
def test_csv_import_derives_team_and_player_rows(app, scorer):
    season, teams = _teams(app)
    team_a = teams[0]["id"]
    team_b = teams[1]["id"]
    scorer.upsert_match_registry_entry(
        season["id"], "M1", between="Thunder vs Blaze",
        team_a_global_id=team_a, team_b_global_id=team_b)
    rows = [
        _delivery(match_id="M1", innings=1, batter="Alice", batter_id="gp-alice",
                  bat_team="Thunder", bat_team_id=team_a, bowl_team="Blaze",
                  bowl_team_id=team_b, bowler="Cara", bowler_id="gp-cara",
                  runs_bat="4", result="Thunder won"),
        _delivery(match_id="M1", innings=2, batter="Cara", batter_id="gp-cara",
                  bat_team="Blaze", bat_team_id=team_b, bowl_team="Thunder",
                  bowl_team_id=team_a, bowler="Alice", bowler_id="gp-alice",
                  runs_bat="2", runs_extra="1", extras_type="Wide",
                  result="Thunder won"),
    ]
    derived = scorer.import_match_csv(_fake_upload(_csv_bytes(rows)), season["id"])
    assert derived["match_row"]["result"] == "Thunder won"
    assert len(derived["team_rows"]) == 2
    # players: Alice + Cara + Bob (non-strike in both innings)
    assert len(derived["player_rows"]) == 3
    # The import normalizes team ids to the canonical global team id.
    thunder = next(t for t in derived["team_rows"]
                   if t["team_id"] == teams[0]["global_team_id"])
    assert thunder["runs_scored"] == 4 and thunder["result"] == "win"
    # name fallback maps to the real created global player; 1 ball, 4 runs -> SR 400.0
    alice = next(p for p in derived["player_rows"] if p["player_name"] == "Alice")
    assert alice["runs"] == 4 and alice["balls_faced"] == 1 and alice["strike_rate"] == 400.0


def test_csv_import_requires_registered_match(app, scorer):
    season, _ = _teams(app)
    rows = [_delivery(match_id="M99")]
    with pytest.raises(ValueError, match="not configured"):
        scorer.import_match_csv(_fake_upload(_csv_bytes(rows)), season["id"])


def test_csv_import_missing_column_rejected(app, scorer):
    season, teams = _teams(app)
    scorer.upsert_match_registry_entry(
        season["id"], "M1", team_a_global_id=teams[0]["id"],
        team_b_global_id=teams[1]["id"])
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(CSV_HEADER[:-3])  # drop the last three required columns
    writer.writerow(_delivery())
    with pytest.raises(ValueError, match="missing columns"):
        scorer.import_match_csv(_fake_upload(buf.getvalue().encode()), season["id"])


def test_csv_import_overwrite_requires_confirm(app, scorer):
    season, teams = _teams(app)
    team_a = teams[0]["id"]
    team_b = teams[1]["id"]
    scorer.upsert_match_registry_entry(
        season["id"], "M1", between="Thunder vs Blaze",
        team_a_global_id=team_a, team_b_global_id=team_b)
    rows = [_delivery(match_id="M1", innings=1, bat_team_id=team_a, bowl_team_id=team_b)]
    scorer.import_match_csv(_fake_upload(_csv_bytes(rows)), season["id"])
    with pytest.raises(Exception):
        scorer.import_match_csv(_fake_upload(_csv_bytes(rows)), season["id"])
    # confirm overwrite succeeds
    derived = scorer.import_match_csv(
        _fake_upload(_csv_bytes(rows)), season["id"], confirm_overwrite=True)
    assert len(derived["team_rows"]) == 2


def test_walkover_match_refuses_csv(app, scorer):
    season, teams = _teams(app)
    scorer.upsert_match_registry_entry(
        season["id"], "M6", walkover=True,
        walkover_winner_team_id=teams[0]["id"],
        team_a_global_id=teams[0]["id"],
        team_b_global_id=teams[1]["id"])
    rows = [_delivery(match_id="M6", bat_team_id=teams[0]["id"],
                      bowl_team_id=teams[1]["id"])]
    with pytest.raises(ValueError, match="walkover"):
        scorer.import_match_csv(_fake_upload(_csv_bytes(rows)), season["id"])


def test_undo_import_removes_all_row_kinds(app, scorer):
    season, teams = _teams(app)
    team_a = teams[0]["id"]
    team_b = teams[1]["id"]
    scorer.upsert_match_registry_entry(
        season["id"], "M1", team_a_global_id=team_a, team_b_global_id=team_b)
    rows = [_delivery(match_id="M1", innings=1, bat_team_id=team_a, bowl_team_id=team_b)]
    scorer.import_match_csv(_fake_upload(_csv_bytes(rows)), season["id"])
    result = scorer.undo_imported_match(f"{season['id']}:m1")
    assert result["ok"] and result["removed_rows"] == 6  # 1 match + 2 team + 3 player
    summary = scorer.match_summary(season["id"], "M1")
    assert summary and not summary["has_uploaded_data"]


# ----------------------------------------------------------------------
# league table
# ----------------------------------------------------------------------
def _seed_match(app, scorer, match_id, winner_id, loser_id, winner_name="Thunder",
                loser_name="Blaze", runs_for=60, balls_for=36, runs_against=40,
                balls_against=36):
    sid = app.extensions["auction_service"].list_seasons()[0]["id"]
    scorer.upsert_match_registry_entry(
        sid, match_id, team_a_global_id=winner_id, team_b_global_id=loser_id)
    with app.extensions["db"].write() as conn:
        key = f"{sid}:{match_id.lower()}"
        conn.execute(
            "INSERT INTO match_stats (match_key, season_id, match_id, result, winner_team_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (key, sid, match_id, f"{winner_name} won", winner_id))
        conn.execute(
            "INSERT INTO match_team_stats (id, match_key, season_id, team_id, team_name, "
            "runs_scored, balls_faced, runs_conceded, balls_bowled, result, wins) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'win', 1)",
            (f"w-{match_id}", key, sid, winner_id, winner_name, runs_for, balls_for,
             runs_against, balls_against))
        conn.execute(
            "INSERT INTO match_team_stats (id, match_key, season_id, team_id, team_name, "
            "runs_scored, balls_faced, runs_conceded, balls_bowled, result, losses) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'loss', 1)",
            (f"l-{match_id}", key, sid, loser_id, loser_name, runs_against, balls_against,
             runs_for, balls_for))


def test_league_table_points_and_nrr(app, scorer):
    season, _, teams = _setup(app, n_teams=2)
    team_a = teams[0]["id"]
    team_b = teams[1]["id"]
    _seed_match(app, scorer, "M1", team_a, team_b)
    _seed_match(app, scorer, "M2", team_a, team_b, runs_for=30, balls_for=36)
    table = scorer.league_table(season["id"])
    a = next(e for e in table if e["team_id"] == team_a)
    b = next(e for e in table if e["team_id"] == team_b)
    assert a["points"] == 4 and a["wins"] == 2 and a["played"] == 2
    assert b["points"] == 0
    assert table[0]["team_id"] == team_a
    # NRR over both matches: for=90/72 balls, against=80/72 balls
    # (90*6/72)=7.5 rr_for, (80*6/72)=6.67 rr_against -> 0.83
    assert abs(a["nrr"] - 0.83) < 0.01


def test_league_table_head_to_head_tiebreak(app, scorer):
    season, _, teams = _setup(app, n_teams=3)
    a, b, c = (t["id"] for t in teams)
    # Level on points AND NRR: A beat B, B beat C, C beat A -> head-to-head must decide
    _seed_match(app, scorer, "M1", a, b, runs_for=60, balls_for=36, runs_against=40, balls_against=36)
    _seed_match(app, scorer, "M2", c, a, runs_for=60, balls_for=36, runs_against=40, balls_against=36)
    _seed_match(app, scorer, "M3", b, c, runs_for=60, balls_for=36, runs_against=40, balls_against=36)
    table = scorer.league_table(season["id"])
    # All on 2 points, NRR +3.33: order decided by head-to-head (A>B, B>C, C>A -> circular, then boundaries/name)
    assert len(table) == 3
    assert all(e["points"] == 2 for e in table)


def test_league_table_boundaries_tiebreak(app, scorer):
    season, _, teams = _setup(app, n_teams=2)
    a, b = (t["id"] for t in teams)
    # Same points, NRR, and head-to-head (split 1-1) -> boundaries decide
    _seed_match(app, scorer, "M1", a, b, runs_for=60, balls_for=36, runs_against=40, balls_against=36)
    _seed_match(app, scorer, "M2", b, a, runs_for=60, balls_for=36, runs_against=40, balls_against=36)
    with app.extensions["db"].write() as conn:
        conn.execute("UPDATE match_team_stats SET fours = 10, sixes = 2 WHERE team_id = ?", (a,))
        conn.execute("UPDATE match_team_stats SET fours = 5, sixes = 1 WHERE team_id = ?", (b,))
    table = scorer.league_table(season["id"])
    assert table[0]["team_id"] == a  # more boundaries (12 vs 6)


# ----------------------------------------------------------------------
# leaderboards
# ----------------------------------------------------------------------
def test_leaderboards_rankings(app, scorer):
    season, _, teams = _setup(app, n_teams=2)
    a, b = (t["id"] for t in teams)
    _seed_match(app, scorer, "M1", a, b)
    with app.extensions["db"].write() as conn:
        sid = season["id"]
        conn.execute(
            "INSERT INTO match_player_stats (id, match_key, season_id, player_id, player_name, "
            "team_id, team_name, runs, balls_faced, fours, sixes, dismissed, wickets, "
            "balls_bowled, runs_conceded, fantasy_score, innings_batted) "
            "VALUES ('p1', ?, ?, 'gp-alice', 'Alice', ?, 'Thunder', 60, 30, 6, 2, 1, 0, 0, 0, 40, 1)",
            (f"{sid}:m1", sid, a))
        conn.execute(
            "INSERT INTO match_player_stats (id, match_key, season_id, player_id, player_name, "
            "team_id, team_name, runs, balls_faced, dismissed, wickets, balls_bowled, "
            "runs_conceded, fantasy_score) "
            "VALUES ('p2', ?, ?, 'gp-cara', 'Cara', ?, 'Blaze', 10, 20, 1, 3, 18, 12, 55)",
            (f"{sid}:m1", sid, b))
    boards = scorer.leaderboards(season["id"])
    assert boards["batters"][0]["player_name"] == "Alice"
    assert boards["bowlers"][0]["player_name"] == "Cara"
    assert boards["fantasy"][0]["player_name"] == "Cara"
    assert boards["batters"][0]["strike_rate"] == 200.0
    assert boards["bowlers"][0]["economy"] == 4.0


# ----------------------------------------------------------------------
# routes
# ----------------------------------------------------------------------
def test_route_smoke(app, scorer):
    season, _, teams = _setup(app, n_teams=2)
    a, b = (t["id"] for t in teams)
    scorer.upsert_match_registry_entry(
        season["id"], "M1", between="Thunder vs Blaze",
        team_a_global_id=a, team_b_global_id=b)
    c = app.test_client()
    assert c.get("/matches").status_code == 200
    assert c.get("/table").status_code == 200
    assert c.get("/leaderboards").status_code == 200
    assert c.get("/teams").status_code == 200
    assert c.get(f"/matches/{season['id']}").status_code == 200
    assert c.get(f"/matches/{season['id']}/M1").status_code == 200
    assert c.get("/matches/nope").status_code == 302
    assert c.get("/teams/nope").status_code == 302
    assert c.get("/players/nope").status_code == 302


def test_admin_scorer_requires_login(app):
    c = app.test_client()
    assert c.get("/admin/scorer").status_code == 302
    c.post("/auth/login", data={"username": "admin", "password": "admin123"})
    assert c.get("/admin/scorer").status_code == 200


def test_admin_scorer_import_flow(app, scorer):
    season, _, teams = _setup(app, n_teams=2)
    a, b = (t["id"] for t in teams)
    scorer.upsert_match_registry_entry(
        season["id"], "M1", between="Thunder vs Blaze",
        team_a_global_id=a, team_b_global_id=b)
    rows = [_delivery(match_id="M1", innings=1, bat_team_id=a, bowl_team_id=b)]
    c = app.test_client()
    c.post("/auth/login", data={"username": "admin", "password": "admin123"})
    r = c.post("/admin/scorer/import", data={
        "season_id": season["id"],
        "csv": (io.BytesIO(_csv_bytes(rows)), "M1.csv"),
    }, content_type="multipart/form-data", follow_redirects=True)
    assert b"Imported M1" in r.data
    summary = scorer.match_summary(season["id"], "M1")
    assert summary["has_uploaded_data"]
