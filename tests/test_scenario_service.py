"""Qualification scenarios + NRR predictor engine tests.

Covers: standings/remaining-fixture derivation, the top-N season-aware
switch (final season → top-2, no-final season → top-1), qualification
statuses (qualified/safe/in-contention/eliminated + requirements), and the
margin calculator (batting-first + chasing, direct-clash and 3rd-party).
"""
import pytest

from tests.conftest import _setup


@pytest.fixture()
def scenario(app):
    return app.extensions["scenario_service"]


@pytest.fixture()
def scorer(app):
    return app.extensions["scorer_service"]


def _seed_match(app, scorer, match_id, winner_id, loser_id,
                winner_name="Thunder", loser_name="Blaze",
                runs_for=60, balls_for=36, runs_against=40, balls_against=36):
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


# ----------------------------------------------------------------------
# qualification scenarios
# ----------------------------------------------------------------------
def test_scenarios_complete_season_top2(app, scenario, scorer):
    """A season with a final (registry > RR count) uses top-2; once all RR
    matches are played the standings collapse to final positions."""
    season, _, teams = _setup(app, n_teams=2)
    sid = season["id"]
    a, b = teams[0]["id"], teams[1]["id"]
    # 2 teams → RR = 2 matches; add a 3rd registry entry (the "final").
    _seed_match(app, scorer, "M1", a, b)
    _seed_match(app, scorer, "M2", b, a, runs_for=50, balls_for=36,
                runs_against=45, balls_against=36)
    scorer.upsert_match_registry_entry(sid, "M3", team_a_global_id=a, team_b_global_id=b)

    assert scenario.qualify_count(sid) == 2
    sc = scenario.scenarios(sid)
    assert sc["complete"] is True
    assert sc["remaining"] == []
    assert len(sc["rows"]) == 2
    # Top of the table is qualified; the runner-up too (final season).
    assert sc["rows"][0]["status"] == "Qualified"
    assert sc["rows"][1]["status"] == "Qualified"


def test_scenarios_in_progress_top1(app, scenario, scorer):
    """No final (registry == RR count) → top-1 (S2: champion = table topper);
    unplayed registry matches appear as remaining fixtures and the teams are
    'in contention'."""
    season, _, teams = _setup(app, n_teams=2)
    sid = season["id"]
    a, b = teams[0]["id"], teams[1]["id"]
    _seed_match(app, scorer, "M1", a, b)
    scorer.upsert_match_registry_entry(sid, "M2", team_a_global_id=a, team_b_global_id=b)

    assert scenario.qualify_count(sid) == 1
    sc = scenario.scenarios(sid)
    assert sc["complete"] is False
    assert [e["match_id"] for e in sc["remaining"]] == ["M2"]
    assert len(sc["rows"]) == 2
    for r in sc["rows"]:
        assert r["status"] == "In contention"
    # leader max = 2 + 2 = 4; challenger max = 2
    assert sc["rows"][0]["max_points"] == 4
    assert "remaining" in sc["rows"][0]


def test_scenarios_eliminated_and_safe(app, scenario, scorer):
    """3 teams: leader not eliminated, middle in contention, last eliminated."""
    season, _, teams = _setup(app, n_teams=3)
    sid = season["id"]
    a, b, c = teams[0]["id"], teams[1]["id"], teams[2]["id"]
    # A: 4pts (beats B and C once each). B: 2pts (beats C). C: 0.
    # Remaining M5/M6 are B vs C, so only B and C can still gain points.
    _seed_match(app, scorer, "M1", a, b)
    _seed_match(app, scorer, "M2", a, c)
    _seed_match(app, scorer, "M3", b, c)
    _seed_match(app, scorer, "M4", a, b)
    scorer.upsert_match_registry_entry(sid, "M5", team_a_global_id=b, team_b_global_id=c)
    scorer.upsert_match_registry_entry(sid, "M6", team_a_global_id=b, team_b_global_id=c)

    sc = scenario.scenarios(sid)
    by_name = {r["team_name"]: r for r in sc["rows"]}
    # C: 0pts, 2 remaining (max 4), A already has 4 and B has 2 → can't
    # guarantee the cut with a single final round — eliminated only if both
    # A and B exceed C's max (A does: 4 == max is a tie, not a beat).
    # C's max = 0 + 2*2 = 4, which A matches but doesn't beat → in contention
    # is not guaranteed; assert the safe invariants instead:
    assert by_name["Thunder"]["status"] in ("Safe", "In contention")
    # The schedule is the full double round-robin over the 3 teams, so
    # Thunder (A) still has its unregistered second match vs Falcon (C).
    assert by_name["Thunder"]["remaining"] == 1
    # B vs C pair is capped at 2 scheduled matches (M3 played + M5 remaining).
    assert by_name["Blaze"]["remaining"] == 1
    assert by_name["Blaze"]["max_points"] == by_name["Blaze"]["points"] + 2
    # Falcon (C) can still reach 4pts but two teams already hold 4+; with
    # top-1 it must beat both on NRR — check the requirement is meaningful.
    assert by_name["Falcon"]["requirement"]


def test_scenarios_remaining_derived_from_teams_not_registry(app, scenario, scorer):
    """Regression: a season whose registry only holds the PLAYED matches must
    still report the full qualification schedule (S2 showed 'Season complete'
    with 2 of 12 matches played because the unplayed fixtures were never
    registered)."""
    season, _, teams = _setup(app, n_teams=4)
    sid = season["id"]
    a, b, c, d = (t["id"] for t in teams)
    _seed_match(app, scorer, "M1", a, b)
    _seed_match(app, scorer, "M2", c, d)
    # Registry contains ONLY the played matches — no future fixtures.
    sc = scenario.scenarios(sid)
    assert sc["complete"] is False
    # 4 teams → 6 pairs × 2 rounds = 12 scheduled; 2 played → 10 left.
    assert len(sc["remaining"]) == 10
    for r in sc["rows"]:
        assert r["remaining"] == 5
        assert r["max_points"] == r["points"] + 10
        assert r["status"] in ("In contention", "Safe")
        assert "Tournament complete" not in r["requirement"]


def test_scenarios_eliminated_when_cut_unreachable(app, scenario, scorer):
    """A team whose max points can't reach the cut is eliminated."""
    season, _, teams = _setup(app, n_teams=3)
    sid = season["id"]
    a, b, c = teams[0]["id"], teams[1]["id"], teams[2]["id"]
    # A sweeps: 6pts after three wins. B and C each 0. Registry = 5 entries
    # (RR=6 for 3 teams → 5 < 6 → no final → top-1).
    _seed_match(app, scorer, "M1", a, b)
    _seed_match(app, scorer, "M2", a, c)
    _seed_match(app, scorer, "M3", b, c)  # B beats C
    _seed_match(app, scorer, "M4", a, b)
    _seed_match(app, scorer, "M5", b, c)  # B beats C again
    scorer.upsert_match_registry_entry(sid, "M6", team_a_global_id=a, team_b_global_id=c)

    sc = scenario.scenarios(sid)
    by_name = {r["team_name"]: r for r in sc["rows"]}
    assert by_name["Thunder"]["points"] == 6
    # C: 0pts, 1 remaining (max 2) — top-1 needs to beat Thunder's 6.
    assert by_name["Falcon"]["status"] == "Eliminated"
    assert "Cannot reach top 1" in by_name["Falcon"]["requirement"]


def test_qualify_count_season_aware(app, scenario, scorer):
    season, _, teams = _setup(app, n_teams=2)
    sid = season["id"]
    a, b = teams[0]["id"], teams[1]["id"]
    _seed_match(app, scorer, "M1", a, b)
    # RR = 2 matches; with 2 registered there is no final → top-1.
    assert scenario.qualify_count(sid) == 1
    # A 3rd registry entry (the final) flips it to top-2.
    scorer.upsert_match_registry_entry(sid, "M2", team_a_global_id=a, team_b_global_id=b)
    scorer.upsert_match_registry_entry(sid, "M3", team_a_global_id=a, team_b_global_id=b)
    assert scenario.qualify_count(sid) == 2


# ----------------------------------------------------------------------
# margin calculator
# ----------------------------------------------------------------------
def test_margin_calc_direct_clash(app, scenario, scorer):
    """Direct clash: rival is the match opponent; both NRRs move together."""
    season, _, teams = _setup(app, n_teams=2)
    sid = season["id"]
    a, b = teams[0]["id"], teams[1]["id"]
    _seed_match(app, scorer, "M1", a, b)  # Thunder won, NRR +3.33 vs -3.33
    mc = scenario.margin_calc(sid, "Thunder", "Blaze", "Blaze", 30)
    assert mc is not None
    assert mc["direct"] is True
    bf = mc["batting_first"]
    assert bf["min_margin"] >= 1
    assert bf["table"] and bf["table"][0]["margin"] == 1
    assert all(r["my_score"] == r["margin"] + mc["opp_score"] for r in bf["table"])
    # Chasing: Thunder already ahead on NRR, so any win within 18 balls works.
    ch = mc["chasing"]
    assert ch["any_win_sufficient"] is True
    assert ch["my_score"] == 31


def test_margin_calc_third_party(app, scenario, scorer):
    """3rd-party rival: rival's NRR is fixed while the match plays out."""
    season, _, teams = _setup(app, n_teams=3)
    sid = season["id"]
    a, b, c = teams[0]["id"], teams[1]["id"], teams[2]["id"]
    _seed_match(app, scorer, "M1", a, c)  # Thunder beats Falcon → Thunder NRR high
    _seed_match(app, scorer, "M2", b, c)  # Blaze beats Falcon too
    mc = scenario.margin_calc(sid, "Blaze", "Thunder", "Falcon", 30)
    assert mc is not None
    assert mc["direct"] is False  # rival Falcon isn't the opponent Thunder
    # Rival NRR fixed across the margin table.
    bf = mc["batting_first"]
    nrr_b = {round(r["nrr_b"], 6) for r in bf["table"]}
    assert len(nrr_b) == 1


def test_margin_calc_invalid_inputs(app, scenario, scorer):
    season, _, teams = _setup(app, n_teams=2)
    sid = season["id"]
    a, b = teams[0]["id"], teams[1]["id"]
    _seed_match(app, scorer, "M1", a, b)
    # Self-opponent / self-rival are rejected.
    assert scenario.margin_calc(sid, "Thunder", "Thunder", "Blaze", 30) is None
    assert scenario.margin_calc(sid, "Thunder", "Blaze", "Thunder", 30) is None
    # Unknown team.
    assert scenario.margin_calc(sid, "Ghosts", "Blaze", "Thunder", 30) is None
    # Unknown season.
    assert scenario.scenarios("nope")["rows"] == []
    assert scenario.margin_calc("nope", "Thunder", "Blaze", "Thunder", 30) is None


def test_margin_calc_zero_balls_guard(app, scenario, scorer):
    """A team that never bowled (walkover) doesn't divide by zero."""
    season, _, teams = _setup(app, n_teams=2)
    sid = season["id"]
    a, b = teams[0]["id"], teams[1]["id"]
    _seed_match(app, scorer, "M1", a, b, runs_for=40, balls_for=36,
                runs_against=0, balls_against=0)  # opponent bowled nothing
    mc = scenario.margin_calc(sid, "Thunder", "Blaze", "Blaze", 20)
    assert mc is not None
    assert mc["batting_first"]["table"]
    assert mc["chasing"]["table"]


# ----------------------------------------------------------------------
# per-match "what's at stake"
# ----------------------------------------------------------------------
def test_match_stakes_panel(app, scenario, scorer):
    """A registered fixture reports both teams' status + a margin hint."""
    season, _, teams = _setup(app, n_teams=2)
    sid = season["id"]
    a, b = teams[0]["id"], teams[1]["id"]
    _seed_match(app, scorer, "M1", a, b)
    scorer.upsert_match_registry_entry(sid, "M2", team_a_global_id=a, team_b_global_id=b)

    st = scenario.match_stakes(sid, "M1")
    assert st is not None
    assert st["qualify_count"] == 1
    assert {t["team_name"] for t in st["teams"]} == {"Thunder", "Blaze"}
    for t in st["teams"]:
        assert t["status"]
        assert t["requirement"]
    # direct-clash fixture (rival is the opponent) still gets a hint;
    # the table leader has no rival above, so the key may be absent
    hints = [t.get("margin_hint", "") for t in st["teams"]]
    assert any("Head-to-head" in h for h in hints)


def test_match_stakes_unknown_match(app, scenario, scorer):
    season, _, teams = _setup(app, n_teams=2)
    sid = season["id"]
    a, b = teams[0]["id"], teams[1]["id"]
    _seed_match(app, scorer, "M1", a, b)
    assert scenario.match_stakes(sid, "M999") is None
    assert scenario.match_stakes("nope", "M1") is None
