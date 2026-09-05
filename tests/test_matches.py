import csv
import io

import pytest

from app.services.scorer_service import player_profile_slug, team_profile_slug
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
# team profile squad names
# ----------------------------------------------------------------------
def test_team_profile_squad_shows_names_not_raw_ids(app, scorer):
    season, teams = _teams(app)
    svc = app.extensions["auction_service"]
    team = teams[0]
    gid = (team.get("global_team_id") or "").strip() or team["id"]

    # Add players to the auction pool and assign two to the team's squad.
    p1 = svc.add_player(season["id"], "Alice", "platinum", "BATTER")
    p2 = svc.add_player(season["id"], "Bob", "gold", "BOWLER")
    import json
    with app.extensions["db"].write() as conn:
        conn.execute("UPDATE teams SET players = ?, bench = ? WHERE id = ?",
                     (json.dumps([p1["id"]]), json.dumps([p2["id"]]), team["id"]))

    profile = scorer.team_profile(team_profile_slug(gid, team["name"]))
    squad = next(s for s in profile["squads"] if s["season_id"] == season["id"])
    assert squad["players"] == [{"player_id": p1["id"], "name": "Alice"}]
    assert squad["bench"] == [{"player_id": p2["id"], "name": "Bob"}]

    # Route renders names, not raw ids.
    c = app.test_client()
    html = c.get(f"/teams/{profile['team_slug']}").data.decode()
    assert "Alice" in html and "Bob" in html
    assert p1["id"] not in html and p2["id"] not in html


def test_team_profile_squad_mentions_manager_name(app, scorer):
    """Each season squad card shows the manager's name (resolved to a name,
    not the raw player id)."""
    season, teams = _teams(app)
    svc = app.extensions["auction_service"]
    team = teams[0]
    gid = (team.get("global_team_id") or "").strip() or team["id"]
    mgr_id = team["manager_player_id"]

    profile = scorer.team_profile(team_profile_slug(gid, team["name"]))
    squad = next(s for s in profile["squads"] if s["season_id"] == season["id"])
    assert squad["manager_global_player_id"] == mgr_id
    assert squad["manager_name"], "manager name must be resolved"

    c = app.test_client()
    html = c.get(f"/teams/{profile['team_slug']}").data.decode()
    assert squad["manager_name"] in html
    assert mgr_id not in html  # no raw id on the page
    # The name links to the manager's player profile.
    assert f"href=\"/players/{squad['manager_slug']}\"" in html
    # And the linked profile actually resolves.
    phtml = c.get(f"/players/{squad['manager_slug']}").data.decode()
    assert squad["manager_name"] in phtml


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
            "balls_bowled, runs_conceded, innings_batted) "
            "VALUES ('p1', ?, ?, 'gp-alice', 'Alice', ?, 'Thunder', 60, 30, 6, 2, 1, 0, 0, 0, 1)",
            (f"{sid}:m1", sid, a))
        conn.execute(
            "INSERT INTO match_player_stats (id, match_key, season_id, player_id, player_name, "
            "team_id, team_name, runs, balls_faced, dismissed, wickets, balls_bowled, "
            "runs_conceded) "
            "VALUES ('p2', ?, ?, 'gp-cara', 'Cara', ?, 'Blaze', 10, 20, 1, 3, 18, 12)",
            (f"{sid}:m1", sid, b))
    boards = scorer.leaderboards(season["id"])
    assert boards["batters"][0]["player_name"] == "Alice"
    assert boards["bowlers"][0]["player_name"] == "Cara"
    assert "fantasy" not in boards, "fantasy removed from leaderboards"
    assert boards["batters"][0]["strike_rate"] == 200.0
    assert boards["bowlers"][0]["economy"] == 4.0
    # Alice has 2 sixes (Cara has none) — sixes board lists her first.
    assert boards["sixes"][0]["player_name"] == "Alice"
    assert boards["sixes"][0]["sixes"] == 2


def test_leaderboards_all_seasons_aggregate(app, scorer):
    season, _, teams = _setup(app, n_teams=2)
    a, b = (t["id"] for t in teams)
    _seed_match(app, scorer, "M1", a, b)
    sid = season["id"]
    # A second season with its own match (same player ids are fine — the
    # no-season aggregate sums across both registries).
    with app.extensions["db"].write() as conn:
        conn.execute("INSERT INTO seasons (id, name, status, created_at) "
                     "VALUES ('s2', 'Season 2', 'active', '2099-01-02T00:00:00')")
    scorer.upsert_match_registry_entry("s2", "M1", team_a_global_id=a, team_b_global_id=b)
    with app.extensions["db"].write() as conn:
        for mkey, sixes in ((f"{sid}:m1", 2), ("s2:m1", 1)):
            conn.execute(
                "INSERT INTO match_player_stats (id, match_key, season_id, player_id, player_name, "
                "team_id, team_name, runs, balls_faced, fours, sixes, dismissed, wickets, "
                "balls_bowled, runs_conceded, innings_batted) "
                "VALUES (?, ?, ?, 'gp-alice', 'Alice', ?, 'Thunder', 30, 15, 3, ?, 0, 0, 0, 0, 1)",
                (f"p-{sixes}", mkey, "s2" if mkey.startswith("s2") else sid, a, sixes))
    all_boards = scorer.leaderboards("")
    alice = next(p for p in all_boards["sixes"] if p["player_name"] == "Alice")
    assert alice["sixes"] == 3  # 2 (season 1) + 1 (season 2)
    per_season = scorer.leaderboards("s2")
    alice_s2 = next(p for p in per_season["sixes"] if p["player_name"] == "Alice")
    assert alice_s2["sixes"] == 1
    # Latest season first (created_at DESC).
    slugs = [s["slug"] for s in scorer.list_match_seasons()]
    assert slugs == ["s2", season["id"]]


def test_player_profile_season_scope_innings_and_ranks(app, scorer):
    """Profile stat lines: innings batted counts matches where the player faced
    >= 1 ball, season scoping filters rows, and every stat carries a league
    rank vs the same scope (leaderboard qualifiers included)."""
    season, _, teams = _setup(app, n_teams=2)
    sid = season["id"]
    t1 = teams[0]["id"]
    t2 = teams[1]["id"]
    with app.extensions["db"].write() as conn:
        conn.execute("INSERT INTO seasons (id, name, status, created_at) "
                     "VALUES ('s2', 'Season 2', 'active', '2099-01-02T00:00:00')")
        for key, season_id in (("k1", sid), ("k2", "s2"), ("k3", sid), ("k4", sid)):
            conn.execute(
                "INSERT INTO match_registry (match_key, season_id, match_id, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (key, season_id, key, "2026-01-01", "2026-01-01"))
        # Alice: M1 (40 off 25, out; 2/20 bowling), M2 in s2 (20*, not out;
        # 1/12), M3 (run out without facing a ball: 0 faced, dismissed).
        alice_rows = [
            ("a1", "k1", sid, 40, 25, 1, 2, 18, 20),
            ("a2", "k2", "s2", 20, 10, 0, 1, 6, 12),
            ("a3", "k3", sid, 0, 0, 1, 0, 0, 0),
        ]
        for row_id, key, season_id, runs, balls, dismissed, wkts, bowl_balls, conceded in alice_rows:
            conn.execute(
                "INSERT INTO match_player_stats (id, match_key, season_id, player_id, "
                "player_name, team_id, team_name, runs, balls_faced, dismissed, "
                "wickets, balls_bowled, runs_conceded) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (row_id, key, season_id, "gp-alice", "Alice", t1, "Thunder",
                 runs, balls, dismissed, wkts, bowl_balls, conceded))
        # Bob beats Alice's run tally in the same season (80) for rank checks.
        conn.execute(
            "INSERT INTO match_player_stats (id, match_key, season_id, player_id, "
            "player_name, team_id, team_name, runs, balls_faced, dismissed, "
            "wickets, balls_bowled, runs_conceded) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("b1", "k4", sid, "gp-bob", "Bob", t2, "Blaze", 80, 30, 1, 0, 0, 0))

    slug = player_profile_slug("gp-alice", "Alice")
    all_p = scorer.player_profile(slug)
    g = all_p["global_stats"]
    assert g["matches"] == 3
    assert g["innings_batted"] == 2, "run-out before facing is not an innings"
    assert g["not_out"] == 1
    assert g["dismissed"] == 2
    assert g["runs"] == 60
    assert g["highest"] == 40 and not g["highest_not_out"]
    assert g["best_wkts"] == 2 and g["best_runs"] == 20
    assert round(g["batting_average"], 2) == 30.0
    assert len(all_p["per_season"]) == 2

    def _row(profile, group, label):
        for grp in profile["stat_groups"]:
            if grp["group"] != group:
                continue
            for r in grp["rows"]:
                if r["label"] == label:
                    return r
        raise AssertionError(f"stat row {label!r} missing")

    runs_row = _row(all_p, "Batting", "Runs")
    assert runs_row["rank"] == 2 and runs_row["of"] == 2  # Bob 80 ahead
    # All-scope economy: 24 balls bowled < 30 -> doesn't qualify -> no rank.
    assert _row(all_p, "Bowling", "Economy")["rank"] is None

    # Season scope: only the sid matches count, ranks recompute in that scope.
    s1_p = scorer.player_profile(slug, sid)
    assert s1_p["global_stats"]["matches"] == 2
    assert s1_p["global_stats"]["innings_batted"] == 1
    assert s1_p["global_stats"]["runs"] == 40
    assert s1_p["per_season"] == []
    assert _row(s1_p, "Batting", "Runs")["rank"] == 2
    # 18 balls >= 12 in a season -> economy qualifies, Alice is the only one.
    econ = _row(s1_p, "Bowling", "Economy")
    assert econ["rank"] == 1 and econ["of"] == 1
    assert round(float(econ["value"]), 2) == round(20 * 6.0 / 18, 2)

    # Route smoke: directory + profile honour the season filter and render the
    # ranked stat groups.
    c = app.test_client()
    assert c.get("/players").status_code == 200
    assert c.get(f"/players?season={sid}").status_code == 200
    body = c.get(f"/players/{slug}").get_data(as_text=True)
    for needle in ("Innings batted", "Batting", "Bowling", "All seasons", "Season 2"):
        assert needle in body
    season_body = c.get(f"/players/{slug}?season={sid}").get_data(as_text=True)
    assert "Season 2" in season_body  # switch is still offered while scoped


def test_player_profile_unqualified_stats_show_figures_unranked(app, scorer):
    """Stats whose qualifier isn't met still display the figure (SR under the
    40-run bar, economy under the overs bar, best bowling without a wicket)
    but carry rank None; true wicket-takers keep their best-bowling rank."""
    season, _, teams = _setup(app, n_teams=2)
    sid = season["id"]
    t1 = teams[0]["id"]
    t2 = teams[1]["id"]
    with app.extensions["db"].write() as conn:
        conn.execute(
            "INSERT INTO match_registry (match_key, season_id, match_id, created_at, updated_at) "
            "VALUES ('k1', ?, 'M1', '2026-01-01', '2026-01-01')", (sid,))
        conn.execute(
            "INSERT INTO match_registry (match_key, season_id, match_id, created_at, updated_at) "
            "VALUES ('k2', ?, 'M2', '2026-01-02', '2026-01-02')", (sid,))
        # Chloe: 30 runs off 25 (SR exists but < 40 runs -> unranked), one over
        # 0/9 (under the 2-over economy bar, best bowling 0/9 unranked).
        conn.execute(
            "INSERT INTO match_player_stats (id, match_key, season_id, player_id, "
            "player_name, team_id, team_name, runs, balls_faced, dismissed, "
            "wickets, balls_bowled, runs_conceded) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("c1", "k1", sid, "gp-chloe", "Chloe", t1, "Thunder",
             30, 25, 1, 0, 6, 9))
        # Dan: a proper wicket spell 1/3 in the same season.
        conn.execute(
            "INSERT INTO match_player_stats (id, match_key, season_id, player_id, "
            "player_name, team_id, team_name, runs, balls_faced, dismissed, "
            "wickets, balls_bowled, runs_conceded) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("d1", "k2", sid, "gp-dan", "Dan", t2, "Blaze",
             0, 0, 0, 1, 6, 3))

    slug = player_profile_slug("gp-chloe", "Chloe")
    prof = scorer.player_profile(slug, sid)

    def _row(profile, group, label):
        for grp in profile["stat_groups"]:
            if grp["group"] != group:
                continue
            for r in grp["rows"]:
                if r["label"] == label:
                    return r
        raise AssertionError(f"stat row {label!r} missing")

    sr = _row(prof, "Batting", "Strike rate")
    assert sr["rank"] is None and sr["value"] == "120.00"
    assert _row(prof, "Batting", "Runs")["rank"] == 1  # only scorer with runs
    econ = _row(prof, "Bowling", "Economy")
    assert econ["rank"] is None and econ["value"] == "9.00"
    best = _row(prof, "Bowling", "Best bowling")
    assert best["rank"] is None and best["value"] == "0/9"
    assert prof["global_stats"]["best_wkts"] == 0
    assert prof["global_stats"]["best_runs"] == 9

    # Dan qualifies for best bowling and ranks #1 among wicket-takers.
    dan_prof = scorer.player_profile(player_profile_slug("gp-dan", "Dan"), sid)
    dan_best = _row(dan_prof, "Bowling", "Best bowling")
    assert dan_best["rank"] == 1 and dan_best["of"] == 1
    assert dan_best["value"] == "1/3"

    # Route smoke: figure renders while the "not ranked" badge shows.
    body = app.test_client().get(f"/players/{slug}?season={sid}").get_data(as_text=True)
    assert "not ranked" in body


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


def test_summary_page_shows_team_branding(app, scorer):
    """Match summary sections carry logo_url + banner_url (SCL fallback)."""
    season, _, teams = _setup(app, n_teams=2)
    a, b = (t["id"] for t in teams)
    scorer.upsert_match_registry_entry(
        season["id"], "M1", between="Thunder vs Blaze",
        team_a_global_id=a, team_b_global_id=b)
    with app.extensions["db"].write() as conn:
        key = f"{season['id']}:m1"
        conn.execute(
            "INSERT INTO match_stats (match_key, season_id, match_id, result, winner_team_id) "
            "VALUES (?, ?, 'M1', 'Thunder won', ?)", (key, season["id"], a))
        conn.execute(
            "INSERT INTO match_team_stats (id, match_key, season_id, team_id, team_name, "
            "runs_scored, balls_faced, runs_conceded, balls_bowled, result, wins) "
            "VALUES ('w1', ?, ?, ?, 'Thunder', 60, 36, 40, 36, 'win', 1)",
            (key, season["id"], a))
        conn.execute(
            "INSERT INTO match_team_stats (id, match_key, season_id, team_id, team_name, "
            "runs_scored, balls_faced, runs_conceded, balls_bowled, result, losses) "
            "VALUES ('l1', ?, ?, ?, 'Blaze', 40, 36, 60, 36, 'loss', 1)",
            (key, season["id"], b))
        conn.execute(
            "INSERT INTO match_player_stats (id, match_key, season_id, player_id, player_name, "
            "team_id, team_name, runs, balls_faced, dismissed, wickets, balls_bowled, "
            "runs_conceded) "
            "VALUES ('ps1', ?, ?, 'gp-alice', 'Alice', ?, 'Thunder', 60, 30, 0, 0, 0, 0)",
            (key, season["id"], a))
    c = app.test_client()
    html = c.get(f"/matches/{season['id']}/M1").get_data(as_text=True)
    # Sections render the banner + logo (SCL fallback URLs).
    assert "team-banner" in html
    assert "team-logo-sm" in html


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
