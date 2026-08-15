"""Central banking: player/team accounts, ledger, and the Vault.

Vault mechanics (per S2 Vault guide):
- Deposits convert Liquid Cash -> Locked Capital (principal untouchable until season end).
- 7% yield per match.
- Default (compounding): yield is added to locked capital, so it compounds.
- Manual harvest: 7% is calculated on the initial principal only and paid out to liquid.
"""
import secrets
from datetime import datetime, timezone

from ..db import row_to_dict, rows_to_dicts

VAULT_YIELD_RATE = 0.07


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class BankService:
    def __init__(self, db):
        self.db = db

    # --- accounts -----------------------------------------------------------
    def get_or_create_account(self, owner_type: str, owner_id: str, conn=None) -> dict:
        """Fetch or create an account. Pass `conn` to run inside a caller's
        write transaction (same connection, atomic with the caller's work)."""
        def _impl(c):
            row = c.execute(
                "SELECT * FROM bank_accounts WHERE owner_type = ? AND owner_id = ?",
                (owner_type, owner_id),
            ).fetchone()
            if row:
                return row_to_dict(row)
            account_id = secrets.token_hex(8)
            c.execute(
                "INSERT INTO bank_accounts (id, owner_type, owner_id, liquid_cash, locked_capital, created_at) "
                "VALUES (?, ?, ?, 0, 0, ?)",
                (account_id, owner_type, owner_id, _now()),
            )
            return row_to_dict(c.execute(
                "SELECT * FROM bank_accounts WHERE id = ?", (account_id,)
            ).fetchone())

        if conn is not None:
            return _impl(conn)
        with self.db.write() as c:
            return _impl(c)

    def get_account(self, account_id: str) -> dict:
        with self.db.read() as conn:
            return row_to_dict(conn.execute(
                "SELECT * FROM bank_accounts WHERE id = ?", (account_id,)
            ).fetchone())

    def account_for_owner(self, owner_type: str, owner_id: str) -> dict:
        with self.db.read() as conn:
            return row_to_dict(conn.execute(
                "SELECT * FROM bank_accounts WHERE owner_type = ? AND owner_id = ?",
                (owner_type, owner_id),
            ).fetchone())

    def _log(self, conn, account_id: str, tx_type: str, amount: int, balance_after, comment: str):
        conn.execute(
            "INSERT INTO bank_transactions (id, account_id, type, amount, balance_after, comment, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (secrets.token_hex(8), account_id, tx_type, amount, balance_after, comment or "", _now()),
        )

    def adjust(self, account_id: str, amount: int, comment: str, tx_type: str = "adjust",
               conn=None) -> dict:
        """amount may be negative for a deduction. Pass `conn` to run inside a
        caller's write transaction (same connection, atomic with the caller's work)."""
        def _impl(c):
            account = c.execute("SELECT * FROM bank_accounts WHERE id = ?", (account_id,)).fetchone()
            if not account:
                raise ValueError("Account not found")
            new_balance = int(account["liquid_cash"]) + int(amount)
            if new_balance < 0:
                raise ValueError("Insufficient liquid cash")
            c.execute(
                "UPDATE bank_accounts SET liquid_cash = ? WHERE id = ?",
                (new_balance, account_id),
            )
            self._log(c, account_id, tx_type, amount, new_balance, comment)
            return row_to_dict(c.execute(
                "SELECT * FROM bank_accounts WHERE id = ?", (account_id,)
            ).fetchone())

        if conn is not None:
            return _impl(conn)
        with self.db.write() as c:
            return _impl(c)

    # --- vault ----------------------------------------------------------------
    def lock_to_vault(self, account_id: str, season_id: str, amount: int, reinvest: bool = True) -> dict:
        amount = int(amount)
        if amount <= 0:
            raise ValueError("Amount must be positive")
        with self.db.write() as conn:
            account = conn.execute("SELECT * FROM bank_accounts WHERE id = ?", (account_id,)).fetchone()
            if not account:
                raise ValueError("Account not found")
            if int(account["liquid_cash"]) < amount:
                raise ValueError("Insufficient liquid cash")
            position = conn.execute(
                "SELECT * FROM vault_positions WHERE account_id = ? AND season_id = ?",
                (account_id, season_id),
            ).fetchone()
            new_liquid = int(account["liquid_cash"]) - amount
            if position:
                new_principal = int(position["principal"]) + amount
                new_locked = int(position["locked_capital"]) + amount
                conn.execute(
                    "UPDATE vault_positions SET principal = ?, locked_capital = ?, reinvest = ? "
                    "WHERE id = ?",
                    (new_principal, new_locked, 1 if reinvest else 0, position["id"]),
                )
            else:
                position_id = secrets.token_hex(8)
                conn.execute(
                    "INSERT INTO vault_positions (id, account_id, season_id, principal, locked_capital, "
                    "reinvest, last_yield_match, created_at) VALUES (?, ?, ?, ?, ?, ?, 0, ?)",
                    (position_id, account_id, season_id, amount, amount, 1 if reinvest else 0, _now()),
                )
            conn.execute(
                "UPDATE bank_accounts SET liquid_cash = ?, locked_capital = ? WHERE id = ?",
                (new_liquid, int(account["locked_capital"]) + amount, account_id),
            )
            self._log(conn, account_id, "vault_lock", -amount, new_liquid, f"Locked {amount} in vault")
        return self.get_account(account_id)

    def set_reinvest(self, position_id: str, reinvest: bool) -> dict:
        with self.db.write() as conn:
            position = conn.execute("SELECT * FROM vault_positions WHERE id = ?", (position_id,)).fetchone()
            if not position:
                raise ValueError("Vault position not found")
            conn.execute(
                "UPDATE vault_positions SET reinvest = ? WHERE id = ?",
                (1 if reinvest else 0, position_id),
            )
            return row_to_dict(conn.execute(
                "SELECT * FROM vault_positions WHERE id = ?", (position_id,)
            ).fetchone())

    def apply_match_yield(self, season_id: str, match_number: int) -> list:
        """Apply the 7% yield for every match step up to `match_number`.

        Yields compound per match (the docs' M1..M12 table), so a position
        whose last_yield_match is 0 given match_number=4 applies four
        sequential 7% steps, not a single 7% (which would skip matches 1-3).
        """
        target = int(match_number)
        if target < 1:
            raise ValueError("Match number must be >= 1")
        results = []
        with self.db.write() as conn:
            positions = conn.execute(
                "SELECT * FROM vault_positions WHERE season_id = ? AND last_yield_match < ?",
                (season_id, target),
            ).fetchall()
            for position in positions:
                for step in range(int(position["last_yield_match"]) + 1, target + 1):
                    locked = int(position["locked_capital"])
                    principal = int(position["principal"])
                    base = principal if not position["reinvest"] else locked
                    yield_amount = int(round(base * VAULT_YIELD_RATE))
                    account = conn.execute(
                        "SELECT * FROM bank_accounts WHERE id = ?", (position["account_id"],)
                    ).fetchone()
                    if position["reinvest"]:
                        new_locked = locked + yield_amount
                        conn.execute(
                            "UPDATE vault_positions SET locked_capital = ?, last_yield_match = ? "
                            "WHERE id = ?",
                            (new_locked, step, position["id"]),
                        )
                        new_total = int(account["locked_capital"]) + yield_amount
                        conn.execute(
                            "UPDATE bank_accounts SET locked_capital = ? WHERE id = ?",
                            (new_total, position["account_id"]),
                        )
                        self._log(conn, position["account_id"], "vault_yield", yield_amount,
                                  int(account["liquid_cash"]),
                                  f"Match {step} compounded yield")
                    else:
                        new_liquid = int(account["liquid_cash"]) + yield_amount
                        conn.execute(
                            "UPDATE bank_accounts SET liquid_cash = ? WHERE id = ?",
                            (new_liquid, position["account_id"]),
                        )
                        conn.execute(
                            "UPDATE vault_positions SET last_yield_match = ? WHERE id = ?",
                            (step, position["id"]),
                        )
                        self._log(conn, position["account_id"], "vault_harvest", yield_amount,
                                  new_liquid, f"Match {step} harvested yield")
                    results.append({"position_id": position["id"], "match": step,
                                    "yield": yield_amount,
                                    "reinvest": bool(position["reinvest"])})
        return results

    def unlock_vault(self, season_id: str, force: bool = False) -> list:
        """Release all locked vault capital of the season to liquid cash.

        The docs lock principal until the end of Match 12; refuse unless the
        season has >= 12 finalized matches unless `force` is given."""
        if not force:
            with self.db.read() as conn:
                count = conn.execute(
                    "SELECT COUNT(*) FROM match_stats WHERE season_id = ?", (season_id,)
                ).fetchone()[0]
            if count < 12:
                raise ValueError(
                    f"Season has only {count} finalized matches; vault unlocks after Match 12 "
                    "(use force to override)"
                )
        results = []
        with self.db.write() as conn:
            positions = conn.execute(
                "SELECT * FROM vault_positions WHERE season_id = ? AND unlocked = 0", (season_id,)
            ).fetchall()
            for position in positions:
                amount = int(position["locked_capital"])
                if amount <= 0:
                    conn.execute(
                        "UPDATE vault_positions SET unlocked = 1, unlocked_at = ? WHERE id = ?",
                        (_now(), position["id"]),
                    )
                    results.append({"position_id": position["id"], "released": 0})
                    continue
                account = conn.execute(
                    "SELECT * FROM bank_accounts WHERE id = ?", (position["account_id"],)
                ).fetchone()
                new_liquid = int(account["liquid_cash"]) + amount
                new_locked = int(account["locked_capital"]) - amount
                conn.execute(
                    "UPDATE bank_accounts SET liquid_cash = ?, locked_capital = ? WHERE id = ?",
                    (new_liquid, new_locked, position["account_id"]),
                )
                conn.execute(
                    "UPDATE vault_positions SET locked_capital = 0, unlocked = 1, unlocked_at = ? "
                    "WHERE id = ?",
                    (_now(), position["id"]),
                )
                self._log(conn, position["account_id"], "vault_unlock", amount, new_liquid,
                          f"Vault unlocked for season {season_id}")
                results.append({"position_id": position["id"], "released": amount})
        return results

    def vault_positions(self, account_id: str) -> list:
        with self.db.read() as conn:
            return rows_to_dicts(conn.execute(
                "SELECT * FROM vault_positions WHERE account_id = ? ORDER BY created_at", (account_id,)
            ).fetchall())

    def transactions(self, account_id: str, limit: int = 100) -> list:
        with self.db.read() as conn:
            return rows_to_dicts(conn.execute(
                "SELECT * FROM bank_transactions WHERE account_id = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (account_id, limit),
            ).fetchall())
