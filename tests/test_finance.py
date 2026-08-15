"""Finances + Vault wiring tests.

Covers: yield catch-up loop, M12 unlock, wallet == purse sync at every auction
money move (plus undo), auto match rewards on finalization (idempotent),
process_pending backfill, manual adjust/transfer + ledger, one-step undo, and
the S1 finance import cross-check.
"""
import importlib.util
from pathlib import Path

import pytest

from .conftest import _setup

ROOT = Path(__file__).resolve().parent.parent
PROD_DATA = ROOT / "prod-data"


@pytest.fixture()
def bank(app):
    return app.extensions["bank_service"]


@pytest.fixture()
def finance(app):
    return app.extensions["finance_service"]


@pytest.fixture()
def scorer(app):
    return app.extensions["scorer_service"]


def _wallet(bank, team):
    """Manager liquid cash for a team (the team's wallet; the purse)."""
    account = bank.account_for_owner("player", team["manager_player_id"])
    return int(account["liquid_cash"]) if account else None


# ---------------------------------------------------------------------------
# 1. yield catch-up (doc table: 2000 -> 2140 -> 2290 -> 2450 -> 2622)
# ---------------------------------------------------------------------------
def test_yield_catchup_compounds_per_match(app, bank):
    season = app.extensions["auction_service"].create_season("Yield Season")
    sid = season["id"]
    account = bank.get_or_create_account("player", "yield-owner")
    bank.adjust(account["id"], 10000, "funds")
    bank.lock_to_vault(account["id"], sid, 2000, reinvest=True)
    # Calling with match_number=4 from last_yield_match=0 must apply 4 steps.
    results = bank.apply_match_yield(sid, 4)
    assert len(results) == 4
    account = bank.get_account(account["id"])
    # 2000 -> 2140 -> 2290 -> 2450 -> 2622 (sequential 7% compounding)
    assert account["locked_capital"] == 2622
    position = bank.vault_positions(account["id"])[0]
    assert position["last_yield_match"] == 4


def test_yield_no_double_apply(app, bank):
    season = app.extensions["auction_service"].create_season("Yield Season 2")
    sid = season["id"]
    account = bank.get_or_create_account("player", "yield-owner-2")
    bank.adjust(account["id"], 10000, "funds")
    bank.lock_to_vault(account["id"], sid, 2000, reinvest=True)
    bank.apply_match_yield(sid, 2)  # -> 2290
    results = bank.apply_match_yield(sid, 2)  # already applied -> no-op
    assert results == []
    account = bank.get_account(account["id"])
    assert account["locked_capital"] == 2290


# ---------------------------------------------------------------------------
# 2. M12 unlock
# ---------------------------------------------------------------------------
def _finalized_matches(app, season_id, n):
    """Insert n finalized match_stats rows (with matching registry keys)."""
    db = app.extensions["db"]
    with db.write() as conn:
        for i in range(1, n + 1):
            key = f"{season_id}:fx{i}"
            conn.execute(
                "INSERT INTO match_registry (match_key, season_id, match_id, match_number, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (key, season_id, f"FX{i}", f"Match {i}",
                 "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"))
            conn.execute(
                "INSERT INTO match_stats (match_key, season_id, match_id, result) "
                "VALUES (?, ?, ?, ?)",
                (key, season_id, f"FX{i}", "Team won"))


def test_unlock_refuses_before_12_and_force_bypasses(app, bank):
    season = app.extensions["auction_service"].create_season("Unlock Season")
    sid = season["id"]
    account = bank.get_or_create_account("player", "unlock-owner")
    bank.adjust(account["id"], 10000, "funds")
    bank.lock_to_vault(account["id"], sid, 2000, reinvest=True)
    with pytest.raises(ValueError, match="12"):
        bank.unlock_vault(sid)
    results = bank.unlock_vault(sid, force=True)
    assert len(results) == 1 and results[0]["released"] == 2000
    account = bank.get_account(account["id"])
    assert account["liquid_cash"] == 8000 + 2000
    assert account["locked_capital"] == 0
    position = bank.vault_positions(account["id"])[0]
    assert position["unlocked"] == 1 and position["locked_capital"] == 0


def test_unlock_allowed_after_12_finalized(app, bank):
    season = app.extensions["auction_service"].create_season("Unlock Season 2")
    sid = season["id"]
    account = bank.get_or_create_account("player", "unlock-owner-2")
    bank.adjust(account["id"], 10000, "funds")
    bank.lock_to_vault(account["id"], sid, 1000, reinvest=True)
    _finalized_matches(app, sid, 12)
    results = bank.unlock_vault(sid)  # no force needed
    assert sum(r["released"] for r in results) == 1000
    txns = bank.transactions(account["id"])
    assert any(t["type"] == "vault_unlock" for t in txns)


# ---------------------------------------------------------------------------
# 3. wallet == purse from creation and at every auction money move
# ---------------------------------------------------------------------------
def test_create_team_funds_manager_wallet(app, svc):
    season, players, teams = _setup(app)
    bank = app.extensions["bank_service"]
    for team in teams:
        # The wallet IS the purse (no separate purse_remaining field).
        assert _wallet(bank, team) == team["wallet"]


def test_gift_moves_wallet_and_undo_reverses(app, svc):
    season, _, teams = _setup(app)
    bank = app.extensions["bank_service"]
    team = teams[0]
    before = _wallet(bank, team)
    svc.gift_team(season["id"], team["id"], 500, "add", comment="top up")
    team = svc._get_team(season["id"], team["id"])
    assert _wallet(bank, team) == before + 500
    assert team["wallet"] == before + 500
    svc.undo_last_action(season["id"])
    team = svc._get_team(season["id"], team["id"])
    assert _wallet(bank, team) == before
    assert team["wallet"] == before


def test_close_sold_moves_wallet_and_undo_reverses(app, svc):
    season, players, teams = _setup(app)
    bank = app.extensions["bank_service"]
    sid = season["id"]
    svc.set_phase(sid, "phase_a_platinum")
    svc.nominate_next(sid)
    thunder = teams[0]
    before = _wallet(bank, thunder)
    svc.place_bid(sid, thunder["id"], 3000)
    svc.close_current(sid)
    thunder = svc._get_team(sid, thunder["id"])
    assert _wallet(bank, thunder) == before - 3000
    assert thunder["wallet"] == before - 3000
    svc.undo_last_action(sid)  # undo the close
    thunder = svc._get_team(sid, thunder["id"])
    assert _wallet(bank, thunder) == before


def test_admin_transfer_moves_wallets(app, svc):
    season, players, teams = _setup(app)
    bank = app.extensions["bank_service"]
    sid = season["id"]
    svc.set_phase(sid, "phase_a_platinum")
    svc.nominate_next(sid)
    svc.place_bid(sid, teams[0]["id"], 3000)
    svc.close_current(sid)  # Alice -> Thunder
    svc.set_phase(sid, "complete")
    alice_id = players[0]["id"]
    t0_wallet = _wallet(bank, teams[0])
    t1_wallet = _wallet(bank, teams[1])
    svc.admin_transfer(sid, team_to=teams[1]["id"], player_id=alice_id,
                       team_from=teams[0]["id"], price=1000, credits=1, note="balance")
    t0 = svc._get_team(sid, teams[0]["id"])
    t1 = svc._get_team(sid, teams[1]["id"])
    assert _wallet(bank, t0) == t0_wallet + 1000
    assert _wallet(bank, t1) == t1_wallet - 1000
    assert t0["wallet"] == _wallet(bank, t0) and t1["wallet"] == _wallet(bank, t1)
    # Undo reverses both wallets.
    svc.undo_last_action(sid)
    t0 = svc._get_team(sid, teams[0]["id"])
    t1 = svc._get_team(sid, teams[1]["id"])
    assert _wallet(bank, t0) == t0_wallet
    assert _wallet(bank, t1) == t1_wallet


def test_delete_team_zeroes_wallet(app, svc):
    season, _, teams = _setup(app)
    bank = app.extensions["bank_service"]
    team = teams[0]
    svc.delete_team(season["id"], team["id"])
    account = bank.account_for_owner("player", team["manager_player_id"])
    assert account is not None and int(account["liquid_cash"]) == 0


# ---------------------------------------------------------------------------
# 4. on_match_finalized (auto reward + yield catch-up, idempotent)
# ---------------------------------------------------------------------------
def _register_finalized(app, scorer, season_id, match_id, match_number, team_a, team_b):
    scorer.upsert_match_registry_entry(
        season_id, match_id, match_number=match_number, between="A vs B",
        team_a_global_id=team_a, team_b_global_id=team_b)
    db = app.extensions["db"]
    with db.write() as conn:
        conn.execute(
            "INSERT INTO match_stats (match_key, season_id, match_id, result) "
            "VALUES (?, ?, ?, ?)",
            (f"{season_id}:{match_id.lower()}", season_id, match_id, "Team A won"))


def test_on_match_finalized_rewards_both_teams_and_applies_yield(app, svc, scorer, finance, bank):
    season, _, teams = _setup(app, n_teams=2)
    sid = season["id"]
    a, b = teams[0], teams[1]
    a_wallet_before = _wallet(bank, a)
    b_wallet_before = _wallet(bank, b)
    # Vault position so yield applies.
    vault_acct = bank.get_or_create_account("player", "fin-owner")
    bank.adjust(vault_acct["id"], 10000, "funds")
    bank.lock_to_vault(vault_acct["id"], sid, 2000, reinvest=True)

    _register_finalized(app, scorer, sid, "M1", "Match 1", a["id"], b["id"])
    result = finance.on_match_finalized(sid, "M1")
    assert result["finalized"] is True and result["rewarded"] is True
    # Both teams got the default 200 reward.
    assert _wallet(bank, svc._get_team(sid, a["id"])) == a_wallet_before + 200
    assert _wallet(bank, svc._get_team(sid, b["id"])) == b_wallet_before + 200
    # Yield compounded once (match 1) -> 2000 -> 2140.
    assert bank.get_account(vault_acct["id"])["locked_capital"] == 2140
    # Ledger rows exist.
    entries = finance.list_finance_entries(sid)
    rewards = [e for e in entries if e["type"] == "match_reward"]
    assert len(rewards) == 2

    # Idempotent on re-run: no double reward, no extra yield.
    result2 = finance.on_match_finalized(sid, "M1")
    assert result2["rewarded"] is False
    assert _wallet(bank, svc._get_team(sid, a["id"])) == a_wallet_before + 200
    assert bank.get_account(vault_acct["id"])["locked_capital"] == 2140


def test_on_match_finalized_not_finalized_match_noop(app, finance):
    season, _, teams = _setup(app, n_teams=2)
    result = finance.on_match_finalized(season["id"], "M99")
    assert result["finalized"] is False


# ---------------------------------------------------------------------------
# 5. process_pending backfill
# ---------------------------------------------------------------------------
def test_process_pending_backfills_rewards(app, svc, scorer, finance, bank):
    season, _, teams = _setup(app, n_teams=2)
    sid = season["id"]
    a, b = teams[0], teams[1]
    _register_finalized(app, scorer, sid, "M1", "Match 1", a["id"], b["id"])
    _register_finalized(app, scorer, sid, "M2", "Match 2", b["id"], a["id"])
    a_wallet_before = _wallet(bank, a)
    results = finance.process_pending(sid)
    assert len(results) == 2
    assert all(r["rewarded"] for r in results)
    # Two matches -> both teams got 2 x 200.
    assert _wallet(bank, svc._get_team(sid, a["id"])) == a_wallet_before + 400
    # Running again finds nothing pending.
    assert finance.process_pending(sid) == []


# ---------------------------------------------------------------------------
# 6. manual adjust / transfer + ledger
# ---------------------------------------------------------------------------
def test_post_adjust_remove_and_ledger(app, svc, finance, bank):
    season, _, teams = _setup(app)
    sid = season["id"]
    team = teams[0]
    before = _wallet(bank, team)
    finance.post_adjust(sid, team["id"], "remove", 200, "Playing with 3 men")
    assert _wallet(bank, svc._get_team(sid, team["id"])) == before - 200
    entries = finance.list_finance_entries(sid)
    assert len(entries) == 1
    e = entries[0]
    assert e["type"] == "adjust" and e["operation"] == "remove"
    assert e["amount"] == 200 and e["before_wallet"] == before and e["after_wallet"] == before - 200


def test_post_adjust_validation(app, finance):
    season, _, teams = _setup(app)
    sid = season["id"]
    with pytest.raises(ValueError, match="positive"):
        finance.post_adjust(sid, teams[0]["id"], "add", 0, "no")
    with pytest.raises(ValueError, match="Operation"):
        finance.post_adjust(sid, teams[0]["id"], "double", 100, "no")
    with pytest.raises(ValueError, match="Comment"):
        finance.post_adjust(sid, teams[0]["id"], "add", 100, "")


def test_post_adjust_overdraft_raises(app, finance):
    season, _, teams = _setup(app)
    sid = season["id"]
    with pytest.raises(ValueError, match="Insufficient"):
        finance.post_adjust(sid, teams[0]["id"], "remove", 999999, "way too much")


def test_post_transfer_and_ledger(app, svc, finance, bank):
    season, _, teams = _setup(app)
    sid = season["id"]
    from_team, to_team = teams[0], teams[1]
    f_before = _wallet(bank, from_team)
    t_before = _wallet(bank, to_team)
    finance.post_transfer(sid, from_team["id"], to_team["id"], 250, "Sub cash")
    assert _wallet(bank, svc._get_team(sid, from_team["id"])) == f_before - 250
    assert _wallet(bank, svc._get_team(sid, to_team["id"])) == t_before + 250
    entries = finance.list_finance_entries(sid)
    assert entries[0]["type"] == "transfer"
    assert entries[0]["amount"] == 250


# ---------------------------------------------------------------------------
# 7. one-step undo
# ---------------------------------------------------------------------------
def test_undo_adjust_and_transfer(app, svc, finance, bank):
    season, _, teams = _setup(app)
    sid = season["id"]
    from_team, to_team = teams[0], teams[1]
    f_before = _wallet(bank, from_team)
    t_before = _wallet(bank, to_team)
    finance.post_adjust(sid, from_team["id"], "remove", 100, "fine")
    finance.post_transfer(sid, from_team["id"], to_team["id"], 50, "sub")
    # Undo the transfer first (most recent).
    result = finance.undo_last_finance_entry(sid)
    assert result["type"] == "transfer"
    assert _wallet(bank, svc._get_team(sid, from_team["id"])) == f_before - 100
    assert _wallet(bank, svc._get_team(sid, to_team["id"])) == t_before
    # Then the adjust.
    finance.undo_last_finance_entry(sid)
    assert _wallet(bank, svc._get_team(sid, from_team["id"])) == f_before
    # Second undo of same entry is a no-op; nothing left -> raises.
    with pytest.raises(ValueError, match="Nothing to undo"):
        finance.undo_last_finance_entry(sid)


def test_undo_match_reward(app, svc, scorer, finance, bank):
    season, _, teams = _setup(app, n_teams=2)
    sid = season["id"]
    a, b = teams[0], teams[1]
    _register_finalized(app, scorer, sid, "M1", "Match 1", a["id"], b["id"])
    finance.on_match_finalized(sid, "M1")
    a_wallet = _wallet(bank, svc._get_team(sid, a["id"]))
    b_wallet = _wallet(bank, svc._get_team(sid, b["id"]))
    finance.undo_last_finance_entry(sid)  # undoes one of the two rewards
    # Exactly one of the two teams was debited back by the reward amount.
    a_after = _wallet(bank, svc._get_team(sid, a["id"]))
    b_after = _wallet(bank, svc._get_team(sid, b["id"]))
    assert (a_after, b_after) in ((a_wallet - 200, b_wallet), (a_wallet, b_wallet - 200))


def test_undo_transfer_overdraft_guard(app, finance):
    """Undoing a transfer would take a wallet negative -> must refuse."""
    season, _, teams = _setup(app)
    sid = season["id"]
    finance.post_transfer(sid, teams[0]["id"], teams[1]["id"], 500, "sub")
    # Zero out team B's wallet so the undo (debit 500) would overdraft.
    bank = app.extensions["bank_service"]
    db = app.extensions["db"]
    b_account = bank.account_for_owner("player", teams[1]["manager_player_id"])
    with db.write() as conn:
        conn.execute("UPDATE bank_accounts SET liquid_cash = 0 WHERE id = ?", (b_account["id"],))
    with pytest.raises(ValueError, match="Insufficient"):
        finance.undo_last_finance_entry(sid)


# ---------------------------------------------------------------------------
# 8. S1 import (--phase finance) cross-check + wallet seed
# ---------------------------------------------------------------------------
def _load_import_module():
    path = ROOT / "scripts" / "import_prod.py"
    spec = importlib.util.spec_from_file_location("import_prod", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.skipif(not PROD_DATA.is_dir(), reason="prod-data not present")
def test_s1_finance_import_cross_check_and_wallet_seed(app, tmp_path):
    from app.db import Database
    db_path = str(tmp_path / "s1.db")
    db = Database(db_path)
    db.bootstrap()
    imp = _load_import_module()
    imp.import_core(db, PROD_DATA)
    imp.import_stats(db, PROD_DATA)
    summary = imp.import_finance(db, PROD_DATA)
    assert summary["cross_check_mismatches"] == []
    with db.read() as conn:
        n = conn.execute("SELECT COUNT(*) FROM season_finance_entries").fetchone()[0]
        assert n == 44
        teams = conn.execute(
            "SELECT t.name, t.manager_player_id FROM teams t "
            "WHERE t.season_id = 'season-1'").fetchall()
        wallets = {}
        for t in teams:
            row = conn.execute(
                "SELECT liquid_cash FROM bank_accounts WHERE owner_type='player' "
                "AND owner_id = ?", (t["manager_player_id"],)).fetchone()
            wallets[t["name"]] = int(row["liquid_cash"]) if row else None
    # Wallets are seeded with the S1 final purses (the purse IS the wallet now).
    expected = {"MHK Royales": 2285, "Naan CC": 1145,
                "Pandiya Associates": 1365, "Quadra Nemesis": 2885}
    for name, purse in expected.items():
        assert wallets[name] == purse, f"{name}: wallet {wallets[name]} != expected {purse}"


# ---------------------------------------------------------------------------
# 9. route smoke
# ---------------------------------------------------------------------------
def test_route_smoke(app, svc, scorer, finance):
    season, _, teams = _setup(app)
    sid = season["id"]
    # Register a match so the season appears in match_seasons (and finances).
    scorer.upsert_match_registry_entry(
        sid, "M1", team_a_global_id=teams[0]["id"], team_b_global_id=teams[1]["id"])
    c = app.test_client()
    # Public board + ledger.
    assert c.get("/finances").status_code == 200
    assert c.get(f"/finances/{sid}").status_code == 200
    # Admin page is login-gated.
    assert c.get("/admin/finances").status_code == 302
    c.post("/auth/login", data={"username": "admin", "password": "admin123"})
    r = c.get("/admin/finances")
    assert r.status_code == 200
    body = r.data.decode()
    assert "Budget Board" in body and "Ledger" in body
    assert "Process pending" in body and "Undo last entry" in body
    # Manager account shows unlock status.
    auth = app.extensions["auth_service"]
    manager_gp = teams[0]["manager_player_id"]
    user = auth.signup("mgruser", "pass1234", "Mgr User")
    auth.link_user_to_player(user["id"], manager_gp)
    bank = app.extensions["bank_service"]
    account = bank.account_for_owner("player", manager_gp)
    bank.lock_to_vault(account["id"], sid, 100, reinvest=True)
    c.get("/auth/logout")
    c.post("/auth/login", data={"username": "mgruser", "password": "pass1234"})
    r = c.get("/account")
    assert r.status_code == 200
    assert "Locked until M12" in r.data.decode()
