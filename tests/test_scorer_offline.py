"""Offline scorer + integrated scorecard PDF tests.

Covers: scorer context/payload, /scorer + /scorer/download routes, the
scorer-CSV -> import -> match_summary round trip, the call-up batting order
(batter_order) fix, delivery_log persistence, and the DB-fed scorecard PDF
(including fall-of-wickets and the walkover case).
"""
import csv
import io

import pytest

from app.services.scorecard_service import _fall_of_wickets
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


def _csv_bytes(rows, header=CSV_HEADER):
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(header)
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8")


def _fake_upload(content: bytes, name: str = "M1.csv"):
    class FakeFile:
        filename = name

        def read(self):
            return content
    return FakeFile()


def _delivery(batter, batter_id, order, ns, ns_id, innings, runs, prog,
              bat_team="Thunder", bat_team_id="t1", bowler="Cara", bowler_id="c1",
              bowl_team="Blaze", bowl_team_id="t2", dismissed="", dismissed_id="",
              wkt=0):
    return ["M1", "Thunder vs Blaze", "Ground 1", "1.3.0", "0", "None", str(innings),
            bat_team, bat_team_id, "mgr", "0", "1", "Yes", batter, batter_id,
            str(order), ns, ns_id, bowler, bowler_id, bowl_team, bowl_team_id, "mgr",
            str(runs), "0", "None", dismissed or "None", dismissed_id, str(prog),
            str(wkt), "Thunder", "Thunder won"]


@pytest.fixture()
def scorer(app):
    return app.extensions["scorer_service"]


def _season_with_teams(app, n_teams=2):
    """2-team season; teams[0] Thunder (mgr Alice), teams[1] Blaze (mgr Bob)."""
    season, player_rows, teams = _setup(app, n_teams=n_teams)
    return season, player_rows, teams


def _pid(player_rows, name):
    return next(p["id"] for p in player_rows if p["name"] == name)


def _register(app, scorer, sid, team_a, team_b):
    scorer.upsert_match_registry_entry(sid, "M1", match_number="Match 1",
                                       between="Thunder vs Blaze",
                                       team_a_global_id=team_a["id"],
                                       team_b_global_id=team_b["id"])


# ----------------------------------------------------------------------
# scorer context + routes
# ----------------------------------------------------------------------
def test_scorer_context_payload(app, scorer):
    season, player_rows, teams = _season_with_teams(app)
    _register(app, scorer, season["id"], teams[0], teams[1])

    context = scorer.build_scorer_context()
    config = context["scorer_config"]
    payload = context["scorer_payload"]
    assert payload["season"]["slug"] == season["id"]
    assert context["scorer_download_filename"] == f"scorer-v{config['version']}.html"
    assert context["scorer_download_url"] == "/scorer/download"

    # Teams carry local ids + manager + manager's player in the roster.
    assert len(payload["teams"]) == 2
    thunder = next(t for t in payload["teams"] if t["name"] == "Thunder")
    assert thunder["id"] == teams[0]["id"]
    assert thunder["manager_id"] == teams[0]["manager_player_id"]
    roster_names = [p["name"] for p in thunder["players"]]
    assert "Alice" in roster_names  # the manager is one of the 4 players
    assert all(p["id"] and p["name"] for t in payload["teams"] for p in t["players"])

    # Registry matches prefill setup: M1 maps global team ids to local ids.
    m1 = next(m for m in payload["matches"] if m["match_id"] == "M1")
    assert m1["team_a_id"] == teams[0]["id"]
    assert m1["team_b_id"] == teams[1]["id"]


def test_scorer_payload_includes_manager_without_season_row(app, scorer):
    """Real-flow managers (excluded from the season's auction pool, so they
    have no season players row) still appear in the scorer roster — under
    their global id — and their stats round-trip through the CSV import."""
    import secrets

    svc = app.extensions["auction_service"]
    season = svc.create_season("Test Season")
    sid = season["id"]
    # A manager global player with NO season players row (sync_season_setup
    # excludes managers from the auction pool).
    mgr_gid = secrets.token_hex(8)
    with app.extensions["db"].write() as conn:
        conn.execute(
            "INSERT INTO global_players (id, name, tier, speciality, created_at) "
            "VALUES (?, 'Zara', 'gold', 'ALL_ROUNDER', ?)",
            (mgr_gid, "2026-01-01T00:00:00"))
    team = svc.create_team(sid, "Zephyrs", mgr_gid)
    with app.extensions["db"].read() as conn:
        assert conn.execute(
            "SELECT 1 FROM players WHERE season_id = ? AND global_player_id = ?",
            (sid, mgr_gid)).fetchone() is None

    scorer.save_config({"season_slug": sid})
    payload = scorer.build_scorer_context()["scorer_payload"]
    zephyrs = next(t for t in payload["teams"] if t["id"] == team["id"])
    mgr = next(p for p in zephyrs["players"] if p["name"] == "Zara")
    assert mgr["id"] == mgr_gid  # global id -> CSV import resolves it

    # Round trip: Zara bats (runs) and bowls (wicket) under her global id.
    t2_gid = secrets.token_hex(8)
    with app.extensions["db"].write() as conn:
        conn.execute(
            "INSERT INTO global_players (id, name, tier, speciality, created_at) "
            "VALUES (?, 'Nemo', 'silver', 'BOWLER', ?)",
            (t2_gid, "2026-01-01T00:00:00"))
    t2_row = svc.create_team(sid, "Vipers", t2_gid)
    _register(app, scorer, sid, team, t2_row)

    rows = [
        _delivery("Zara", mgr_gid, 1, "Nemo", "n1", 1, 4, 4,
                  bat_team="Zephyrs", bat_team_id=team["id"],
                  bowl_team="Vipers", bowl_team_id=t2_row["id"]),
        _delivery("Nemo", "n1", 2, "Zara", mgr_gid, 1, 2, 6,
                  bat_team="Zephyrs", bat_team_id=team["id"],
                  bowl_team="Vipers", bowl_team_id=t2_row["id"]),
        _delivery("Nemo", "n1", 1, "Zara", mgr_gid, 2, 0, 0,
                  bat_team="Vipers", bat_team_id=t2_row["id"],
                  bowl_team="Zephyrs", bowl_team_id=team["id"],
                  bowler="Zara", bowler_id=mgr_gid,
                  dismissed="Nemo", dismissed_id="n1", wkt=1),
    ]
    scorer.import_match_csv(_fake_upload(_csv_bytes(rows)), season_id=sid)

    with app.extensions["db"].read() as conn:
        stats = conn.execute(
            "SELECT player_id, player_name, runs, wickets, balls_faced, balls_bowled "
            "FROM match_player_stats WHERE season_id = ? AND player_id = ?",
            (sid, mgr_gid)).fetchall()
    assert len(stats) == 1
    s = stats[0]
    assert s["player_name"] == "Zara"
    assert s["runs"] == 4  # ball 2's 2 runs belong to Nemo
    assert s["balls_faced"] == 1
    assert s["wickets"] == 1
    assert s["balls_bowled"] == 1


def test_scorer_routes(app):
    _season_with_teams(app)
    client = app.test_client()

    r = client.get("/scorer")
    assert r.status_code == 200
    assert b"const SCORER_DATA" in r.data
    assert b"Download HTML" in r.data

    r = client.get("/scorer/download")
    assert r.status_code == 200
    disposition = r.headers.get("Content-Disposition") or ""
    assert disposition.startswith("attachment")
    assert "scorer-v1.3.0.html" in disposition


# ----------------------------------------------------------------------
# round trip + batting order (call-up order fix)
# ----------------------------------------------------------------------
def test_round_trip_and_callup_order(app, scorer):
    season, player_rows, teams = _season_with_teams(app)
    sid = season["id"]
    t1, t2 = teams[0]["id"], teams[1]["id"]
    _register(app, scorer, sid, teams[0], teams[1])

    rows = [
        # Innings 1 (Thunder bat): call-up Alice(1) 0, Bob(2) 6 & out, Eve(3) 50.
        _delivery("Alice", _pid(player_rows, "Alice"), 1, "Bob", _pid(player_rows, "Bob"),
                  1, 0, 0, bat_team_id=t1, bowl_team_id=t2),
        _delivery("Bob", _pid(player_rows, "Bob"), 2, "Alice", _pid(player_rows, "Alice"),
                  1, 6, 6, bat_team_id=t1, bowl_team_id=t2,
                  dismissed="Bob", dismissed_id=_pid(player_rows, "Bob"), wkt=1),
        _delivery("Eve", _pid(player_rows, "Eve"), 3, "Bob", _pid(player_rows, "Bob"),
                  1, 50, 56, bat_team_id=t1, bowl_team_id=t2),
        # Innings 2 (Blaze bat): Cara(1) faces one ball; Dave never faces.
        _delivery("Cara", _pid(player_rows, "Cara"), 1, "Dave", _pid(player_rows, "Dave"),
                  2, 4, 4, bat_team="Blaze", bat_team_id=t2,
                  bowl_team="Thunder", bowl_team_id=t1),
    ]
    derived = scorer.import_match_csv(_fake_upload(_csv_bytes(rows)), season_id=sid)
    assert derived["match_row"]["delivery_rows"] == 4

    summary = scorer.match_summary(sid, "M1")
    assert summary["has_uploaded_data"]
    assert len(summary["delivery_log"]) == 4

    thunder = next(s for s in summary["team_sections"] if s["team_name"] == "Thunder")
    # Call-up order, NOT runs order (Eve scored most but bats last).
    batting = [(b["player_name"], b["batter_order"]) for b in thunder["batting"]]
    assert batting == [("Alice", 1), ("Bob", 2), ("Eve", 3)]

    # Innings-start non-striker who never faces still gets order 2.
    blaze = next(s for s in summary["team_sections"] if s["team_name"] == "Blaze")
    assert [(b["player_name"], b["batter_order"]) for b in blaze["batting"]] == \
        [("Cara", 1), ("Dave", 2)]

    # Aggregates from the same import.
    assert thunder["team"]["runs_scored"] == 56
    assert thunder["team"]["wickets_lost"] == 1


def test_batting_order_fallback_without_column(app, scorer):
    """Old CSVs without 'Batter Order' still list batsmen by call-up order."""
    season, player_rows, teams = _season_with_teams(app)
    sid = season["id"]
    t1, t2 = teams[0]["id"], teams[1]["id"]
    _register(app, scorer, sid, teams[0], teams[1])

    rows = [
        _delivery("Alice", _pid(player_rows, "Alice"), "", "Bob", _pid(player_rows, "Bob"),
                  1, 0, 0, bat_team_id=t1, bowl_team_id=t2),
        _delivery("Bob", _pid(player_rows, "Bob"), "", "Alice", _pid(player_rows, "Alice"),
                  1, 6, 6, bat_team_id=t1, bowl_team_id=t2),
        _delivery("Eve", _pid(player_rows, "Eve"), "", "Bob", _pid(player_rows, "Bob"),
                  1, 50, 56, bat_team_id=t1, bowl_team_id=t2),
    ]
    scorer.import_match_csv(_fake_upload(_csv_bytes(rows)), season_id=sid)

    summary = scorer.match_summary(sid, "M1")
    thunder = next(s for s in summary["team_sections"] if s["team_name"] == "Thunder")
    batting = [b["player_name"] for b in thunder["batting"]]
    assert batting == ["Alice", "Bob", "Eve"]
    # All three have a derived order (no None placeholders).
    assert all(b["batter_order"] is not None for b in thunder["batting"])


# ----------------------------------------------------------------------
# ball-by-ball view
# ----------------------------------------------------------------------
def test_ball_by_ball_structure(app, scorer):
    """ball_by_ball groups deliveries into innings/overs/balls with labels,
    FOW, and (closed + unbroken) partnerships."""
    season, player_rows, teams = _season_with_teams(app)
    sid = season["id"]
    t1, t2 = teams[0]["id"], teams[1]["id"]
    _register(app, scorer, sid, teams[0], teams[1])

    rows = [
        _delivery("Alice", _pid(player_rows, "Alice"), 1, "Bob", _pid(player_rows, "Bob"),
                  1, 1, 1, bat_team_id=t1, bowl_team_id=t2),
        _delivery("Bob", _pid(player_rows, "Bob"), 2, "Alice", _pid(player_rows, "Alice"),
                  1, 6, 7, bat_team_id=t1, bowl_team_id=t2,
                  dismissed="Bob", dismissed_id=_pid(player_rows, "Bob"), wkt=1),
        _delivery("Eve", _pid(player_rows, "Eve"), 3, "Bob", _pid(player_rows, "Bob"),
                  1, 2, 9, bat_team_id=t1, bowl_team_id=t2),
        _delivery("Cara", _pid(player_rows, "Cara"), 1, "Dave", _pid(player_rows, "Dave"),
                  2, 4, 4, bat_team="Blaze", bat_team_id=t2,
                  bowl_team="Thunder", bowl_team_id=t1),
    ]
    scorer.import_match_csv(_fake_upload(_csv_bytes(rows)), season_id=sid)

    data = scorer.ball_by_ball(sid, "M1")
    assert data["has_ball_by_ball"]
    assert data["between"] == "Thunder vs Blaze"
    assert len(data["innings"]) == 2

    inn1 = data["innings"][0]
    assert inn1["team"] == "Thunder"
    assert inn1["total"] == "9/1"
    assert len(inn1["overs"]) == 1
    balls = inn1["overs"][0]["balls"]
    assert [(b["label"], b["progressive"]) for b in balls] == \
        [("1", "1/0"), ("W", "7/1"), ("2", "9/1")]
    assert balls[1]["dismissed"] == "Bob"
    assert balls[1]["batter"] == "Bob"
    assert balls[1]["bowler"] == "Cara"  # _delivery defaults the bowler to Cara

    inn2 = data["innings"][1]
    assert inn2["team"] == "Blaze"
    assert inn2["total"] == "4/0"
    assert inn2["overs"][0]["balls"][0]["label"] == "4"

    # FOW per innings; Blaze has none. (_delivery hardcodes Over 0 / Ball 1.)
    fow = {f["innings"]: f["entries"] for f in data["fow"]}
    assert fow["1"] == ["7-1 (Bob, 0.1)"]
    assert fow["2"] == []

    # Partnerships: closed 1st-wicket stand (7 runs) + unbroken 2nd stand (Eve),
    # plus an unbroken Blaze stand.
    parts = data["partnerships"]
    inn1 = [p for p in parts if p["innings"] == "1"]
    assert len(inn1) == 2
    assert inn1[0]["runs"] == 7
    assert inn1[0]["dismissed"] == "Bob"
    assert inn1[1]["current"] is True
    assert inn1[1]["runs"] == 2
    assert inn1[1]["partners"] == "Eve"
    unbroken = [p for p in parts if p["innings"] == "2"]
    assert len(unbroken) == 1
    assert unbroken[0]["current"] is True
    assert unbroken[0]["runs"] == 4
    assert unbroken[0]["partners"] == "Cara"


def test_ball_by_ball_no_data_and_missing_match(app, scorer):
    """Matches without a delivery log report empty innings; unknown matches are None."""
    season, player_rows, teams = _season_with_teams(app)
    sid = season["id"]
    t1, t2 = teams[0]["id"], teams[1]["id"]
    _register(app, scorer, sid, teams[0], teams[1])

    # Registered but never imported -> no ball-by-ball data.
    data = scorer.ball_by_ball(sid, "M1")
    assert data is not None
    assert data["has_ball_by_ball"] is False
    assert data["innings"] == []

    # Unknown match -> None (route redirects).
    assert scorer.ball_by_ball(sid, "M999") is None


def test_ball_by_ball_route(app, scorer):
    """The /balls route renders the scorecard link + over grid, and redirects
    for unknown matches."""
    season, player_rows, teams = _season_with_teams(app)
    sid = season["id"]
    t1, t2 = teams[0]["id"], teams[1]["id"]
    _register(app, scorer, sid, teams[0], teams[1])

    rows = [
        _delivery("Alice", _pid(player_rows, "Alice"), 1, "Bob", _pid(player_rows, "Bob"),
                  1, 1, 1, bat_team_id=t1, bowl_team_id=t2),
        _delivery("Bob", _pid(player_rows, "Bob"), 2, "Alice", _pid(player_rows, "Alice"),
                  1, 6, 7, bat_team_id=t1, bowl_team_id=t2,
                  dismissed="Bob", dismissed_id=_pid(player_rows, "Bob"), wkt=1),
        _delivery("Cara", _pid(player_rows, "Cara"), 1, "Dave", _pid(player_rows, "Dave"),
                  2, 4, 4, bat_team="Blaze", bat_team_id=t2,
                  bowl_team="Thunder", bowl_team_id=t1),
    ]
    scorer.import_match_csv(_fake_upload(_csv_bytes(rows)), season_id=sid)

    client = app.test_client()
    resp = client.get(f"/matches/{sid}/M1/balls")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Ball by ball" in body
    assert "Over 0" in body
    assert "Bob out" in body or "Bob" in body
    # Summary page links through to the balls view.
    summary = client.get(f"/matches/{sid}/M1")
    assert b"Ball by ball" in summary.data

    # Unknown match -> redirect to matches index.
    resp = client.get(f"/matches/{sid}/M999/balls")
    assert resp.status_code == 302


# ----------------------------------------------------------------------
# scorecard PDF
# ----------------------------------------------------------------------
def test_fall_of_wickets_helper():
    log = [
        {"Dismissed Batter": "Bob", "Batting Team": "Thunder",
         "Progressive Runs": "6", "Over Number": "0", "Ball Number": "2"},
        {"Dismissed Batter": "None", "Batting Team": "Thunder",
         "Progressive Runs": "56", "Over Number": "1", "Ball Number": "3"},
        {"Dismissed Batter": "Eve", "Batting Team": "Blaze",
         "Progressive Runs": "4", "Over Number": "0", "Ball Number": "1"},
    ]
    fow = _fall_of_wickets(log)
    assert fow["Thunder"] == ["6-1 (Bob, 0.2)"]
    assert fow["Blaze"] == ["4-1 (Eve, 0.1)"]
    assert _fall_of_wickets([]) == {}
    assert _fall_of_wickets([{"Dismissed Batter": "None"}]) == {}


def test_scorecard_pdf_build(app, scorer):
    season, player_rows, teams = _season_with_teams(app)
    sid = season["id"]
    t1, t2 = teams[0]["id"], teams[1]["id"]
    _register(app, scorer, sid, teams[0], teams[1])

    rows = [
        _delivery("Alice", _pid(player_rows, "Alice"), 1, "Bob", _pid(player_rows, "Bob"),
                  1, 0, 0, bat_team_id=t1, bowl_team_id=t2),
        _delivery("Bob", _pid(player_rows, "Bob"), 2, "Alice", _pid(player_rows, "Alice"),
                  1, 6, 6, bat_team_id=t1, bowl_team_id=t2,
                  dismissed="Bob", dismissed_id=_pid(player_rows, "Bob"), wkt=1),
        _delivery("Eve", _pid(player_rows, "Eve"), 3, "Bob", _pid(player_rows, "Bob"),
                  1, 50, 56, bat_team_id=t1, bowl_team_id=t2),
        _delivery("Cara", _pid(player_rows, "Cara"), 1, "Dave", _pid(player_rows, "Dave"),
                  2, 4, 4, bat_team="Blaze", bat_team_id=t2,
                  bowl_team="Thunder", bowl_team_id=t1),
    ]
    scorer.import_match_csv(_fake_upload(_csv_bytes(rows)), season_id=sid)
    summary = scorer.match_summary(sid, "M1")

    pdf = app.extensions["scorecard_service"].build(summary, [])
    assert pdf[:4] == b"%PDF"
    assert len(pdf) > 3000

    # Walkover match -> small PDF, no innings sections.
    scorer.upsert_match_registry_entry(sid, "M6", walkover=True,
                                       walkover_winner_team_id=teams[0]["id"],
                                       between="Thunder vs Blaze",
                                       team_a_global_id=t1, team_b_global_id=t2)
    w_summary = scorer.match_summary(sid, "M6")
    assert w_summary["walkover"]
    w_pdf = app.extensions["scorecard_service"].build(w_summary, [])
    assert w_pdf[:4] == b"%PDF"


def test_scorecard_route_with_revenue(app, scorer):
    season, player_rows, teams = _season_with_teams(app)
    sid = season["id"]
    t1, t2 = teams[0]["id"], teams[1]["id"]
    _register(app, scorer, sid, teams[0], teams[1])

    rows = [
        _delivery("Alice", _pid(player_rows, "Alice"), 1, "Bob", _pid(player_rows, "Bob"),
                  1, 0, 0, bat_team_id=t1, bowl_team_id=t2),
        _delivery("Bob", _pid(player_rows, "Bob"), 2, "Alice", _pid(player_rows, "Alice"),
                  1, 6, 6, bat_team_id=t1, bowl_team_id=t2),
        _delivery("Eve", _pid(player_rows, "Eve"), 3, "Bob", _pid(player_rows, "Bob"),
                  1, 50, 56, bat_team_id=t1, bowl_team_id=t2),
        _delivery("Cara", _pid(player_rows, "Cara"), 1, "Dave", _pid(player_rows, "Dave"),
                  2, 4, 4, bat_team="Blaze", bat_team_id=t2,
                  bowl_team="Thunder", bowl_team_id=t1),
    ]
    scorer.import_match_csv(_fake_upload(_csv_bytes(rows)), season_id=sid)
    # Auto finance posts the match rewards -> revenue section in the PDF.
    outcome = app.extensions["finance_service"].on_match_finalized(sid, "M1")
    assert outcome["finalized"] and outcome["rewarded"]

    client = app.test_client()
    r = client.get(f"/matches/{sid}/M1/scorecard")
    assert r.status_code == 200
    assert r.data[:4] == b"%PDF"
    assert "scorecard.pdf" in (r.headers.get("Content-Disposition") or "")

    # No data -> redirect, not a PDF.
    r = client.get(f"/matches/{sid}/M6/scorecard")
    assert r.status_code == 302
