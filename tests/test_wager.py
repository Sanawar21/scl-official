import pytest

from tests.conftest import _setup


@pytest.fixture()
def wager(app):
    return app.extensions["wager_service"]


@pytest.fixture()
def bank(app):
    return app.extensions["bank_service"]


def _linked_user(app, username, gp_id, funds=10000):
    """Create + link a user, fund their liquid cash, return the session-shaped user dict."""
    auth = app.extensions["auth_service"]
    user = auth.signup(username, "pass1234", username)
    auth.link_user_to_player(user["id"], gp_id)
    account = app.extensions["bank_service"].get_or_create_account("player", gp_id)
    app.extensions["bank_service"].adjust(account["id"], funds, "test funds")
    return auth.get_by_username(username)


def _liquid(app, gp_id):
    account = app.extensions["bank_service"].account_for_owner("player", gp_id)
    return account["liquid_cash"] if account else 0


def _open_market(app, wager, creator, side="Yes", amount=200, estimate=50):
    """Create a market and run it to 'vetted' (calibrated + finalized)."""
    w = wager.create_wager(creator, "Royales win Match 1", "", "Yes", "No", side, amount)
    wager.calibrate(w["id"], "admin", estimate)
    return wager.finalize_calibration(w["id"], "admin")


def test_create_wager_with_first_stake(app, wager, bank):
    season, players, _ = _setup(app, n_teams=2)
    gp = players[0]["global_player_id"]
    user = _linked_user(app, "alice", gp)
    before = _liquid(app, gp)

    w = wager.create_wager(user, "Royales win Match 1", "desc", "Yes", "No", "Yes", 500)

    assert w["status"] == "proposed"
    assert not w["accepting_bets"]
    assert w["initiator_name"] == "alice"
    assert len(w["bets"]) == 1 and w["bets"][0]["amount"] == 500
    assert _liquid(app, gp) == before - 500
    txns = bank.transactions(bank.account_for_owner("player", gp)["id"])
    assert txns[0]["type"] == "wager_stake"


def test_betting_blocked_before_finalize(app, wager):
    season, players, _ = _setup(app, n_teams=2)
    gp = players[1]["global_player_id"]
    user = _linked_user(app, "bob", gp)
    w = wager.create_wager(user, "Toss market", "", "Heads", "Tails", "Heads", 100)
    wager.calibrate(w["id"], "admin", 50)
    other = _linked_user(app, "carol", players[2]["global_player_id"])
    with pytest.raises(ValueError):
        wager.place_bet(other, w["id"], "Tails", 100)


def test_calibration_consensus_is_average(app, wager):
    season, players, _ = _setup(app, n_teams=2)
    user = _linked_user(app, "alice", players[0]["global_player_id"])
    w = wager.create_wager(user, "Underdog market", "", "Yes", "No", "No", 100)
    wager.calibrate(w["id"], "admin1", 20)
    w = wager.calibrate(w["id"], "admin2", 30)
    assert w["status"] == "calibrating"
    assert w["house_probability"] == 25.0
    assert len(w["calibration_estimates"]) == 2


def test_finalize_opens_betting_and_pools_update(app, wager):
    season, players, _ = _setup(app, n_teams=2)
    gp = players[0]["global_player_id"]
    user = _linked_user(app, "alice", gp)
    w = _open_market(app, wager, user)
    assert w["status"] == "vetted" and w["accepting_bets"]

    other = _linked_user(app, "bob", players[1]["global_player_id"])
    w = wager.place_bet(other, w["id"], "No", 300)
    assert w["yes_total"] == 200 and w["no_total"] == 300
    assert w["pot"] == 500


def test_insufficient_balance_rejected(app, wager):
    season, players, _ = _setup(app, n_teams=2)
    # players[2] is not a manager, so no purse tops up their account.
    gp = players[2]["global_player_id"]
    user = _linked_user(app, "bob", gp, funds=50)
    with pytest.raises(ValueError):
        wager.create_wager(user, "Too rich", "", "Yes", "No", "Yes", 100)


def test_requires_linked_account(app, wager):
    season, players, _ = _setup(app, n_teams=2)
    auth = app.extensions["auth_service"]
    unlinked = auth.signup("ghost", "pass1234", "Ghost")
    with pytest.raises(ValueError):
        wager.create_wager(unlinked, "No link", "", "Yes", "No", "Yes", 100)


def test_veto_refunds_all_stakes(app, wager):
    season, players, _ = _setup(app, n_teams=2)
    gp = players[0]["global_player_id"]
    user = _linked_user(app, "alice", gp)
    before = _liquid(app, gp)
    w = wager.create_wager(user, "Risky market", "", "Yes", "No", "Yes", 300)
    assert _liquid(app, gp) == before - 300         # stake deducted
    w = wager.veto(w["id"], "admin", "threatens solvency")
    assert w["status"] == "voided" and w["veto_reason"] == "threatens solvency"
    assert _liquid(app, gp) == before               # stake refunded in full
    assert w["bets"][0]["status"] == "refunded"


def test_freeze_blocks_and_unfreeze_reopens(app, wager):
    season, players, _ = _setup(app, n_teams=2)
    user = _linked_user(app, "alice", players[0]["global_player_id"])
    w = _open_market(app, wager, user)
    other = _linked_user(app, "bob", players[1]["global_player_id"])
    w = wager.freeze(w["id"], "admin")
    assert w["status"] == "frozen" and not w["accepting_bets"]
    with pytest.raises(ValueError):
        wager.place_bet(other, w["id"], "No", 100)
    w = wager.unfreeze(w["id"], "admin")
    assert w["status"] == "vetted" and w["accepting_bets"]
    wager.place_bet(other, w["id"], "No", 100)


def test_house_injection_uses_house_funds(app, wager, bank):
    season, players, _ = _setup(app, n_teams=2)
    house = bank.get_or_create_account("house", "house")
    bank.adjust(house["id"], 5000, "house top-up")
    user = _linked_user(app, "alice", players[0]["global_player_id"])
    w = _open_market(app, wager, user)
    w = wager.inject_house(w["id"], "admin", 1000)
    assert w["house_injected"] == 1000
    assert bank.get_account(house["id"])["liquid_cash"] == 4000
    with pytest.raises(ValueError):
        wager.inject_house(w["id"], "admin", 99999)


def test_resolve_proportional_split(app, wager):
    """Fat pot: winners get fair odds, house takes the remainder."""
    season, players, _ = _setup(app, n_teams=2)
    house = bank = wager.bank
    house_acct = bank.get_or_create_account("house", "house")
    alice = _linked_user(app, "alice", players[0]["global_player_id"])
    bob = _linked_user(app, "bob", players[1]["global_player_id"])
    carol = _linked_user(app, "carol", players[2]["global_player_id"])
    alice_before = _liquid(app, players[0]["global_player_id"])
    w = _open_market(app, wager, alice, side="Yes", amount=120, estimate=50)

    wager.place_bet(bob, w["id"], "Yes", 80)
    wager.place_bet(carol, w["id"], "No", 300)
    # Yes: 200 total. No: 300. fair(Yes)=2x -> guaranteed 400 <= 500.
    # Winners get fair odds: 120*2=240, 80*2=160. House keeps 100.
    w = wager.resolve(w["id"], "admin", "Yes")

    assert w["status"] == "resolved" and w["winning_side"] == "Yes"
    by_user = {b["username"]: b for b in w["bets"]}
    assert by_user["alice"]["payout"] == 240
    assert by_user["bob"]["payout"] == 160
    assert by_user["carol"]["payout"] == 0
    assert _liquid(app, players[0]["global_player_id"]) == alice_before - 120 + 240
    assert bank.get_account(house_acct["id"])["liquid_cash"] == 100


def test_resolve_house_guarantee_topup(app, wager, bank):
    """Thin pool: the House tops up so the underdog gets fair odds (4x at 25%)."""
    season, players, _ = _setup(app, n_teams=2)
    house = bank.get_or_create_account("house", "house")
    bank.adjust(house["id"], 5000, "house top-up")

    alice = _linked_user(app, "alice", players[0]["global_player_id"])
    bob = _linked_user(app, "bob", players[1]["global_player_id"])
    # p(No) = 25 -> fair(No) = 4x. Thin No pool.
    w = _open_market(app, wager, alice, side="Yes", amount=200, estimate=25)
    wager.place_bet(bob, w["id"], "No", 100)
    # pot = 300; guaranteed(No) = 100 * 4 = 400 > 300 -> top-up 100.
    # Bob gets 100 * 4 = 400. House rake = 0 (spent 100 to cover shortfall).
    w = wager.resolve(w["id"], "admin", "No")

    by_user = {b["username"]: b for b in w["bets"]}
    assert by_user["bob"]["payout"] == 400
    assert by_user["alice"]["payout"] == 0
    assert bank.get_account(house["id"])["liquid_cash"] == 5000 - 100
    assert w["history"][-1]["note"].startswith("No won")


def test_resolve_infinite_house_covers_shortfall(app, wager, bank):
    """House has infinite bankroll — always pays fair odds."""
    season, players, _ = _setup(app, n_teams=2)
    house = bank.get_or_create_account("house", "house")

    alice = _linked_user(app, "alice", players[0]["global_player_id"])
    bob = _linked_user(app, "bob", players[1]["global_player_id"])
    w = _open_market(app, wager, alice, side="Yes", amount=200, estimate=25)
    wager.place_bet(bob, w["id"], "No", 100)
    # pot = 300; guaranteed(No) = 100 * 4 = 400 > 300 -> house tops up 100.
    # Bob gets 400 (fair odds). House rake = 300 + 0 + 100 - 400 = 0.
    w = wager.resolve(w["id"], "admin", "No")

    by_user = {b["username"]: b for b in w["bets"]}
    assert by_user["bob"]["payout"] == 400
    assert by_user["alice"]["payout"] == 0
    assert w["status"] == "resolved"


def test_void_refunds_100_percent(app, wager):
    season, players, _ = _setup(app, n_teams=2)
    alice = _linked_user(app, "alice", players[0]["global_player_id"])
    bob = _linked_user(app, "bob", players[1]["global_player_id"])
    w = _open_market(app, wager, alice, side="Yes", amount=200, estimate=50)
    wager.place_bet(bob, w["id"], "No", 150)
    # Capture balances after stakes are placed (managers start with purse + test funds).
    alice_before = _liquid(app, players[0]["global_player_id"])
    bob_before  = _liquid(app, players[1]["global_player_id"])
    w = wager.void(w["id"], "admin", "ambiguous condition")
    assert w["status"] == "voided" and w["void_reason"] == "ambiguous condition"
    # Both stakes refunded 100% (back to the level before the stakes).
    assert _liquid(app, players[0]["global_player_id"]) == alice_before + 200
    assert _liquid(app, players[1]["global_player_id"]) == bob_before + 150
    assert all(b["status"] == "refunded" and b["payout"] == b["amount"] for b in w["bets"])


def test_lifecycle_guards(app, wager, bank):
    season, players, _ = _setup(app, n_teams=2)
    house = bank.get_or_create_account("house", "house")
    bank.adjust(house["id"], 5000, "house top-up")
    alice = _linked_user(app, "alice", players[0]["global_player_id"])
    bob = _linked_user(app, "bob", players[1]["global_player_id"])
    w = _open_market(app, wager, alice, side="Yes", amount=200, estimate=50)

    wager.resolve(w["id"], "admin", "Yes")
    with pytest.raises(ValueError):
        wager.resolve(w["id"], "admin", "Yes")          # resolve twice
    with pytest.raises(ValueError):
        wager.place_bet(bob, w["id"], "No", 100)        # bet after resolve
    with pytest.raises(ValueError):
        wager.void(w["id"], "admin", "late")            # void after resolve

    w2 = wager.create_wager(bob, "Uncalibrated", "", "Yes", "No", "Yes", 100)
    with pytest.raises(ValueError):
        wager.resolve(w2["id"], "admin", "Yes")         # resolve before calibration

    # Veto is the pre-open financial gate; an OPEN market is cancelled via void.
    w3 = wager.create_wager(bob, "Open market", "", "Yes", "No", "Yes", 100)
    w3 = wager.calibrate(w3["id"], "admin", 40)
    w3 = wager.finalize_calibration(w3["id"], "admin")
    with pytest.raises(ValueError):
        wager.veto(w3["id"], "admin", "late veto")


def test_route_board_and_admin_pages(app):
    c = app.test_client()
    assert c.get("/wagers").status_code == 200
    assert c.get("/wagers/admin").status_code == 302  # admin required
    c.post("/auth/login", data={"username": "admin", "password": "admin123"})
    assert c.get("/wagers/admin").status_code == 200


def test_route_admin_remove_bet(app, wager):
    """The admin endpoint removes a bet; non-admins are redirected away."""
    season, players, _ = _setup(app, n_teams=2)
    alice = _linked_user(app, "alice", players[0]["global_player_id"])
    bob = _linked_user(app, "bob", players[1]["global_player_id"])
    w = _open_market(app, wager, alice, side="Yes", amount=200, estimate=50)
    w = wager.place_bet(bob, w["id"], "No", 100)
    bet_id = next(b for b in w["bets"] if b["username"] == "bob")["id"]

    c = app.test_client()
    # Not logged in -> redirect.
    r = c.post(f"/wagers/admin/{w['id']}/bets/{bet_id}/remove")
    assert r.status_code == 302
    # Admin removes it.
    c.post("/auth/login", data={"username": "admin", "password": "admin123"})
    r = c.post(f"/wagers/admin/{w['id']}/bets/{bet_id}/remove")
    assert r.status_code == 302
    fresh = wager.get_wager(w["id"])
    assert fresh["pot"] == 200
    assert sum(1 for b in fresh["bets"] if b["status"] == "open") == 1
    # The admin page lists bets with a remove form for open ones.
    body = c.get("/wagers/admin").data.decode()
    assert "Remove bet" in body
    assert "1 open" in body


def test_remove_bet_refunds_stake_and_recomputes_pools(app, wager, bank):
    """Admin can remove a single open bet; the stake is refunded and the
    pools/pot/house coverage recompute immediately."""
    season, players, _ = _setup(app, n_teams=2)
    alice = _linked_user(app, "alice", players[0]["global_player_id"])
    bob = _linked_user(app, "bob", players[1]["global_player_id"])
    bob_before = _liquid(app, players[1]["global_player_id"])
    w = _open_market(app, wager, alice, side="Yes", amount=200, estimate=25)
    w = wager.place_bet(bob, w["id"], "No", 100)
    assert w["pot"] == 300
    bob_bet = next(b for b in w["bets"] if b["username"] == "bob")

    w = wager.remove_bet(w["id"], bob_bet["id"], "admin")
    assert w["pot"] == 200                       # bob's 100 left the pot
    assert w["no_total"] == 0
    assert sum(1 for b in w["bets"] if b["status"] == "open") == 1
    assert _liquid(app, players[1]["global_player_id"]) == bob_before   # refunded
    removed = next(b for b in w["bets"] if b["id"] == bob_bet["id"])
    assert removed["status"] == "refunded"
    assert removed["payout"] == 100
    assert any(h["action"] == "remove_bet" for h in w["history"])

    # Removing it again is rejected (already refunded).
    with pytest.raises(ValueError):
        wager.remove_bet(w["id"], bob_bet["id"], "admin")


def test_remove_bet_guards(app, wager):
    """Only open bets can be removed, and only before resolution/void."""
    season, players, _ = _setup(app, n_teams=2)
    alice = _linked_user(app, "alice", players[0]["global_player_id"])
    bob = _linked_user(app, "bob", players[1]["global_player_id"])
    w = _open_market(app, wager, alice, side="Yes", amount=200, estimate=25)
    w = wager.place_bet(bob, w["id"], "No", 100)
    bet_id = next(b for b in w["bets"] if b["username"] == "bob")["id"]
    # Unknown bet id.
    with pytest.raises(ValueError):
        wager.remove_bet(w["id"], "nope", "admin")
    # After resolution the market is closed to bet removal.
    bank = app.extensions["bank_service"]
    bank.get_or_create_account("house", "house")
    bank.adjust(bank.get_or_create_account("house", "house")["id"], 5000, "house funds")
    w = wager.resolve(w["id"], "admin", "Yes")
    with pytest.raises(ValueError):
        wager.remove_bet(w["id"], bet_id, "admin")


def test_house_coverage_computed_live(app, wager, bank):
    """The automatic guarantee shows how much the House covers per side, and
    adjusts as stakes land on either side."""
    season, players, _ = _setup(app, n_teams=2)
    bank.get_or_create_account("house", "house")
    alice = _linked_user(app, "alice", players[0]["global_player_id"])
    bob = _linked_user(app, "bob", players[1]["global_player_id"])
    # p(No) = 25 -> fair(Yes) = 100/75 = 1.33x, fair(No) = 4x.
    w = _open_market(app, wager, alice, side="Yes", amount=200, estimate=25)
    assert w["pot"] == 200
    # If Yes wins: guaranteed 200*1.33 = 267 > pot 200 -> House covers 67.
    # If No wins: no No stakes -> covers 0.
    assert w["cover_a"] == 67   # int(round(200 * 100/75)) - 200
    assert w["cover_b"] == 0

    # Bob stakes 100 on No: pot 300.
    w = wager.place_bet(bob, w["id"], "No", 100)
    assert w["pot"] == 300
    # If No wins: guaranteed 100*4 = 400 > 300 -> covers 100. The bigger pot now
    # also fully covers the Yes guarantee (267 < 300).
    assert w["cover_a"] == 0
    assert w["cover_b"] == 100

    # Alice adds 100 more on Yes: pot 400 — the No guarantee (100*4 = 400) is
    # now exactly covered too, so the House is off the hook on both sides.
    wager.place_bet(alice, w["id"], "Yes", 100)
    w = wager.get_wager(w["id"])
    assert w["pot"] == 400
    assert w["cover_a"] == 0
    assert w["cover_b"] == 0

    # The live endpoints expose the same numbers.
    c = app.test_client()
    live = c.get("/wagers/live").get_json()
    entry = next(x for x in live if x["id"] == w["id"])
    assert entry["cover_a"] == 0 and entry["cover_b"] == 0
    single = c.get(f"/wagers/{w['id']}/live").get_json()
    assert single["cover_a"] == 0 and single["cover_b"] == 0
    assert single["pot"] == 400
