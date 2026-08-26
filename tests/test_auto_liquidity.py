"""Auto-account liquidity system (S2 economy).

Auto accounts must stay as LIQUID as possible:
- all income (match rewards, admin adds) lands in liquid cash;
- their liquid is swept into the vault ONLY at the very last moment —
  immediately before a yield release — so it earns that release's step;
- their vault positions ALWAYS run manual harvest (reinvest=0), so the yield
  pays straight back out into spendable liquid instead of compounding inside
  the vault;
- manual accounts are completely unaffected.
"""
from datetime import datetime, timedelta, timezone

import pytest

from tests.conftest import _setup


@pytest.fixture()
def bank(app):
    return app.extensions["bank_service"]


@pytest.fixture()
def finance(app):
    return app.extensions["finance_service"]


@pytest.fixture()
def season_id(app):
    """A real season row (season_finance_entries has an FK to seasons)."""
    return app.extensions["auction_service"].create_season("Yield Season")["id"]


@pytest.fixture()
def finalized_season_id(app, season_id):
    """Season with M1 finalized (needed for scheduled releases)."""
    _finalize_match(app, season_id, 1)
    return season_id


def _auto_account(bank, owner_id="gp-auto"):
    acct = bank.get_or_create_account("player", owner_id)
    assert acct["auto_vault"] == 1  # unlinked wallets default to auto
    return acct


def _finalize_match(app, season_id, match_number):
    """Insert a finalized M<n> in registry + stats (CSV-upload equivalent)."""
    now = datetime.now(timezone.utc).isoformat()
    key = f"{season_id}:m{match_number}"
    with app.extensions["db"].write() as conn:
        conn.execute(
            "INSERT INTO match_registry (match_key, season_id, match_id, match_number, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (key, season_id, f"M{match_number}", f"Match {match_number}", now, now))
        conn.execute(
            "INSERT INTO match_stats (match_key, season_id, match_id, result) "
            "VALUES (?, ?, ?, 'done')",
            (key, season_id, f"M{match_number}"))


# ── TC1: income stays liquid for auto accounts ──────────────────────────────
def test_tc1_income_stays_liquid(app, bank, finance):
    acct = _auto_account(bank)
    bank.credit(acct["id"], 250, "Match reward (M1)", tx_type="match_reward")
    bank.adjust(acct["id"], 500, "umpire duty", tx_type="admin_adjust")
    acct = bank.get_account(acct["id"])
    assert acct["liquid_cash"] == 750
    assert acct["locked_capital"] == 0


# ── TC2 + TC3: release sweeps all liquid just-before-yield; manual harvest ──
def test_tc2_tc3_release_sweeps_then_harvests_to_liquid(app, bank, finance, season_id):
    acct = _auto_account(bank)
    bank.adjust(acct["id"], 5000, "funding")

    result = finance.release_yield_for_match(season_id, 1)

    # TC2: sweep locked ALL 5,000 into a manual-harvest position.
    acct = bank.get_account(acct["id"])
    assert result["swept"] == 5000
    positions = bank.vault_positions(acct["id"])
    assert len(positions) == 1
    pos = positions[0]
    assert pos["principal"] == 5000 and pos["locked_capital"] == 5000
    assert pos["reinvest"] == 0, "auto positions must be manual-harvest"
    assert acct["locked_capital"] == 5000

    # TC3: the step paid OUT to liquid (7% of 5,000 = 350); principal intact.
    acct = bank.get_account(acct["id"])
    assert result["yield_total"] == 350
    assert acct["liquid_cash"] == 350
    assert acct["locked_capital"] == 5000
    positions = bank.vault_positions(acct["id"])
    assert positions[0]["season_id"] == season_id


# ── TC4: next cycle — new income joins the sweep and earns interest too ─────
def test_tc4_second_cycle_new_income_earns_interest(app, bank, finance, season_id):
    acct = _auto_account(bank)
    bank.adjust(acct["id"], 5000, "funding")
    finance.release_yield_for_match(season_id, 1)  # liquid -> 350

    bank.adjust(acct["id"], 250, "Match reward (M2)", tx_type="match_reward")
    assert bank.get_account(acct["id"])["liquid_cash"] == 600

    finance.release_yield_for_match(season_id, 2)

    acct = bank.get_account(acct["id"])
    pos = bank.vault_positions(acct["id"])[0]
    # Sweep 600 -> principal 5,600; manual step pays 7% of PRINCIPAL = 392.
    assert pos["principal"] == 5600
    assert acct["locked_capital"] == 5600
    assert acct["liquid_cash"] == 392


# ── TC5: legacy compound position on an auto account flips to manual ────────
def test_tc5_compound_position_migrates_to_manual(app, bank, finance, season_id):
    acct = _auto_account(bank)
    bank.adjust(acct["id"], 5000, "funding")
    bank.lock_to_vault(acct["id"], season_id, 2000, reinvest=True)  # old behaviour
    pos = bank.vault_positions(acct["id"])[0]
    assert pos["reinvest"] == 1

    bank.adjust(acct["id"], 1000, "extra income")  # stays liquid
    finance.release_yield_for_match(season_id, 1)

    pos = bank.vault_positions(acct["id"])[0]
    acct = bank.get_account(acct["id"])
    assert pos["reinvest"] == 0, "compound position must migrate to manual"
    # Sweep takes ALL liquid: 3000 leftover + 1000 income -> principal 6,000;
    # harvest pays 7% of 6,000 = 420 back out to liquid.
    assert acct["liquid_cash"] == 420
    assert acct["locked_capital"] == 6000, "nothing compounded into locked"


# ── TC6: manual accounts fully unaffected ───────────────────────────────────
def test_tc6_manual_accounts_unaffected(app, bank, finance):
    """Manual accounts: no sweep of their free liquid, owner's reinvest flag
    preserved, compound still compounds into locked, manual-harvest pays to
    liquid."""
    season, players, _ = _setup(app, n_teams=2)
    sid = season["id"]
    a = bank.account_for_owner("player", players[0]["global_player_id"])
    b = bank.account_for_owner("player", players[1]["global_player_id"])
    assert a["auto_vault"] == 0

    # A runs a COMPOUND position; B runs a MANUAL-HARVEST position.
    bank.lock_to_vault(a["id"], sid, 2000, reinvest=True)
    bank.lock_to_vault(b["id"], sid, 1000, reinvest=False)

    finance.release_yield_for_match(sid, 1)

    # A: liquid untouched (no sweep), yield compounded INTO the vault.
    a_after = bank.get_account(a["id"])
    pos_a = bank.vault_positions(a["id"])[0]
    assert a_after["liquid_cash"] == 8000, "manual liquid must not be swept"
    assert pos_a["locked_capital"] == 2140  # 2000 + 7% compounded
    assert pos_a["reinvest"] == 1, "owner's flag preserved"

    # B: manual-harvest paid OUT to liquid; principal intact in the vault.
    b_after = bank.get_account(b["id"])
    pos_b = bank.vault_positions(b["id"])[0]
    assert b_after["liquid_cash"] == 9070  # 9000 remaining + 70 harvested
    assert pos_b["locked_capital"] == 1000
    assert pos_b["reinvest"] == 0


# ── TC7: zero-liquid auto account at release is a clean no-op sweep ─────────
def test_tc7_zero_liquid_auto_still_harvests(app, bank, finance, season_id):
    acct = _auto_account(bank)
    bank.adjust(acct["id"], 5000, "funding")
    finance.release_yield_for_match(season_id, 1)  # liquid -> 350

    bank.adjust(acct["id"], -350, "spend it all")  # liquid -> 0
    finance.release_yield_for_match(season_id, 2)

    acct = bank.get_account(acct["id"])
    assert acct["liquid_cash"] == 350, "harvest still paid out"
    pos = bank.vault_positions(acct["id"])[0]
    assert pos["principal"] == 5000 and pos["locked_capital"] == 5000


# ── TC8: idempotency — no double release, no double pay ─────────────────────
def test_tc8_no_double_release_or_double_pay(app, bank, finance, season_id):
    acct = _auto_account(bank)
    bank.adjust(acct["id"], 5000, "funding")
    finance.release_yield_for_match(season_id, 1)
    first = bank.get_account(acct["id"])

    with pytest.raises(ValueError):
        finance.release_yield_for_match(season_id, 1)
    with pytest.raises(ValueError):
        finance.release_yield_for_match(season_id, 0)

    assert bank.get_account(acct["id"]) == first
    entries = finance.list_finance_entries(season_id)
    releases = [e for e in entries if e["type"] == "yield_release" and not e.get("undone_at")]
    assert len(releases) == 1


# ── TC9: bulk release across several matches — one sweep, N harvest steps ───
def test_tc9_bulk_release_single_sweep_multi_steps(app, bank, finance, season_id):
    acct = _auto_account(bank)
    bank.adjust(acct["id"], 5000, "funding")

    result = finance.release_yield(season_id, 3)  # steps M1..M3

    acct = bank.get_account(acct["id"])
    assert result["steps"] == 3
    assert result["swept"] == 5000
    # Manual harvest: 3 x 7% of principal 5,000 = 1,050 total to liquid.
    assert result["yield_total"] == 1050
    assert acct["liquid_cash"] == 1050
    pos = bank.vault_positions(acct["id"])[0]
    assert pos["principal"] == 5000 and pos["locked_capital"] == 5000
    assert pos["last_yield_match"] == 3


# ── TC10: scheduled release behaves exactly like a manual one ───────────────
def test_tc10_scheduler_release_same_behaviour(app, bank, finance, finalized_season_id):
    sid = finalized_season_id

    acct = _auto_account(bank, owner_id="gp-sched")
    bank.adjust(acct["id"], 4000, "funding")

    # Schedule directly in the past so the scheduler picks it up immediately.
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    with app.extensions["db"].write() as conn:
        conn.execute(
            "INSERT INTO yield_schedules (id, season_id, match_number, scheduled_at, "
            "status, created_by, created_at) VALUES ('sched1', ?, 1, ?, 'scheduled', "
            "'test', ?)", (sid, past, past))

    executed = finance.check_and_execute_due_schedules()
    assert len(executed) == 1 and executed[0]["yield_total"] == 280  # 7% of 4k

    acct = bank.get_account(acct["id"])
    assert acct["liquid_cash"] == 280
    assert acct["locked_capital"] == 4000
    pos = bank.vault_positions(acct["id"])[0]
    assert pos["reinvest"] == 0
    rows = finance.list_yield_schedules(sid)
    assert rows[0]["status"] == "executed"


# ── TC11: season-end unlock returns accumulated capital to liquid ───────────
def test_tc11_season_end_unlock_restores_liquid(app, bank, finance, season_id):
    acct = _auto_account(bank)
    bank.adjust(acct["id"], 5000, "funding")
    finance.release_yield_for_match(season_id, 1)  # 5,000 locked, 350 liquid

    released = bank.unlock_vault(season_id, force=True)

    acct = bank.get_account(acct["id"])
    assert released[0]["released"] == 5000
    assert acct["liquid_cash"] == 5350  # 350 harvest + 5,000 principal back
    assert acct["locked_capital"] == 0
    pos = bank.vault_positions(acct["id"])[0]
    assert pos["unlocked"] == 1


# ── TC12: squad-cost levy still works on auto accounts ──────────────────────
def test_tc12_squad_levy_on_auto_account(app, bank, finance):
    season, players, teams = _setup(app, n_teams=2)
    # One team spent (exempt) so the levy has an average to charge everyone else.
    with app.extensions["db"].write() as conn:
        conn.execute("UPDATE teams SET spent = 3000 WHERE id = ?", (teams[0]["id"],))
    # An auto account with liquid + vault exposure.
    acct = _auto_account(bank, owner_id="gp-levy")
    bank.adjust(acct["id"], 5000, "funding")
    finance.release_yield_for_match(season["id"], 1)  # 5,000 locked, 350 liquid

    # avg squad cost = 3000 / 2 teams = 1500.
    result = finance.apply_squad_levy(season["id"])
    assert result["applied"] and result["levy"] == 1500

    acct = bank.get_account(acct["id"])
    # Levy took 1,500 of the 350 harvest from LIQUID... only 350 available,
    # remainder 1,150 seized from the vault position.
    assert acct["liquid_cash"] == 0
    assert acct["locked_capital"] == 3850  # 5000 - 1150 seized
    pos = bank.vault_positions(acct["id"])[0]
    assert pos["locked_capital"] == 3850

    # Idempotent: re-running does nothing.
    again = finance.apply_squad_levy(season["id"])
    assert not again["applied"]
