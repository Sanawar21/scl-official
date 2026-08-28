"""Central banking: player/team accounts, ledger, and the Vault.

Vault mechanics (per S2 Vault guide):
- Deposits convert Liquid Cash -> Locked Capital (principal untouchable until season end).
- 7% yield per match.
- Default (compounding): yield is added to locked capital, so it compounds.
- Manual harvest: 7% is calculated on the initial principal only and paid out to liquid.
"""
import re
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
                "INSERT INTO bank_accounts (id, owner_type, owner_id, liquid_cash, locked_capital, auto_vault, created_at) "
                "VALUES (?, ?, ?, 0, 0, 1, ?)",
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
        caller's write transaction (same connection, atomic with the caller's work).

        If a deduction exceeds liquid cash and the account has locked vault
        capital, the shortfall is pulled from the vault automatically."""
        def _impl(c):
            account = c.execute("SELECT * FROM bank_accounts WHERE id = ?", (account_id,)).fetchone()
            if not account:
                raise ValueError("Account not found")
            liquid = int(account["liquid_cash"])
            locked = int(account["locked_capital"])
            new_liquid = liquid + int(amount)

            # --- auto-draw from vault when liquid is short ---
            vault_take = 0
            if new_liquid < 0 and account["owner_type"] != "house":
                shortfall = abs(new_liquid)
                vault_take = min(shortfall, locked)
                if vault_take < shortfall:
                    raise ValueError(
                        f"Insufficient funds: liquid {liquid}, vault {locked}, "
                        f"need {abs(int(amount))}"
                    )
                new_liquid += vault_take
                # pull from vault position(s) — latest season first
                remaining = vault_take
                positions = c.execute(
                    "SELECT * FROM vault_positions WHERE account_id = ? ORDER BY created_at DESC",
                    (account_id,),
                ).fetchall()
                for pos in positions:
                    if remaining <= 0:
                        break
                    take = min(remaining, int(pos["locked_capital"]))
                    if take <= 0:
                        continue
                    c.execute(
                        "UPDATE vault_positions SET locked_capital = ?, principal = MAX(0, principal - ?) WHERE id = ?",
                        (int(pos["locked_capital"]) - take, take, pos["id"]),
                    )
                    remaining -= take

            new_locked = locked - vault_take
            c.execute(
                "UPDATE bank_accounts SET liquid_cash = ?, locked_capital = ? WHERE id = ?",
                (new_liquid, new_locked, account_id),
            )
            log_comment = comment or ""
            if vault_take > 0:
                log_comment = f"{log_comment} (vault auto-draw: {vault_take})".strip()
            self._log(c, account_id, tx_type, amount, new_liquid, log_comment)
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

        The money lands in **liquid cash** even for auto accounts — the 10k
        must be spendable through the auction (managers bid with it). Auto
        accounts' leftover liquid is locked into the vault AFTER the auction
        via `sweep_auto_liquid_to_vault`. Wallets are auto-created for players who
        never signed up (auto mode ON by default). Idempotent: an account that
        already received `season_funding` is skipped on re-runs.
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
                acct = self.get_or_create_account("player", pid, conn=conn)
                already = conn.execute(
                    "SELECT 1 FROM bank_transactions WHERE account_id = ? "
                    "AND type = 'season_funding'", (acct["id"],)).fetchone()
                if already:
                    skipped += 1
                    continue
                self.credit(acct["id"], amount, comment, tx_type="season_funding",
                            season_id=season_id, conn=conn, force_liquid=True)
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
            # New position: yield accrues from the next RELEASED match onward
            # (the admin releases yield manually; capital locked after release
            # N earns from release N+1 — no retroactive steps).
            start_match = self._released_through(conn, season_id)
            position_id = secrets.token_hex(8)
            conn.execute(
                "INSERT INTO vault_positions (id, account_id, season_id, principal, locked_capital, "
                "reinvest, last_yield_match, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (position_id, account_id, season_id, amount, amount,
                 1 if reinvest else 0, start_match, _now()),
            )
        conn.execute(
            "UPDATE bank_accounts SET liquid_cash = ?, locked_capital = ? WHERE id = ?",
            (new_liquid, int(account["locked_capital"]) + amount, account_id),
        )
        self._log(conn, account_id, "vault_lock", -amount, new_liquid, f"Locked {amount} in vault")

    @staticmethod
    def _released_through(conn, season_id: str) -> int:
        """Highest match number whose yield has been RELEASED (0 if none).

        Yield is decoupled from match finalization: the admin releases it
        manually, and each release is recorded in the season finance ledger
        (type 'yield_release'). New vault positions start their horizon here so
        capital locked after release N never earns steps 1..N retroactively."""
        nums = []
        for r in conn.execute(
                "SELECT match_id FROM season_finance_entries WHERE season_id = ? "
                "AND type = 'yield_release'", (season_id,)).fetchall():
            m = re.search(r"\d+", str(r["match_id"] or ""))
            if m:
                nums.append(int(m.group(0)))
        return max(nums) if nums else 0

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
               season_id: str = None, conn=None, force_liquid: bool = False) -> dict:
        """Credit an account — always into liquid cash.

        Auto mode no longer routes credits straight to the vault: money stays
        liquid and the FULL balance of every auto account is swept into the
        vault just before each yield release (see `release_yield` /
        `sweep_auto_liquid_to_vault`). `season_id` and
        `force_liquid` are accepted for caller compatibility but unused.
        Pass `conn` to run inside a caller's write transaction."""
        amount = int(amount)
        if amount <= 0:
            raise ValueError("Amount must be positive")

        def _impl(c):
            account = c.execute("SELECT * FROM bank_accounts WHERE id = ?", (account_id,)).fetchone()
            if not account:
                raise ValueError("Account not found")
            self.adjust(account_id, amount, comment, tx_type=tx_type, conn=c)
            return row_to_dict(c.execute(
                "SELECT * FROM bank_accounts WHERE id = ?", (account_id,)).fetchone())

        if conn is not None:
            return _impl(conn)
        with self.db.write() as c:
            return _impl(c)

    def sweep_auto_liquid_to_vault(self, season_id: str) -> dict:
        """Sweep every AUTO account's full liquid balance into its season vault
        position — in MANUAL-HARVEST mode (reinvest=0).

        Auto accounts stay as liquid as possible: all income (match rewards,
        admin adds, wager payouts) lands in liquid cash and is only vaulted at
        the very last moment — immediately BEFORE a yield release — so it earns
        the next 7% step. Because auto positions always run manual harvest,
        that step pays straight back out into liquid instead of compounding
        silently inside the vault.

        Any existing COMPOUND position on an auto account is flipped to manual
        here (idempotent migration, runs on every sweep). Manual accounts are
        never touched. Returns {"locked": n_accounts, "amount": total}."""
        season_id = (season_id or "").strip().lower()
        locked = 0
        total = 0
        with self.db.write() as conn:
            # Auto-account positions are ALWAYS manual-harvest: yields must
            # land in spendable liquid, never compound inside the vault.
            conn.execute(
                "UPDATE vault_positions SET reinvest = 0 WHERE account_id IN "
                "(SELECT id FROM bank_accounts WHERE auto_vault = 1)")
            accounts = conn.execute(
                "SELECT * FROM bank_accounts WHERE auto_vault = 1").fetchall()
            for account in accounts:
                liquid = int(account["liquid_cash"])
                if liquid <= 0:
                    continue
                self._lock_internal(conn, account, season_id, liquid, reinvest=False)
                locked += 1
                total += liquid
        return {"locked": locked, "amount": total}

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

    def vault_adjust(self, account_id: str, season_id: str, amount: int,
                     comment: str = "") -> dict:
        """Admin: directly add/remove funds from an account's vault.

        Positive amount = inject money into vault (liquid -> locked).
        Negative amount = withdraw money from vault (locked -> liquid).
        Creates a vault position for the season if one doesn't exist."""
        amount = int(amount)
        if amount == 0:
            raise ValueError("Amount must be non-zero")
        with self.db.write() as conn:
            account = conn.execute(
                "SELECT * FROM bank_accounts WHERE id = ?", (account_id,)
            ).fetchone()
            if not account:
                raise ValueError("Account not found")
            liquid = int(account["liquid_cash"])
            locked = int(account["locked_capital"])
            if amount > 0:
                # inject into vault — pull from liquid
                if liquid < amount:
                    raise ValueError(
                        f"Insufficient liquid cash: have {liquid}, need {amount}"
                    )
                self._lock_internal(conn, account, season_id, amount, reinvest=True)
                self._log(conn, account_id, "vault_admin_inject", amount,
                          liquid - amount, comment or f"Admin injected {amount} into vault")
            else:
                # withdraw from vault — push to liquid
                withdraw = abs(amount)
                if locked < withdraw:
                    raise ValueError(
                        f"Insufficient vault balance: have {locked}, need {withdraw}"
                    )
                # pull from vault position(s) — latest season first
                remaining = withdraw
                positions = conn.execute(
                    "SELECT * FROM vault_positions WHERE account_id = ? ORDER BY created_at DESC",
                    (account_id,),
                ).fetchall()
                for pos in positions:
                    if remaining <= 0:
                        break
                    take = min(remaining, int(pos["locked_capital"]))
                    if take <= 0:
                        continue
                    conn.execute(
                        "UPDATE vault_positions SET locked_capital = ?, principal = MAX(0, principal - ?) WHERE id = ?",
                        (int(pos["locked_capital"]) - take, take, pos["id"]),
                    )
                    remaining -= take
                new_liquid = liquid + withdraw
                new_locked = locked - withdraw
                conn.execute(
                    "UPDATE bank_accounts SET liquid_cash = ?, locked_capital = ? WHERE id = ?",
                    (new_liquid, new_locked, account_id),
                )
                self._log(conn, account_id, "vault_admin_withdraw", -withdraw,
                          new_liquid, comment or f"Admin withdrew {withdraw} from vault")
        return self.get_account(account_id)

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
