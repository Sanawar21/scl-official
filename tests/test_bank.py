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
