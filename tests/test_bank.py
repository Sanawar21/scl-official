import pytest

from tests.conftest import _setup


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


def test_linking_creates_wallet_in_manual_mode(app, bank):
    """Linking an account to a player switches it to MANUAL (auto off): new
    unlinked signups default to auto, but the link is the signal that the
    player manages their own finances."""
    auth = app.extensions["auth_service"]
    season, players, _ = _setup(app, n_teams=2)
    gp = players[2]["global_player_id"]  # non-manager player, no wallet yet
    user = auth.signup("linkme", "pass1234", "Link Me")
    auth.link_user_to_player(user["id"], gp)
    acct = bank.account_for_owner("player", gp)
    assert acct is not None, "linking should create the wallet"
    assert acct["auto_vault"] == 0, "linked accounts are manual"
    assert acct["liquid_cash"] == 0


def test_linking_flips_existing_auto_account_to_manual(app, bank):
    """A wallet that already exists in auto mode is flipped to manual when the
    account gets linked to the player."""
    auth = app.extensions["auth_service"]
    season, players, _ = _setup(app, n_teams=2)
    gp = players[2]["global_player_id"]
    acct = bank.get_or_create_account("player", gp)
    assert acct["auto_vault"] == 1  # new accounts default to auto
    user = auth.signup("linkme2", "pass1234", "Link Me 2")
    auth.link_user_to_player(user["id"], gp)
    assert bank.account_for_owner("player", gp)["auto_vault"] == 0


def test_unlink_restores_auto_mode(app, bank):
    """Unlinking an account restores AUTO mode — the only accounts that run on
    auto are the ones NOT linked to a player."""
    auth = app.extensions["auth_service"]
    season, players, _ = _setup(app, n_teams=2)
    gp = players[2]["global_player_id"]
    acct = bank.get_or_create_account("player", gp)
    assert acct["auto_vault"] == 1  # new accounts default to auto
    user = auth.signup("unlinkme", "pass1234", "Unlink Me")
    auth.link_user_to_player(user["id"], gp)
    assert bank.account_for_owner("player", gp)["auto_vault"] == 0  # linked = manual
    auth.unlink_user(user["id"])
    assert bank.account_for_owner("player", gp)["auto_vault"] == 1  # unlinked = auto


def test_fund_all_players_lands_in_liquid_not_vault(app, bank):
    """Universal funding must be spendable (liquid) — managers need it to bid.
    New accounts default to auto mode, but funding lands liquid regardless;
    the leftover locks into the vault only after the auction."""
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
        assert acct["auto_vault"] == 1, "new accounts default to auto mode"
        assert acct["locked_capital"] == 0, "nothing locked by funding itself"
    # Idempotent: second run skips both.
    result2 = bank.fund_all_players(10000)
    assert result2["skipped"] == 2


def test_sweep_auto_liquid_to_vault_locks_remaining_liquid(app, bank):
    """Sweeping auto accounts locks their liquid into the vault (manual-harvest
    mode); manual accounts keep liquid control."""
    with app.extensions["db"].write() as conn:
        conn.execute("INSERT INTO global_players (id, name, tier, speciality, created_at) "
                     "VALUES ('gp-a', 'A', 'gold', 'BATTER', '2026-01-01')")
        conn.execute("INSERT INTO global_players (id, name, tier, speciality, created_at) "
                     "VALUES ('gp-b', 'B', 'silver', 'BOWLER', '2026-01-01')")
    bank.fund_all_players(10000)  # both land liquid, auto by default
    b = bank.account_for_owner("player", "gp-b")
    bank.set_auto(b["id"], False)  # B opts out of auto

    result = bank.sweep_auto_liquid_to_vault("season-2")
    assert result["locked"] == 1
    assert result["amount"] == 10000

    a = bank.account_for_owner("player", "gp-a")
    assert a["liquid_cash"] == 0 and a["locked_capital"] == 10000
    positions = bank.vault_positions(a["id"])
    assert len(positions) == 1 and positions[0]["locked_capital"] == 10000
    assert positions[0]["reinvest"] == 0, "auto positions are always manual-harvest"
    # Manual account untouched.
    b = bank.account_for_owner("player", "gp-b")
    assert b["liquid_cash"] == 10000 and b["locked_capital"] == 0
    assert bank.vault_positions(b["id"]) == []


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


def test_adjust_auto_draws_from_vault_when_liquid_short(app, bank):
    """When liquid cash is insufficient for a deduction, the shortfall is
    automatically pulled from vault locked capital."""
    account = _account(app, bank)
    bank.adjust(account["id"], 5000, "funds")
    bank.lock_to_vault(account["id"], "season-2", 3000, reinvest=True)
    account = bank.get_account(account["id"])
    assert account["liquid_cash"] == 2000
    assert account["locked_capital"] == 3000
    # Try to deduct 2500 — liquid only has 2000, so 500 should come from vault
    account = bank.adjust(account["id"], -2500, "big fine")
    assert account["liquid_cash"] == 0  # 2000 - 2500 + 500 from vault
    assert account["locked_capital"] == 2500  # 3000 - 500
    # Verify the log mentions vault auto-draw
    txns = bank.transactions(account["id"])
    last = txns[0]
    assert "vault auto-draw" in last["comment"]


def test_adjust_vault_draw_exact_shortfall(app, bank):
    """Deduction equals liquid + vault — account drains to zero."""
    account = _account(app, bank)
    bank.adjust(account["id"], 3000, "funds")
    bank.lock_to_vault(account["id"], "season-2", 2000, reinvest=True)
    # liquid=1000, vault=2000 — deduct 3000
    account = bank.adjust(account["id"], -3000, "total drain")
    assert account["liquid_cash"] == 0
    assert account["locked_capital"] == 0


def test_adjust_vault_draw_insufficient_even_with_vault(app, bank):
    """Deduction exceeds liquid + vault — error raised."""
    account = _account(app, bank)
    bank.adjust(account["id"], 1000, "funds")
    bank.lock_to_vault(account["id"], "season-2", 500, reinvest=True)
    # liquid=500, vault=500 — deduct 2000 should fail
    with pytest.raises(ValueError, match="Insufficient funds"):
        bank.adjust(account["id"], -2000, "too much")


def test_vault_adjust_admin_inject(app, bank):
    """Admin injects money from liquid into vault."""
    account = _account(app, bank)
    bank.adjust(account["id"], 5000, "funds")
    account = bank.vault_adjust(account["id"], "season-2", 2000, "admin inject")
    assert account["liquid_cash"] == 3000
    assert account["locked_capital"] == 2000


def test_vault_adjust_admin_withdraw(app, bank):
    """Admin withdraws money from vault back to liquid."""
    account = _account(app, bank)
    bank.adjust(account["id"], 5000, "funds")
    bank.lock_to_vault(account["id"], "season-2", 3000, reinvest=True)
    account = bank.vault_adjust(account["id"], "season-2", -1000, "admin withdraw")
    assert account["liquid_cash"] == 3000  # 2000 + 1000
    assert account["locked_capital"] == 2000  # 3000 - 1000


def test_vault_adjust_admin_insufficient_liquid(app, bank):
    """Admin inject fails if liquid is insufficient."""
    account = _account(app, bank)
    bank.adjust(account["id"], 500, "funds")
    with pytest.raises(ValueError, match="Insufficient liquid"):
        bank.vault_adjust(account["id"], "season-2", 1000, "too much")


def test_vault_adjust_admin_insufficient_vault(app, bank):
    """Admin withdraw fails if vault is insufficient."""
    account = _account(app, bank)
    bank.adjust(account["id"], 5000, "funds")
    bank.lock_to_vault(account["id"], "season-2", 500, reinvest=True)
    with pytest.raises(ValueError, match="Insufficient vault"):
        bank.vault_adjust(account["id"], "season-2", -1000, "too much")
