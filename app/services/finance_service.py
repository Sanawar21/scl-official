"""Season finance + Vault wiring.

The manager's player bank account IS his team's money (user decision), so all
team finance is really wallet movement on the manager's account. This service:

- auto-posts a fixed match reward to both playing teams when a match result is
  finalized (idempotent), and catches up the 7% vault yield for the season;
- offers manual wallet adjusts/transfers (fines, umpire duty, sub cash) with a
  season finance ledger (`season_finance_entries`) recording the story;
- one-step undo of the last ledger entry;
- a display-only credit-refund hint (refunds themselves go through the existing
  admin bank adjust, per user decision).
"""
import re
import secrets
from datetime import datetime, timezone

from ..db import json_loads, row_to_dict, rows_to_dicts


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _match_number_int(match_number: str, match_id: str = "") -> int:
    text = str(match_number or "").strip()
    match = re.search(r"\d+", text)
    if match:
        return int(match.group(0))
    text = str(match_id or "").strip()
    match = re.search(r"\d+", text)
    return int(match.group(0)) if match else 0


MAX_YIELD_MATCH = 12  # the Vault guide's yield horizon (end of Match 12)


class FinanceService:
    def __init__(self, db, bank_service, auction_service):
        self.db = db
        self.bank = bank_service
        self.auction = auction_service

    # ------------------------------------------------------------------
    # reads
    # ------------------------------------------------------------------
    def list_season_finances(self, season_id: str) -> list:
        """Budget Board: team + manager wallet (liquid) + credits + roster."""
        season_id = (season_id or "").strip().lower()
        if not season_id:
            return []
        rows = []
        with self.db.read() as conn:
            teams = conn.execute(
                "SELECT * FROM teams WHERE season_id = ?", (season_id,)).fetchall()
            for t in teams:
                account = self.bank.account_for_owner("player", t["manager_player_id"])
                rows.append({
                    "team_id": t["id"],
                    "team_name": t["name"],
                    "wallet": int(account["liquid_cash"]) if account else 0,
                    "credits_remaining": int(t["credits_remaining"] or 0),
                    "players_count": len(json_loads(t["players"], [])),
                    "bench_count": len(json_loads(t["bench"], [])),
                })
        rows.sort(key=lambda r: r["team_name"].lower())
        return rows

    def list_finance_entries(self, season_id: str, limit: int = 200) -> list:
        season_id = (season_id or "").strip().lower()
        with self.db.read() as conn:
            rows = conn.execute(
                "SELECT * FROM season_finance_entries WHERE season_id = ? "
                "ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (season_id, max(1, int(limit or 200)))).fetchall()
        entries = []
        for r in rows:
            entry = dict(r)
            ttype = entry.get("type") or "adjust"
            if ttype == "transfer":
                entry["label"] = "Transfer"
                entry["summary"] = (
                    f"{(entry.get('from_team_id') and self._team_name(entry['from_team_id'])) or '-'} → "
                    f"{(entry.get('to_team_id') and self._team_name(entry['to_team_id'])) or '-'}")
            elif ttype == "match_reward":
                entry["label"] = "Match reward"
                entry["summary"] = entry.get("team_name") or entry.get("team_id") or "-"
            elif ttype == "adjust":
                entry["label"] = "Add" if entry.get("operation") == "add" else "Remove"
                entry["summary"] = entry.get("team_name") or entry.get("team_id") or "-"
            else:
                entry["label"] = ttype.replace("_", " ").title()
                entry["summary"] = entry.get("team_name") or entry.get("team_id") or "-"
            entries.append(entry)
        return entries

    def _team_name(self, team_id: str) -> str:
        if not team_id:
            return ""
        with self.db.read() as conn:
            row = conn.execute("SELECT name FROM teams WHERE id = ?", (team_id,)).fetchone()
            return row["name"] if row else team_id

    def credit_refund_hint(self, season_id: str) -> list:
        """Display-only: what unspent credits are worth at the ruleset rate."""
        season_id = (season_id or "").strip().lower()
        rows = []
        with self.db.read() as conn:
            season = conn.execute("SELECT id FROM seasons WHERE id = ?", (season_id,)).fetchone()
            if not season:
                return rows
            ruleset = self.auction._get_ruleset(conn, season_id)
            for t in conn.execute(
                    "SELECT * FROM teams WHERE season_id = ?", (season_id,)).fetchall():
                credits = int(t["credits_remaining"] or 0)
                rows.append({
                    "team_id": t["id"],
                    "team_name": t["name"],
                    "credits_remaining": credits,
                    "refund_amount": credits * ruleset.credit_refund_rate,
                })
        rows.sort(key=lambda r: r["team_name"].lower())
        return rows

    # ------------------------------------------------------------------
    # match finalization (auto rewards + yield catch-up)
    # ------------------------------------------------------------------
    def _match_key(self, season_id: str, match_id: str) -> str:
        return f"{(season_id or '').strip().lower()}:{(match_id or '').strip().lower()}"

    def _finalized_match_keys(self, season_id: str) -> set:
        with self.db.read() as conn:
            rows = conn.execute(
                "SELECT match_key FROM match_stats WHERE season_id = ?", (season_id,)).fetchall()
        return {r["match_key"] for r in rows}

    def _resolve_match_teams(self, conn, season_id: str, match_id: str) -> list:
        reg = conn.execute(
            "SELECT * FROM match_registry WHERE season_id = ? AND match_id = ?",
            (season_id, match_id)).fetchone()
        if not reg:
            return []
        teams = []
        for ref in (reg["team_a_global_id"], reg["team_b_global_id"]):
            ref = (ref or "").strip()
            if not ref:
                continue
            row = conn.execute(
                "SELECT * FROM teams WHERE season_id = ? AND (id = ? OR global_team_id = ?)",
                (season_id, ref, ref)).fetchone()
            if row:
                teams.append(row_to_dict(row))
        return teams

    def _max_finalized_match_number(self, season_id: str) -> int:
        with self.db.read() as conn:
            rows = conn.execute(
                "SELECT r.match_number, r.match_id FROM match_stats s "
                "JOIN match_registry r ON r.match_key = s.match_key "
                "WHERE s.season_id = ?", (season_id,)).fetchall()
        numbers = [_match_number_int(r["match_number"], r["match_id"]) for r in rows]
        return max(numbers) if numbers else 0

    def _apply_match_reward(self, season_id: str, match_id: str, actor: str) -> bool:
        """Credit EVERY player wallet with the per-match reward (S2 economy).

        One `season_finance_entries` marker row (team_id NULL) guards the whole
        batch, so re-runs are no-ops and the admin 'pending' count still works.
        Auto-vault accounts get the money routed straight to the vault."""
        season_id = (season_id or "").strip().lower()
        match_id = (match_id or "").strip()
        if not season_id or not match_id:
            return False
        with self.db.write() as conn:
            already = conn.execute(
                "SELECT 1 FROM season_finance_entries WHERE season_id = ? "
                "AND type = 'match_reward' AND match_id = ?",
                (season_id, match_id)).fetchone()
            if already:
                return False
            ruleset = self.auction._get_ruleset(conn, season_id)
            reward = ruleset.match_reward_amount
            accounts = conn.execute(
                "SELECT * FROM bank_accounts WHERE owner_type = 'player'").fetchall()
            if not accounts:
                return False
            count = 0
            for acct in accounts:
                self.bank.credit(acct["id"], reward, f"Match reward ({match_id})",
                                 tx_type="match_reward", season_id=season_id, conn=conn)
                count += 1
            conn.execute(
                "INSERT INTO season_finance_entries (id, season_id, match_id, team_id, "
                "team_name, type, operation, amount, comment, created_by, before_wallet, "
                "after_wallet, created_at) VALUES (?, ?, ?, NULL, 'all players', "
                "'match_reward', NULL, ?, ?, ?, NULL, NULL, ?)",
                (secrets.token_hex(8), season_id, match_id, reward,
                 f"Match reward ({match_id}) — {count} players", actor, _now()))
        return True

    def _apply_yield_catchup(self, season_id: str) -> int:
        n = self._max_finalized_match_number(season_id)
        if n < 1:
            return 0
        results = self.bank.apply_match_yield(season_id, min(n, MAX_YIELD_MATCH))
        return len(results)

    def on_match_finalized(self, season_id: str, match_id: str, actor: str = "system") -> dict:
        """Idempotent: reward both playing teams + catch up vault yield.

        Called after a match result is recorded (CSV import / walkover). Safe to
        call repeatedly — rewards are skipped once, yield is guarded by
        last_yield_match.
        """
        key = self._match_key(season_id, match_id)
        with self.db.read() as conn:
            finalized = conn.execute(
                "SELECT 1 FROM match_stats WHERE match_key = ?", (key,)).fetchone()
        if not finalized:
            return {"finalized": False, "rewarded": False, "yield_applied": 0}
        rewarded = self._apply_match_reward(season_id, match_id, actor)
        yield_applied = self._apply_yield_catchup(season_id)
        return {"finalized": True, "rewarded": rewarded, "yield_applied": yield_applied}

    def process_pending(self, season_id: str, actor: str = "admin") -> list:
        """Backfill: run on_match_finalized for every finalized match."""
        season_id = (season_id or "").strip().lower()
        finalized = self._finalized_match_keys(season_id)
        if not finalized:
            return []
        with self.db.read() as conn:
            registry = conn.execute(
                "SELECT match_id FROM match_registry WHERE season_id = ?", (season_id,)).fetchall()
        results = []
        for r in registry:
            key = self._match_key(season_id, r["match_id"])
            if key in finalized:
                outcome = self.on_match_finalized(season_id, r["match_id"], actor=actor)
                # Report only matches where something was actually done (reward
                # newly posted, or yield newly applied), so the backfill button
                # shows meaningful counts and re-runs report nothing.
                if outcome.get("rewarded") or outcome.get("yield_applied"):
                    results.append({"match_id": r["match_id"], **outcome})
        return results

    # ------------------------------------------------------------------
    # manual finance (fines, umpire duty, sub cash)
    # ------------------------------------------------------------------
    def post_adjust(self, season_id: str, team_id: str, operation: str, amount: int,
                    comment: str, actor: str = "admin") -> dict:
        season_id = (season_id or "").strip().lower()
        operation = (operation or "").strip().lower()
        amount = int(amount or 0)
        if amount <= 0:
            raise ValueError("Amount must be a positive integer")
        if operation not in ("add", "remove"):
            raise ValueError("Operation must be add or remove")
        if not comment:
            raise ValueError("Comment is required")
        with self.db.write() as conn:
            team = conn.execute(
                "SELECT * FROM teams WHERE id = ? AND season_id = ?", (team_id, season_id)
            ).fetchone()
            if not team:
                raise ValueError("Team not found")
            account = self.bank.get_or_create_account("player", team["manager_player_id"],
                                                      conn=conn)
            before = int(account["liquid_cash"])
            delta = amount if operation == "add" else -amount
            self.bank.adjust(account["id"], delta, comment, tx_type="match_finance", conn=conn)
            after = before + delta
            conn.execute(
                "INSERT INTO season_finance_entries (id, season_id, team_id, team_name, type, "
                "operation, amount, comment, created_by, before_wallet, after_wallet, created_at) "
                "VALUES (?, ?, ?, ?, 'adjust', ?, ?, ?, ?, ?, ?, ?)",
                (secrets.token_hex(8), season_id, team_id, team["name"], operation, amount,
                 comment, actor, before, after, _now()))
        return {"ok": True, "team_id": team_id, "operation": operation, "amount": amount}

    def post_transfer(self, season_id: str, from_team_id: str, to_team_id: str, amount: int,
                      comment: str, actor: str = "admin") -> dict:
        season_id = (season_id or "").strip().lower()
        amount = int(amount or 0)
        if amount <= 0:
            raise ValueError("Amount must be a positive integer")
        if not from_team_id or not to_team_id:
            raise ValueError("Both teams are required")
        if from_team_id == to_team_id:
            raise ValueError("Source and target teams must be different")
        if not comment:
            raise ValueError("Comment is required")
        with self.db.write() as conn:
            from_team = conn.execute(
                "SELECT * FROM teams WHERE id = ? AND season_id = ?", (from_team_id, season_id)
            ).fetchone()
            to_team = conn.execute(
                "SELECT * FROM teams WHERE id = ? AND season_id = ?", (to_team_id, season_id)
            ).fetchone()
            if not from_team or not to_team:
                raise ValueError("One or more teams not found")
            from_account = self.bank.get_or_create_account("player", from_team["manager_player_id"],
                                                           conn=conn)
            to_account = self.bank.get_or_create_account("player", to_team["manager_player_id"],
                                                         conn=conn)
            from_before = int(from_account["liquid_cash"])
            to_before = int(to_account["liquid_cash"])
            self.bank.adjust(from_account["id"], -amount, comment, tx_type="match_finance",
                             conn=conn)
            self.bank.adjust(to_account["id"], amount, comment, tx_type="match_finance",
                             conn=conn)
            conn.execute(
                "INSERT INTO season_finance_entries (id, season_id, team_id, team_name, type, "
                "operation, amount, comment, created_by, from_team_id, to_team_id, "
                "before_wallet, after_wallet, created_at) VALUES (?, ?, ?, ?, 'transfer', NULL, "
                "?, ?, ?, ?, ?, ?, ?, ?)",
                (secrets.token_hex(8), season_id, from_team_id, from_team["name"], amount,
                 comment, actor, from_team_id, to_team_id, from_before,
                 from_before - amount, _now()))
        return {"ok": True, "from_team_id": from_team_id, "to_team_id": to_team_id,
                "amount": amount}

    # ------------------------------------------------------------------
    # undo
    # ------------------------------------------------------------------
    def undo_last_finance_entry(self, season_id: str, actor: str = "admin") -> dict:
        season_id = (season_id or "").strip().lower()
        with self.db.write() as conn:
            row = conn.execute(
                "SELECT * FROM season_finance_entries WHERE season_id = ? AND undone_at IS NULL "
                "ORDER BY created_at DESC, rowid DESC LIMIT 1", (season_id,)).fetchone()
            if not row:
                raise ValueError("Nothing to undo")
            entry = row_to_dict(row)
            ttype = entry.get("type")
            amount = int(entry.get("amount") or 0)

            def wallet_of(team_id):
                team = conn.execute("SELECT * FROM teams WHERE id = ?", (team_id,)).fetchone()
                if not team:
                    raise ValueError("Team no longer exists")
                return team, self.bank.get_or_create_account("player", team["manager_player_id"],
                                                             conn=conn)

            if ttype == "adjust":
                team, account = wallet_of(entry["team_id"])
                delta = amount if entry.get("operation") == "remove" else -amount
                if delta:
                    self.bank.adjust(account["id"], delta, f"Undo ({entry.get('comment') or ''})",
                                     tx_type="match_finance", conn=conn)
            elif ttype == "transfer":
                from_team, from_account = wallet_of(entry["from_team_id"])
                to_team, to_account = wallet_of(entry["to_team_id"])
                self.bank.adjust(from_account["id"], amount, "Undo transfer",
                                 tx_type="match_finance", conn=conn)
                self.bank.adjust(to_account["id"], -amount, "Undo transfer",
                                 tx_type="match_finance", conn=conn)
            elif ttype == "match_reward":
                if entry.get("team_id"):
                    # Legacy per-team reward: reverse that manager's wallet.
                    team, account = wallet_of(entry["team_id"])
                    self.bank.adjust(account["id"], -amount, "Undo match reward",
                                     tx_type="match_reward", conn=conn)
                else:
                    # Universal credit: reverse every player account
                    # (auto-vault accounts give the money back from the vault).
                    accounts = conn.execute(
                        "SELECT * FROM bank_accounts WHERE owner_type = 'player'").fetchall()
                    for acct in accounts:
                        if acct["auto_vault"]:
                            self.bank.unlock_amount(acct["id"], season_id, amount,
                                                    conn=conn, comment="Undo match reward")
                        else:
                            self.bank.adjust(acct["id"], -amount, "Undo match reward",
                                             tx_type="match_reward", conn=conn)
            else:
                raise ValueError(f"Cannot undo entry type '{ttype}'")
            conn.execute(
                "UPDATE season_finance_entries SET undone_at = ?, created_by = ? WHERE id = ?",
                (_now(), actor, entry["id"]))
            return {"ok": True, "type": ttype, "amount": amount}
