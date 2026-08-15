"""Reset team account balances to zero (new economic system).

The manager's player account IS the team's money (the team purse IS the wallet,
no separate teams.purse_remaining column), so the reset zeroes each manager's
wallet. History is preserved: existing `bank_transactions` and
`season_finance_entries` rows stay; each reset appends one `balance_reset`
transaction per account documenting old -> new.

Nothing is zeroed if the wallet is already 0 (idempotent).

Usage:
    ./.venv/Scripts/python.exe scripts/reset_balances.py --db data/scl.db --yes
    ./.venv/Scripts/python.exe scripts/reset_balances.py --db data/scl.db   # dry run

Refuses to write without --yes.
"""
import argparse
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.db import Database  # noqa: E402
from app.services.bank_service import BankService  # noqa: E402


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def collect(db: Database) -> list:
    """Return team rows needing a reset: manager, wallet, locked."""
    bank = BankService(db)
    rows = []
    with db.read() as conn:
        teams = conn.execute(
            "SELECT id, name, manager_player_id FROM teams "
            "ORDER BY name").fetchall()
        for t in teams:
            manager = (t["manager_player_id"] or "").strip()
            if not manager:
                continue
            account = bank.account_for_owner("player", manager)
            rows.append({
                "team_id": t["id"],
                "team_name": t["name"],
                "wallet": int(account["liquid_cash"]) if account else 0,
                "locked": int(account["locked_capital"]) if account else 0,
                "account_id": account["id"] if account else None,
            })
    return rows


def apply_reset(db: Database, rows: list) -> dict:
    bank = BankService(db)
    reset = []
    with db.write() as conn:
        for r in rows:
            if r["wallet"] == 0 and r["locked"] == 0:
                continue
            # Wallet -> 0 (liquid + locked; no vault positions exist in S1,
            # but keep the account clean regardless).
            if r["account_id"]:
                conn.execute(
                    "UPDATE bank_accounts SET liquid_cash = 0, locked_capital = 0 "
                    "WHERE id = ?", (r["account_id"],))
                if r["wallet"] != 0:
                    bank._log(conn, r["account_id"], "balance_reset", -r["wallet"], 0,
                              f"Balance reset (new economy); old liquid {r['wallet']}")
                if r["locked"] != 0:
                    bank._log(conn, r["account_id"], "balance_reset", -r["locked"], 0,
                              f"Vault reset (new economy); old locked {r['locked']}")
            reset.append({**r, "reset": True})
    return {"reset": reset, "count": len(reset)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Reset team account balances to zero.")
    parser.add_argument("--db", default="data/scl.db", help="target SQLite DB path")
    parser.add_argument("--yes", action="store_true",
                        help="actually write (default is a dry run)")
    args = parser.parse_args()

    db = Database(args.db)
    db.bootstrap()
    rows = collect(db)
    if not rows:
        print("No teams with managers found.")
        return 0

    print("Teams that would be reset (wallet -> 0):")
    for r in rows:
        if r["wallet"] == 0 and r["locked"] == 0:
            print(f"  {r['team_name']:<22} already 0 (skipped)")
        else:
            print(f"  {r['team_name']:<22} wallet {r['wallet']:<6} locked {r['locked']}")
    print(f"  -> {sum(1 for r in rows if r['wallet'] or r['locked'])} account(s) to reset")

    if not args.yes:
        print("\nDry run — no changes written. Re-run with --yes to apply.")
        return 0

    summary = apply_reset(db, rows)
    print(f"\nReset complete: {summary['count']} account(s). History kept "
          "(one balance_reset transaction appended per account).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
