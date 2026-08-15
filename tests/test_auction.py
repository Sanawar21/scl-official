import pytest

from .conftest import _setup


# ---------------------------------------------------------------------------
# setup
# ---------------------------------------------------------------------------
def test_season_defaults_are_s2(app, svc):
    season = svc.create_season("Season Two")
    rs = season["ruleset"]
    assert rs["tier_purses"] == {"platinum": 9000, "gold": 10000, "silver": 11000}
    assert rs["tier_base_prices"] == {"platinum": 3000, "gold": 2000, "silver": 1000}
    assert rs["tier_credits"] == {"platinum": 3, "gold": 2, "silver": 1}
    assert rs["phase_order"][0] == "platinum"
    assert season["status"] == "setup"


def test_create_team_purse_credits_from_manager_profile(app, svc):
    season, players, teams = _setup(app)
    by_name = {t["name"]: t for t in teams}
    # Platinum manager (Alice) -> 9000 purse, 8-3=5 credits
    assert by_name["Thunder"]["purse_remaining"] == 9000
    assert by_name["Thunder"]["credits_remaining"] == 5
    # Gold manager (Bob) -> 10000 purse, 8-2=6 credits
    assert by_name["Blaze"]["purse_remaining"] == 10000
    assert by_name["Blaze"]["credits_remaining"] == 6


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
    before = svc._get_team(season["id"], team["id"])["purse_remaining"]
    svc.gift_team(season["id"], team["id"], 500, "add", comment="balance skill gap")
    assert svc._get_team(season["id"], team["id"])["purse_remaining"] == before + 500
    svc.undo_last_action(season["id"])
    assert svc._get_team(season["id"], team["id"])["purse_remaining"] == before


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
    assert team["purse_remaining"] == 9000 - 3000
    assert players[0]["id"] in team["players"]

    # Undo the sale: refund, reopen lot.
    svc.undo_last_action(sid)
    player = svc._get_player(sid, players[0]["id"])
    assert player["status"] == "unsold" and player["sold_price"] == 0
    team = svc._get_team(sid, thunder["id"])
    assert team["purse_remaining"] == 9000
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
    assert svc._get_team(sid, teams[1]["id"])["purse_remaining"] == 10000 - 500
    # Undo the accepted trade.
    svc.undo_last_action(sid)
    assert svc._get_player(sid, offered)["sold_to_team_id"] == teams[0]["id"]
    assert svc._get_team(sid, teams[1]["id"])["purse_remaining"] == 10000


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
    assert svc._get_team(sid, teams[1]["id"])["purse_remaining"] == t1_before["purse_remaining"] - 1000
    assert svc._get_team(sid, teams[0]["id"])["purse_remaining"] == t0_before["purse_remaining"] + 1000
    assert svc._get_team(sid, teams[1]["id"])["credits_remaining"] == t1_before["credits_remaining"] - 1
    # Undo the transfer.
    svc.undo_last_action(sid)
    assert svc._get_player(sid, alice_id)["sold_to_team_id"] == teams[0]["id"]
    assert svc._get_team(sid, teams[0]["id"])["purse_remaining"] == t0_before["purse_remaining"]
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
