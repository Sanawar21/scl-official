"""Universal player funding (S2 economy).

Credits EVERY global player's wallet once before the auction (default 10,000).
Wallets are auto-created — players who never signed up get one too. Idempotent:
accounts that already received `season_funding` are skipped, so re-running is
safe. The same action exists in the admin UI (Finances -> "Fund all players").

Usage:
    ./.venv/Scripts/python.exe scripts/fund_players.py --db data/scl.db --amount 10000 --yes
    ./.venv/Scripts/python.exe scripts/fund_players.py --db data/scl.db       # dry run

Refuses to write without --yes.
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.db import Database  # noqa: E402
from app.services.bank_service import BankService  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="Fund every player's wallet once")
    parser.add_argument("--db", default="data/scl.db", help="SQLite DB path")
    parser.add_argument("--amount", type=int, default=10000, help="Amount per player")
    parser.add_argument("--yes", action="store_true", help="Actually write (dry run otherwise)")
    args = parser.parse_args()

    db = Database(args.db)
    db.bootstrap()
    bank = BankService(db)
    with db.read() as conn:
        total = conn.execute("SELECT COUNT(*) FROM global_players").fetchone()[0]
        funded = conn.execute(
            "SELECT COUNT(DISTINCT a.id) FROM bank_accounts a "
            "JOIN bank_transactions t ON t.account_id = a.id "
            "WHERE a.owner_type = 'player' AND t.type = 'season_funding'").fetchone()[0]

    print(f"Players in system : {total}")
    print(f"Already funded    : {funded}")
    print(f"Will fund         : {max(total - funded, 0)} x {args.amount:,}")
    if not args.yes:
        print("\nDry run — pass --yes to write.")
        return

    result = bank.fund_all_players(args.amount)
    print(f"\nDone: funded {result['funded']}, skipped {result['skipped']} (already funded).")


if __name__ == "__main__":
    main()
