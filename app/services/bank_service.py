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
    # --- universal funding --------------------------------------------------
    def fund_all_players(self, amount: int = 10000, comment: str = "") -> dict:
        """Credit every global player's wallet once (S2 universal funding).

        Wallets are auto-created — players who never signed up get one too, and
        it runs on **auto mode** (auto_vault on; the 10k lands in the vault of
        the latest season). Accounts the owner already manages keep their own
        mode. Idempotent: an account that already received `season_funding` is
        skipped on re-runs. Returns {"funded": n, "skipped": n}.
        """
        amount = int(amount)
        if amount <= 0:
            raise ValueError("Amount must be positive")
        comment = comment or f"{amount:,} universal funding"
        with self.db.read() as conn:
            pids = [r["id"] for r in conn.execute(
                "SELECT id FROM global_players ORDER BY name").fetchall()]
            latest = conn.execute(
                "SELECT id FROM seasons ORDER BY rowid DESC LIMIT 1").fetchone()
        season_id = latest["id"] if latest else None
        funded = skipped = 0
        with self.db.write() as conn:
            for pid in pids:
                existed = conn.execute(
                    "SELECT 1 FROM bank_accounts WHERE owner_type = 'player' AND owner_id = ?",
                    (pid,)).fetchone()
                acct = self.get_or_create_account("player", pid, conn=conn)
                already = conn.execute(
                    "SELECT 1 FROM bank_transactions WHERE account_id = ? "
                    "AND type = 'season_funding'", (acct["id"],)).fetchone()
                if already:
                    skipped += 1
                    continue
                # A wallet created right here (player never signed up) runs on
                # auto by default; pre-existing wallets keep the owner's mode.
                if not existed and not acct["auto_vault"]:
                    conn.execute(
                        "UPDATE bank_accounts SET auto_vault = 1 WHERE id = ?",
                        (acct["id"],))
                self.credit(acct["id"], amount, comment, tx_type="season_funding",
                            season_id=season_id, conn=conn)
                funded += 1
        return {"funded": funded, "skipped": skipped}

    def _lock_internal(self, conn, account, season_id: str, amount: int, reinvest: bool = True) -> None:
        """Move `amount` liquid into the vault position (caller's write txn)."""
        account_id = account["id"]
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
            self._lock_internal(conn, account, season_id, amount, reinvest=reinvest)
        return self.get_account(account_id)

    def set_auto(self, account_id: str, on: bool) -> dict:
        """Toggle auto mode: all incoming money routes straight to the vault."""
        with self.db.write() as conn:
            conn.execute(
                "UPDATE bank_accounts SET auto_vault = ? WHERE id = ?",
                (1 if on else 0, account_id),
            )
        return self.get_account(account_id)

    def seize(self, account_id: str, season_id: str, amount: int, conn=None,
              comment: str = "") -> int:
        """Remove locked vault capital without moving liquid (squad-cost levy).

        Takes from the season's vault position, capped at its locked capital.
        Returns the amount actually seized."""
        amount = int(amount)

        def _impl(c):
            position = c.execute(
                "SELECT * FROM vault_positions WHERE account_id = ? AND season_id = ?",
                (account_id, season_id),
            ).fetchone()
            if not position:
                return 0
            take = min(amount, int(position["locked_capital"]))
            if take <= 0:
                return 0
            c.execute(
                "UPDATE vault_positions SET locked_capital = ? WHERE id = ?",
                (int(position["locked_capital"]) - take, position["id"]),
            )
            account = c.execute(
                "SELECT * FROM bank_accounts WHERE id = ?", (account_id,)).fetchone()
            c.execute(
                "UPDATE bank_accounts SET locked_capital = ? WHERE id = ?",
                (int(account["locked_capital"]) - take, account_id),
            )
            self._log(c, account_id, "squad_levy", -take,
                      int(account["liquid_cash"]),
                      comment or f"Squad cost levy {take} (from vault)")
            return take

        if conn is not None:
            return _impl(conn)
        with self.db.write() as c:
            return _impl(c)

    def unlock_amount(self, account_id: str, season_id: str, amount: int, conn=None,
                      comment: str = "") -> int:
        """Release locked vault capital back to liquid, capped at what's locked.

        Used to reverse auto-vaulted credits (undoing a match reward). The
        position's principal is left untouched — only locked capital moves.
        Returns the amount actually released."""
        amount = int(amount)

        def _impl(c):
            position = c.execute(
                "SELECT * FROM vault_positions WHERE account_id = ? AND season_id = ?",
                (account_id, season_id),
            ).fetchone()
            if not position:
                return 0
            release = min(amount, int(position["locked_capital"]))
            if release <= 0:
                return 0
            new_locked = int(position["locked_capital"]) - release
            c.execute(
                "UPDATE vault_positions SET locked_capital = ? WHERE id = ?",
                (new_locked, position["id"]),
            )
            account = c.execute(
                "SELECT * FROM bank_accounts WHERE id = ?", (account_id,)).fetchone()
            new_liquid = int(account["liquid_cash"]) + release
            c.execute(
                "UPDATE bank_accounts SET liquid_cash = ?, locked_capital = ? WHERE id = ?",
                (new_liquid, int(account["locked_capital"]) - release, account_id),
            )
            self._log(c, account_id, "vault_unlock", release, new_liquid,
                      comment or f"Unlocked {release} from vault")
            return release

        if conn is not None:
            return _impl(conn)
        with self.db.write() as c:
            return _impl(c)

    def credit(self, account_id: str, amount: int, comment: str, tx_type: str = "adjust",
               season_id: str = None, conn=None) -> dict:
        """Credit an account; auto-vault accounts route straight to the vault.

        Auto accounts: money lands in liquid and is immediately locked into the
        season's vault position (compounding) — net liquid unchanged. Manual
        accounts: plain liquid credit. Pass `conn` to run inside a caller's
        write transaction (atomic with the caller's work)."""
        amount = int(amount)
        if amount <= 0:
            raise ValueError("Amount must be positive")

        def _impl(c):
            account = c.execute("SELECT * FROM bank_accounts WHERE id = ?", (account_id,)).fetchone()
            if not account:
                raise ValueError("Account not found")
            if account["auto_vault"] and season_id:
                new_liquid = int(account["liquid_cash"]) + amount
                c.execute(
                    "UPDATE bank_accounts SET liquid_cash = ? WHERE id = ?",
                    (new_liquid, account_id),
                )
                self._log(c, account_id, tx_type, amount, new_liquid, comment)
                fresh = c.execute(
                    "SELECT * FROM bank_accounts WHERE id = ?", (account_id,)).fetchone()
                self._lock_internal(c, fresh, season_id, amount, reinvest=True)
            else:
                self.adjust(account_id, amount, comment, tx_type=tx_type, conn=c)
            return row_to_dict(c.execute(
                "SELECT * FROM bank_accounts WHERE id = ?", (account_id,)).fetchone())

        if conn is not None:
            return _impl(conn)
        with self.db.write() as c:
            return _impl(c)

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
                # Running totals: compounding steps must build on each other, so
                # the loop cannot re-read the stale row between steps.
                locked = int(position["locked_capital"])
                principal = int(position["principal"])
                account = conn.execute(
                    "SELECT * FROM bank_accounts WHERE id = ?", (position["account_id"],)
                ).fetchone()
                account_locked = int(account["locked_capital"])
                account_liquid = int(account["liquid_cash"])
                for step in range(int(position["last_yield_match"]) + 1, target + 1):
                    base = principal if not position["reinvest"] else locked
                    yield_amount = int(round(base * VAULT_YIELD_RATE))
                    if position["reinvest"]:
                        locked += yield_amount
                        account_locked += yield_amount
                        conn.execute(
                            "UPDATE vault_positions SET locked_capital = ?, last_yield_match = ? "
                            "WHERE id = ?",
                            (locked, step, position["id"]),
                        )
                        conn.execute(
                            "UPDATE bank_accounts SET locked_capital = ? WHERE id = ?",
                            (account_locked, position["account_id"]),
                        )
                        self._log(conn, position["account_id"], "vault_yield", yield_amount,
                                  account_liquid, f"Match {step} compounded yield")
                    else:
                        account_liquid += yield_amount
                        conn.execute(
                            "UPDATE bank_accounts SET liquid_cash = ? WHERE id = ?",
                            (account_liquid, position["account_id"]),
                        )
                        conn.execute(
                            "UPDATE vault_positions SET last_yield_match = ? WHERE id = ?",
                            (step, position["id"]),
                        )
                        self._log(conn, position["account_id"], "vault_harvest", yield_amount,
                                  account_liquid, f"Match {step} harvested yield")
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
