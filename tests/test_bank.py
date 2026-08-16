import pytest


@pytest.fixture()
def bank(app):
    return app.extensions["bank_service"]


def _account(app, bank, owner_id="p1"):
    return bank.get_or_create_account("player", owner_id)


def test_account_and_adjust(app, bank):
    account = _account(app, bank)
    assert account["liquid_cash"] == 0
    account = bank.adjust(account["id"], 5000, "admin gift", tx_type="admin_adjust")
    assert account["liquid_cash"] == 5000
    with pytest.raises(ValueError):
        bank.adjust(account["id"], -99999, "overdraft")
    txns = bank.transactions(account["id"])
    assert len(txns) == 1 and txns[0]["amount"] == 5000


def test_fund_all_players_lands_in_liquid_not_vault(app, bank):
    """Universal funding must be spendable (liquid) — managers need it to bid.
    Auto mode is opt-in, never forced by funding."""
    with app.extensions["db"].write() as conn:
        conn.execute("INSERT INTO global_players (id, name, tier, speciality, created_at) "
                     "VALUES ('gp-x', 'X', 'gold', 'BATTER', '2026-01-01')")
        conn.execute("INSERT INTO global_players (id, name, tier, speciality, created_at) "
                     "VALUES ('gp-y', 'Y', 'silver', 'BOWLER', '2026-01-01')")
    result = bank.fund_all_players(10000)
    assert result["funded"] == 2
    for gp_id in ("gp-x", "gp-y"):
        acct = bank.account_for_owner("player", gp_id)
        assert acct["liquid_cash"] == 10000, "funding must land liquid"
        assert acct["auto_vault"] == 0, "auto mode must not be forced"
        assert acct["locked_capital"] == 0, "nothing locked by funding"
    # Idempotent: second run skips both.
    result2 = bank.fund_all_players(10000)
    assert result2["skipped"] == 2


def test_vault_lock_and_compounding_yield(app, bank):
    account = _account(app, bank)
    bank.adjust(account["id"], 10000, "funds")
    account = bank.lock_to_vault(account["id"], "season-2", 2000, reinvest=True)
    assert account["liquid_cash"] == 8000
    assert account["locked_capital"] == 2000
    # Match 1: 7% of 2000 = 140 compounded.
    bank.apply_match_yield("season-2", 1)
    account = bank.get_account(account["id"])
    assert account["locked_capital"] == 2140
    # Match 2: 7% of 2140 = 149.8 -> 150.
    bank.apply_match_yield("season-2", 2)
    account = bank.get_account(account["id"])
    assert account["locked_capital"] == 2290


def test_vault_manual_harvest_pays_out_principal_only(app, bank):
    account = _account(app, bank)
    bank.adjust(account["id"], 10000, "funds")
    bank.lock_to_vault(account["id"], "season-2", 2000, reinvest=False)
    bank.apply_match_yield("season-2", 1)
    account = bank.get_account(account["id"])
    # 7% of principal (2000) = 140 paid to liquid; locked stays 2000.
    assert account["locked_capital"] == 2000
    assert account["liquid_cash"] == 8000 + 140
    bank.apply_match_yield("season-2", 2)
    account = bank.get_account(account["id"])
    assert account["liquid_cash"] == 8000 + 280


def test_vault_lock_insufficient_funds(app, bank):
    account = _account(app, bank)
    with pytest.raises(ValueError):
        bank.lock_to_vault(account["id"], "season-2", 100, reinvest=True)
