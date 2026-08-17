import pytest

from .conftest import _setup


# ---------------------------------------------------------------------------
# setup
# ---------------------------------------------------------------------------
def test_season_defaults_are_s2(app, svc):
    season = svc.create_season("Season Two")
    rs = season["ruleset"]
    # S2: no per-tier purse (all zeros).
    assert rs["tier_purses"] == {"platinum": 0, "gold": 0, "silver": 0}
    assert rs["tier_base_prices"] == {"platinum": 3000, "gold": 2000, "silver": 1000}
    assert rs["tier_credits"] == {"platinum": 3, "gold": 2, "silver": 1}
    assert rs["phase_order"][0] == "platinum"
    assert season["status"] == "setup"


def test_create_team_credits_from_manager_profile_no_purse(app, svc):
    season, players, teams = _setup(app)
    by_name = {t["name"]: t for t in teams}
    # S2: no tier purse — the wallet is the manager's own funding (10k from
    # _setup), and credits depend on the manager's tier.
    # Platinum manager (Alice) -> 8-3=5 credits; Gold manager (Bob) -> 8-2=6.
    assert by_name["Thunder"]["credits_remaining"] == 5
    assert by_name["Blaze"]["credits_remaining"] == 6
    assert by_name["Thunder"]["wallet"] == 10000
    assert by_name["Blaze"]["wallet"] == 10000


# ---------------------------------------------------------------------------
# persistent team accounts (global_teams)
# ---------------------------------------------------------------------------
def test_player_creates_team_account_without_season(app, svc):
    """A player can own a team that isn't in any season; its money is their
    wallet and no purse is funded."""
    season, players, _ = _setup(app)
    gp = players[4]["global_player_id"]  # Eve, not a manager yet
    bank = app.extensions["bank_service"]
    acct = bank.get_or_create_account("player", gp)
    bank.adjust(acct["id"], 5000, "eve funds", tx_type="funding")

    team = svc.create_team_account(gp, "Eve United")
    assert team["name"] == "Eve United"
    assert team["manager_player_id"] == gp
    assert team["wallet"] == 5000
    # Not registered for any season.
    with app.extensions["db"].read() as conn:
        assert conn.execute(
            "SELECT 1 FROM teams WHERE global_team_id = ?", (team["id"],)).fetchone() is None
    # Same player can't own a second team.
    try:
        svc.create_team_account(gp, "Eve Second")
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        assert "already manages" in str(exc)


def test_update_team_profile_logo_about(app, svc):
    season, players, _ = _setup(app)
    gp = players[4]["global_player_id"]
    team = svc.create_team_account(gp, "Eve United")
    updated = svc.update_team_profile(team["id"], name="Eve United FC",
                                      logo="https://x/logo.png", about="From Eve")
    assert updated["name"] == "Eve United FC"
    assert updated["logo"] == "https://x/logo.png"
    assert updated["about"] == "From Eve"


def test_create_team_reuses_existing_global_team(app, svc):
    """Admin registering a team for a season reuses the player's team account."""
    season, players, _ = _setup(app)
    sid = season["id"]
    gp = players[4]["global_player_id"]  # Eve
    gt = svc.create_team_account(gp, "Eve United")
    season2 = svc.create_season("Season Two")
    team = svc.create_team(season2["id"], "Eve United", gp)
    assert team["global_team_id"] == gt["id"]
    assert team["season_id"] == season2["id"]
    # Still no purse: wallet is Eve's own funding (0 here — never funded).
    assert team["wallet"] == 0
    # Credits come from the manager's tier (Eve = gold -> 8-2=6).
    assert team["credits_remaining"] == 6


def test_create_team_after_season_completes_profile_only(app, svc):
    """Admin can always create a team; once the season is done it's a profile,
    not a registration."""
    season, players, _ = _setup(app)
    sid = season["id"]
    gp = players[4]["global_player_id"]
    with app.extensions["db"].write() as conn:
        conn.execute("UPDATE seasons SET status = 'completed' WHERE id = ?", (sid,))
    team = svc.create_team(sid, "Late Team", gp)
    assert team["registered"] is False
    with app.extensions["db"].read() as conn:
        assert conn.execute(
            "SELECT 1 FROM teams WHERE season_id = ? AND name = 'Late Team'",
            (sid,)).fetchone() is None
    # The profile still exists for future seasons.
    assert svc.get_global_team(team["id"])["name"] == "Late Team"


def test_add_update_player_before_auction(app, svc):
    season, _, _ = _setup(app)
    p = svc.add_player(season["id"], "Zed", "silver", "BATTER")
    assert p["base_price"] == 1000 and p["credits"] == 1
    updated = svc.update_player(season["id"], p["id"], tier="platinum")
    assert updated["tier"] == "platinum"
    assert updated["base_price"] == 3000 and updated["credits"] == 3
    svc.delete_player(season["id"], p["id"])
    assert svc._get_player(season["id"], p["id"]) is None


def test_gift_team_and_undo(app, svc):
    season, _, teams = _setup(app)
    team = teams[0]
    before = svc._get_team(season["id"], team["id"])["wallet"]
    svc.gift_team(season["id"], team["id"], 500, "add", comment="balance skill gap")
    assert svc._get_team(season["id"], team["id"])["wallet"] == before + 500
    svc.undo_last_action(season["id"])
    assert svc._get_team(season["id"], team["id"])["wallet"] == before


# ---------------------------------------------------------------------------
# bidding
# ---------------------------------------------------------------------------
def test_bid_min_and_increment(app, svc):
    season, players, teams = _setup(app)
    sid = season["id"]
    svc.set_phase(sid, "phase_a_platinum")
    svc.nominate_next(sid)  # Alice, base 3000
    alice = svc._get_player(sid, players[0]["id"])
    thunder = teams[0]
    with pytest.raises(ValueError):
        svc.place_bid(sid, thunder["id"], 2999)  # below base
    with pytest.raises(ValueError):
        svc.place_bid(sid, thunder["id"], 3125)  # not in +50 increments
    svc.place_bid(sid, thunder["id"], 3000)
    svc.place_bid(sid, teams[1]["id"], 3050)
    lot = svc._get_player(sid, alice["id"])
    assert lot["current_bid"] == 3050
    assert lot["current_bidder_team_id"] == teams[1]["id"]


def test_bidder_cannot_bid_against_self(app, svc):
    """The current highest bidder must not be able to raise the price on
    their own wallet — only another team can push the bid up."""
    season, players, teams = _setup(app)
    sid = season["id"]
    svc.set_phase(sid, "phase_a_platinum")
    svc.nominate_next(sid)  # Alice, base 3000
    thunder, blaze = teams[0], teams[1]

    svc.place_bid(sid, thunder["id"], 3000)
    # Thunder holds the top bid: re-bidding must be rejected, at any amount.
    with pytest.raises(ValueError, match="already hold the highest bid"):
        svc.place_bid(sid, thunder["id"], 3050)
    with pytest.raises(ValueError, match="already hold the highest bid"):
        svc.place_bid(sid, thunder["id"], 4000)
    # Another team can still bid over them.
    svc.place_bid(sid, blaze["id"], 3050)
    # Now Blaze holds it: Thunder may bid again, Blaze may not.
    with pytest.raises(ValueError, match="already hold the highest bid"):
        svc.place_bid(sid, blaze["id"], 3100)
    svc.place_bid(sid, thunder["id"], 3100)
    lot = svc._get_player(sid, players[0]["id"])
    assert lot["current_bid"] == 3100
    assert lot["current_bidder_team_id"] == thunder["id"]


def _bid_ids(app, sid, player_id):
    """All bid ids for a player, lowest amount first (place_bid returns the
    player, not the bid, so tests fetch real bid ids from the table)."""
    with app.extensions["db"].read() as conn:
        rows = conn.execute(
            "SELECT id, amount FROM bids WHERE season_id = ? AND player_id = ? "
            "ORDER BY amount", (sid, player_id)).fetchall()
        return [dict(r) for r in rows]


def test_admin_delete_bid_reverts_to_previous_top(app, svc):
    """Deleting a mistaken bid on the current lot restores the previous
    highest bidder as the top bid."""
    season, players, teams = _setup(app)
    sid = season["id"]
    svc.set_phase(sid, "phase_a_platinum")
    svc.nominate_next(sid)  # Alice, base 3000
    thunder, blaze = teams[0], teams[1]

    svc.place_bid(sid, thunder["id"], 3000)
    svc.place_bid(sid, blaze["id"], 3050)
    lot = svc._get_player(sid, players[0]["id"])
    assert lot["current_bid"] == 3050 and lot["current_bidder_team_id"] == blaze["id"]
    bids = _bid_ids(app, sid, players[0]["id"])  # [(3000, id), (3050, id)]

    # admin deletes Blaze's mistaken top bid -> Thunder's bid becomes top
    svc.delete_bid(sid, bids[1]["id"])
    lot = svc._get_player(sid, players[0]["id"])
    assert lot["current_bid"] == 3000
    assert lot["current_bidder_team_id"] == thunder["id"]

    # deleting the last bid zeroes the lot
    svc.delete_bid(sid, bids[0]["id"])
    lot = svc._get_player(sid, players[0]["id"])
    assert lot["current_bid"] == 0
    assert lot["current_bidder_team_id"] is None


def test_admin_delete_bid_restricted_to_current_lot(app, svc):
    """Bids on already-sold lots cannot be deleted — only the live lot."""
    season, players, teams = _setup(app)
    sid = season["id"]
    svc.set_phase(sid, "phase_a_platinum")
    svc.nominate_next(sid)  # Alice
    svc.place_bid(sid, teams[0]["id"], 3000)
    old_bid_id = _bid_ids(app, sid, players[0]["id"])[0]["id"]
    svc.close_current(sid)  # sold to teams[0]
    svc.nominate_next(sid)  # next player (Bob)
    with pytest.raises(ValueError, match="current lot"):
        svc.delete_bid(sid, old_bid_id)
    with pytest.raises(ValueError, match="Bid not found"):
        svc.delete_bid(sid, "nonexistent")


def test_undo_delete_bid_restores_it(app, svc):
    """Undo re-inserts a deleted bid and makes it the top bid again."""
    season, players, teams = _setup(app)
    sid = season["id"]
    svc.set_phase(sid, "phase_a_platinum")
    svc.nominate_next(sid)
    thunder, blaze = teams[0], teams[1]
    svc.place_bid(sid, thunder["id"], 3000)
    svc.place_bid(sid, blaze["id"], 3050)
    top_id = _bid_ids(app, sid, players[0]["id"])[1]["id"]
    svc.delete_bid(sid, top_id)
    lot = svc._get_player(sid, players[0]["id"])
    assert lot["current_bid"] == 3000
    svc.undo_last_action(sid)  # undo the delete_bid
    lot = svc._get_player(sid, players[0]["id"])
    assert lot["current_bid"] == 3050
    assert lot["current_bidder_team_id"] == blaze["id"]


def test_bid_insufficient_purse(app, svc):
    season, _, teams = _setup(app)
    sid = season["id"]
    svc.set_phase(sid, "phase_a_platinum")
    svc.nominate_next(sid)
    with pytest.raises(ValueError):
        svc.place_bid(sid, teams[0]["id"], 99999)  # purse too low


def test_close_lot_and_undo_sale(app, svc):
    season, players, teams = _setup(app)
    sid = season["id"]
    svc.set_phase(sid, "phase_a_platinum")
    svc.nominate_next(sid)
    thunder = teams[0]
    svc.place_bid(sid, thunder["id"], 3000)
    result = svc.close_current(sid)
    assert result["sold"] is True
    assert result["team_name"] == "Thunder"
    player = svc._get_player(sid, players[0]["id"])
    assert player["status"] == "sold" and player["sold_price"] == 3000
    team = svc._get_team(sid, thunder["id"])
    assert team["wallet"] == 10000 - 3000
    assert players[0]["id"] in team["players"]

    # Undo the sale: refund, reopen lot.
    svc.undo_last_action(sid)
    player = svc._get_player(sid, players[0]["id"])
    assert player["status"] == "unsold" and player["sold_price"] == 0
    team = svc._get_team(sid, thunder["id"])
    assert team["wallet"] == 10000
    state = svc.get_state(sid)
    assert state["current_player"]["id"] == players[0]["id"]


def test_undo_bid_restores_previous_top_bid(app, svc):
    season, players, teams = _setup(app)
    sid = season["id"]
    svc.set_phase(sid, "phase_a_platinum")
    svc.nominate_next(sid)
    svc.place_bid(sid, teams[0]["id"], 3000)
    svc.place_bid(sid, teams[1]["id"], 3050)
    svc.undo_last_action(sid)  # undo the 3050 bid
    player = svc._get_player(sid, players[0]["id"])
    assert player["current_bid"] == 3000
    assert player["current_bidder_team_id"] == teams[0]["id"]


def test_phase_b_flat_price_and_incomplete_blocked(app, svc):
    season, players, teams = _setup(app, n_teams=3)
    sid = season["id"]
    # Complete team Falcon (silver manager, 7 credits) with 3 cheap silvers,
    # leaving 4 credits for Phase B bench buys.
    svc.set_phase(sid, "phase_a_silver")
    for _ in range(3):
        svc.nominate_next(sid)
        svc.place_bid(sid, teams[2]["id"], 1000)
        svc.close_current(sid)
    assert svc.get_state(sid)["phase_b_readiness"]["can_enter_phase_b"] is True

    svc.set_phase(sid, "phase_b")
    svc.nominate_next(sid)  # Alice (platinum) is the first unsold player
    with pytest.raises(ValueError):
        svc.place_bid(sid, teams[0]["id"], 200)  # incomplete team blocked
    with pytest.raises(ValueError):
        svc.place_bid(sid, teams[2]["id"], 201)  # price fixed at 200
    svc.place_bid(sid, teams[2]["id"], 200)
    result = svc.close_current(sid)
    assert result["sold"] is True
    assert len(svc._get_team(sid, teams[2]["id"])["bench"]) == 1


def test_complete_draft_fills_incomplete_and_undo(app, svc):
    season, players, teams = _setup(app)
    sid = season["id"]
    svc.set_phase(sid, "phase_a_gold")
    for _ in range(2):
        svc.nominate_next(sid)
        svc.place_bid(sid, teams[1]["id"], 2000)
        svc.close_current(sid)
    before_players = {p["id"]: dict(p) for p in svc.get_state(sid)["players"]}
    svc.complete_draft(sid)
    state = svc.get_state(sid)
    assert state["phase"] == "complete"
    for team in state["teams"]:
        if team["is_active"]:
            assert len(team["players"]) == 3, team["name"]
    # Undo completion restores everything.
    svc.undo_last_action(sid)
    state = svc.get_state(sid)
    assert state["phase"] == "phase_a_gold"
    for p in state["players"]:
        assert p["status"] == before_players[p["id"]]["status"]


# ---------------------------------------------------------------------------
# trades
# ---------------------------------------------------------------------------
def test_trade_requests_enrich_with_names(app, svc):
    """Regression: get_trade_requests_for_team crashed with "sqlite3.Row
    object has no attribute 'get'" the moment any trade existed — the lookup
    maps held raw Rows while the enrich closure called .get() on them."""
    season, players, teams = _setup(app)
    sid = season["id"]
    svc.set_phase(sid, "phase_a_platinum")
    svc.nominate_next(sid)
    svc.place_bid(sid, teams[0]["id"], 3000)
    svc.close_current(sid)  # Alice -> teams[0]
    svc.set_phase(sid, "break")
    offered = players[0]["id"]
    req = svc.request_trade(sid, teams[0]["id"], teams[1]["id"], offered)

    out = svc.get_trade_requests_for_team(sid, teams[0]["id"])
    assert out["outgoing"][0]["offered_player_name"] == "Alice"
    assert out["outgoing"][0]["to_team_name"] == teams[1]["name"]
    inc = svc.get_trade_requests_for_team(sid, teams[1]["id"])
    assert inc["incoming"][0]["offered_player_name"] == "Alice"
    assert inc["incoming"][0]["from_team_name"] == teams[0]["name"]
    assert req["id"] == inc["incoming"][0]["id"]


def test_trade_during_break_with_cash(app, svc):
    season, players, teams = _setup(app)
    sid = season["id"]
    svc.set_phase(sid, "phase_a_platinum")
    svc.nominate_next(sid)
    svc.place_bid(sid, teams[0]["id"], 3000)
    svc.close_current(sid)  # Alice -> Thunder
    svc.set_phase(sid, "break")
    offered = players[0]["id"]
    req = svc.request_trade(sid, teams[0]["id"], teams[1]["id"], offered, cash_from_target=500)
    svc.respond_trade(sid, req["id"], teams[1]["id"], "reject")
    req = svc.request_trade(sid, teams[0]["id"], teams[1]["id"], offered, cash_from_target=500)
    svc.respond_trade(sid, req["id"], teams[1]["id"], "accept")
    assert svc._get_player(sid, offered)["sold_to_team_id"] == teams[1]["id"]
    assert svc._get_team(sid, teams[1]["id"])["wallet"] == 10000 - 500
    # Undo the accepted trade.
    svc.undo_last_action(sid)
    assert svc._get_player(sid, offered)["sold_to_team_id"] == teams[0]["id"]
    assert svc._get_team(sid, teams[1]["id"])["wallet"] == 10000


# ---------------------------------------------------------------------------
# takeover
# ---------------------------------------------------------------------------
def test_takeover_blocks_manager_bid_and_restore(app, svc):
    season, players, teams = _setup(app)
    sid = season["id"]
    svc.set_phase(sid, "phase_a_platinum")
    svc.nominate_next(sid)
    svc.takeover_team(sid, teams[0]["id"], reason="manager no-show")
    with pytest.raises(ValueError):
        svc.place_bid(sid, teams[0]["id"], 3000, actor="manager")
    # Admin can bid on the taken-over team's behalf.
    svc.place_bid(sid, teams[0]["id"], 3000, actor="admin")
    assert svc._get_player(sid, players[0]["id"])["current_bidder_team_id"] == teams[0]["id"]
    svc.undo_last_action(sid)  # undo the admin bid
    svc.restore_team(sid, teams[0]["id"])
    assert svc._get_team(sid, teams[0]["id"])["control_status"] == "manager_controlled"
    # Undo the restore -> back to takeover.
    svc.undo_last_action(sid)
    assert svc._get_team(sid, teams[0]["id"])["control_status"] == "admin_takeover"


# ---------------------------------------------------------------------------
# transfers & publish
# ---------------------------------------------------------------------------
def test_admin_transfer_after_completion(app, svc):
    season, players, teams = _setup(app)
    sid = season["id"]
    # Thunder (platinum mgr): Alice + Cara + Fay
    svc.set_phase(sid, "phase_a_platinum")
    svc.nominate_next(sid)
    svc.place_bid(sid, teams[0]["id"], 3000)
    svc.close_current(sid)
    svc.set_phase(sid, "phase_a_gold")
    for _ in range(2):
        svc.nominate_next(sid)
        svc.place_bid(sid, teams[1]["id"], 2000)
        svc.close_current(sid)
    svc.set_phase(sid, "phase_a_silver")
    for _ in range(2):
        svc.nominate_next(sid)
        svc.place_bid(sid, teams[0]["id"], 1000)
        svc.close_current(sid)
    svc.nominate_next(sid)
    svc.place_bid(sid, teams[1]["id"], 1000)
    svc.close_current(sid)
    svc.complete_draft(sid)

    alice_id = players[0]["id"]
    t0_before = svc._get_team(sid, teams[0]["id"])
    t1_before = svc._get_team(sid, teams[1]["id"])
    svc.admin_transfer(sid, team_to=teams[1]["id"], player_id=alice_id,
                       team_from=teams[0]["id"], price=1000, credits=1, note="balance")
    assert svc._get_player(sid, alice_id)["sold_to_team_id"] == teams[1]["id"]
    assert svc._get_team(sid, teams[1]["id"])["wallet"] == t1_before["wallet"] - 1000
    assert svc._get_team(sid, teams[0]["id"])["wallet"] == t0_before["wallet"] + 1000
    assert svc._get_team(sid, teams[1]["id"])["credits_remaining"] == t1_before["credits_remaining"] - 1
    # Undo the transfer.
    svc.undo_last_action(sid)
    assert svc._get_player(sid, alice_id)["sold_to_team_id"] == teams[0]["id"]
    assert svc._get_team(sid, teams[0]["id"])["wallet"] == t0_before["wallet"]
    assert svc._get_team(sid, teams[1]["id"])["credits_remaining"] == t1_before["credits_remaining"]


def test_publish_and_viewer_state(app, svc):
    season, _, _ = _setup(app)
    sid = season["id"]
    svc.publish(sid, "Season Two Final")
    with app.extensions["db"].read() as conn:
        row = conn.execute("SELECT * FROM season_snapshots WHERE season_id = ?", (sid,)).fetchone()
    assert row is not None and row["name"] == "Season Two Final"
    svc.undo_last_action(sid)
    with app.extensions["db"].read() as conn:
        row = conn.execute("SELECT * FROM season_snapshots WHERE season_id = ?", (sid,)).fetchone()
    assert row is None


def test_undo_nominate_and_phase(app, svc):
    season, players, _ = _setup(app)
    sid = season["id"]
    svc.set_phase(sid, "phase_a_platinum")
    svc.undo_last_action(sid)  # undo set_phase
    assert svc.get_state(sid)["phase"] == "setup"
    svc.set_phase(sid, "phase_a_platinum")
    svc.nominate_next(sid)
    assert svc.get_state(sid)["current_player"]["id"] == players[0]["id"]
    svc.undo_last_action(sid)  # undo nominate
    assert svc.get_state(sid)["current_player"] is None


# ---------------------------------------------------------------------------
# career stats on the current lot
# ---------------------------------------------------------------------------
def test_called_up_player_carries_career_stats(app, svc):
    """The auction lot shows the called-up player's career stats."""
    season, players, _ = _setup(app)
    sid = season["id"]
    gpid = players[0]["global_player_id"]  # Alice

    with app.extensions["db"].write() as conn:
        for mk in ("mk1", "mk2"):
            conn.execute(
                "INSERT INTO match_registry (match_key, season_id, match_id, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (mk, sid, mk, "2026-01-01", "2026-01-01"))
        conn.execute(
            "INSERT INTO match_player_stats (id, match_key, season_id, player_id, player_name, "
            "team_id, team_name, matches, runs, balls_faced, dismissed, wickets, balls_bowled, "
            "runs_conceded, fours, sixes, fantasy_score) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("mps1", "mk1", sid, gpid, "Alice", "t1", "Thunder",
             3, 142, 95, 2, 5, 18, 26, 14, 4, 96),
        )
        conn.execute(
            "INSERT INTO match_player_stats (id, match_key, season_id, player_id, player_name, "
            "team_id, team_name, matches, runs, balls_faced, dismissed, wickets, balls_bowled, "
            "runs_conceded, fours, sixes, fantasy_score) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("mps2", "mk2", sid, gpid, "Alice", "t1", "Thunder",
             1, 58, 40, 0, 1, 6, 9, 6, 2, 40),
        )

    svc.set_phase(sid, "phase_a_platinum")
    svc.nominate_next(sid)  # Alice
    state = svc.get_state(sid)
    stats = state["current_player"]["stats"]
    assert stats is not None
    assert stats["matches"] == 4
    assert stats["runs"] == 200
    assert stats["balls_faced"] == 135
    assert stats["wickets"] == 6
    assert stats["balls_bowled"] == 24
    assert stats["runs_conceded"] == 35
    assert stats["fours"] == 20
    assert stats["sixes"] == 6
    assert stats["fantasy_score"] == 136
    assert round(stats["strike_rate"], 2) == round(200 * 100.0 / 135, 2)
    # avg only over dismissed innings (2); econ over balls bowled.
    assert stats["batting_average"] == 100.0
    assert round(stats["economy"], 2) == round(35 * 6.0 / 24, 2)


def test_called_up_player_without_stats_is_null(app, svc):
    """Newcomers with no recorded matches get stats=None (UI hides the block)."""
    season, players, _ = _setup(app)
    sid = season["id"]
    svc.set_phase(sid, "phase_a_platinum")
    svc.nominate_next(sid)  # Alice, no stats rows
    state = svc.get_state(sid)
    assert state["current_player"]["stats"] is None


# ---------------------------------------------------------------------------
# upcoming players in the current phase
# ---------------------------------------------------------------------------
def test_upcoming_queue_in_tier_phase(app, svc):
    """Phase A queue = unsold, un-nominated players of the tier, in rowid order,
    excluding the live lot."""
    season, players, _ = _setup(app)
    sid = season["id"]
    svc.set_phase(sid, "phase_a_platinum")
    svc.nominate_next(sid)  # Alice (platinum, first by rowid)
    state = svc.get_state(sid)
    # Platinum players: Alice(rowid 1), Dave(4), Gil(7). Alice is live.
    assert [p["name"] for p in state["upcoming"]] == ["Dave", "Gil"]
    assert state["current_player"]["name"] == "Alice"


def test_upcoming_queue_excludes_sold(app, svc):
    """Closing a lot sold removes the player from the queue."""
    season, players, teams = _setup(app)
    sid = season["id"]
    svc.set_phase(sid, "phase_a_platinum")
    svc.nominate_next(sid)  # Alice
    svc.place_bid(sid, teams[0]["id"], players[0]["base_price"])
    svc.close_current(sid)
    state = svc.get_state(sid)
    assert "Alice" not in [p["name"] for p in state["upcoming"]]
    # Dave next; Gil after him.
    assert [p["name"] for p in state["upcoming"]] == ["Dave", "Gil"]


def test_upcoming_queue_empty_outside_auction(app, svc):
    season, _, _ = _setup(app)
    sid = season["id"]
    assert svc.get_state(sid)["upcoming"] == []  # setup phase


def test_upcoming_queue_in_phase_b(app, svc):
    """Phase B queue = every unsold player (all tiers), excluding the live lot."""
    season, players, _ = _setup(app)
    sid = season["id"]
    svc.set_phase(sid, "phase_b")
    svc.nominate_next(sid)  # Alice (first unsold by rowid)
    state = svc.get_state(sid)
    names = [p["name"] for p in state["upcoming"]]
    assert "Alice" not in names
    assert len(names) == len(players) - 1
    # Order follows rowid (Bob, Cara, Dave, ...).
    assert names[0] == "Bob"
