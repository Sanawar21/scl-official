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
