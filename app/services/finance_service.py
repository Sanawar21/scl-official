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
        """Budget Board, three sections (S2): teams in this season -> teams not
        in this season -> individual players. Every row carries `section` and
        `kind`; wallets are the manager's/player's liquid cash, `locked` the
        vault capital."""
        season_id = (season_id or "").strip().lower()
        if not season_id:
            return []
        rows = []
        with self.db.read() as conn:
            season_teams = conn.execute(
                "SELECT * FROM teams WHERE season_id = ?", (season_id,)).fetchall()
            global_teams = [dict(r) for r in conn.execute(
                "SELECT * FROM global_teams").fetchall()]
            in_season_gids = {t["global_team_id"] for t in season_teams
                              if (t["global_team_id"] or "").strip()}
            in_season_gids |= {t["id"] for t in season_teams}

            def _gp_name(gp_id):
                if not gp_id:
                    return None
                row = conn.execute(
                    "SELECT name FROM global_players WHERE id = ?", (gp_id,)).fetchone()
                return row["name"] if row else gp_id

            def _wallet(owner_id):
                acct = self.bank.account_for_owner("player", owner_id)
                if not acct:
                    return (0, 0)
                return (int(acct["liquid_cash"]), int(acct["locked_capital"]))

            # 1) teams playing this season
            for t in season_teams:
                mgr = (t["manager_player_id"] or "").strip()
                wallet, locked = _wallet(mgr) if mgr else (0, 0)
                rows.append({
                    "section": "playing", "kind": "team",
                    "team_id": t["id"], "name": t["name"],
                    "manager_name": _gp_name(mgr),
                    "account_ref": f"team:{t['id']}" if mgr else None,
                    "wallet": wallet, "locked": locked,
                    "credits_remaining": int(t["credits_remaining"] or 0),
                    "players_count": len(json_loads(t["players"], [])),
                    "bench_count": len(json_loads(t["bench"], [])),
                })
            # 2) persistent teams NOT in this season (even without a manager)
            for gt in global_teams:
                if gt["id"] in in_season_gids:
                    continue
                mgr = (gt.get("manager_player_id") or "").strip()
                wallet, locked = _wallet(mgr) if mgr else (0, 0)
                rows.append({
                    "section": "non_playing", "kind": "team",
                    "team_id": gt["id"], "name": gt["name"],
                    "manager_name": _gp_name(mgr),
                    "account_ref": f"team:{gt['id']}" if mgr else None,
                    "wallet": wallet, "locked": locked,
                    "credits_remaining": None, "players_count": 0, "bench_count": 0,
                })
            # 3) individual players — EVERY global player not managing a team
            manager_ids = {(t["manager_player_id"] or "").strip() for t in season_teams}
            manager_ids |= {(gt.get("manager_player_id") or "").strip() for gt in global_teams}
            for gp in conn.execute(
                    "SELECT id, name FROM global_players ORDER BY name").fetchall():
                if gp["id"] in manager_ids:
                    continue
                acct = self.bank.account_for_owner("player", gp["id"])
                rows.append({
                    "section": "players", "kind": "player",
                    "team_id": gp["id"], "name": gp["name"],
                    "manager_name": None,
                    "account_ref": f"player:{gp['id']}",
                    "wallet": int(acct["liquid_cash"]) if acct else 0,
                    "locked": int(acct["locked_capital"]) if acct else 0,
                    "credits_remaining": None, "players_count": 0, "bench_count": 0,
                })
        order = {"playing": 0, "non_playing": 1, "players": 2}
        rows.sort(key=lambda r: (order.get(r["section"], 9), r["name"].lower()))
        return rows

    # ------------------------------------------------------------------
    # squad-cost levy (S2: average squad cost charged to non-spenders)
    # ------------------------------------------------------------------
    def apply_squad_levy(self, season_id: str, actor: str = "admin") -> dict:
        """Deduct the average squad cost of the season from wallets that didn't
        spend in the auction (playing teams that spent are exempt). Liquid first,
        then the vault position for auto accounts; never below zero. Idempotent
        per season (one `squad_levy` marker entry)."""
        season_id = (season_id or "").strip().lower()
        with self.db.write() as conn:
            already = conn.execute(
                "SELECT 1 FROM season_finance_entries WHERE season_id = ? "
                "AND type = 'squad_levy'", (season_id,)).fetchone()
            if already:
                return {"applied": False, "levy": 0, "charged": 0, "exempt": 0}
            teams = conn.execute(
                "SELECT * FROM teams WHERE season_id = ?", (season_id,)).fetchall()
            if not teams:
                return {"applied": False, "levy": 0, "charged": 0, "exempt": 0}
            total_spent = sum(int(t["spent"] or 0) for t in teams)
            if total_spent <= 0:
                return {"applied": False, "levy": 0, "charged": 0, "exempt": len(teams)}
            avg = round(total_spent / len(teams))
            exempt = {t["manager_player_id"] for t in teams if int(t["spent"] or 0) > 0}
            accounts = conn.execute(
                "SELECT * FROM bank_accounts WHERE owner_type = 'player'").fetchall()
            charged = exempt_count = 0
            for acct in accounts:
                if acct["owner_id"] in exempt:
                    exempt_count += 1
                    continue
                self._levy_one(conn, acct, season_id, avg)
                charged += 1
            conn.execute(
                "INSERT INTO season_finance_entries (id, season_id, match_id, team_id, "
                "team_name, type, operation, amount, comment, created_by, before_wallet, "
                "after_wallet, created_at) VALUES (?, ?, NULL, NULL, 'non-spenders', "
                "'squad_levy', NULL, ?, ?, ?, NULL, NULL, ?)",
                (secrets.token_hex(8), season_id, avg,
                 f"Squad cost levy ({avg}) — {charged} charged, {exempt_count} exempt",
                 actor, _now()))
        return {"applied": True, "levy": avg, "charged": charged, "exempt": exempt_count}

    def _levy_one(self, conn, account, season_id: str, amount: int) -> None:
        """Deduct from liquid first, then from the season's vault position."""
        remaining = int(amount)
        liquid = int(account["liquid_cash"])
        if liquid > 0:
            take = min(remaining, liquid)
            self.bank.adjust(account["id"], -take,
                             f"Squad cost levy ({amount})", tx_type="squad_levy", conn=conn)
            remaining -= take
        if remaining > 0:
            self.bank.seize(account["id"], season_id, remaining,
                            conn=conn, comment=f"Squad cost levy ({amount})")

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

    def _team_name(self, ref: str) -> str:
        """Resolve a team/owner reference to a display name.

        Accepts season team ids (legacy ledger entries), global team ids, or
        owner ids (global player ids) — the references the admin forms post.
        """
        ref = (ref or "").strip()
        if not ref:
            return ""
        if ":" in ref:
            ref = ref.split(":", 1)[1]
        with self.db.read() as conn:
            for table in ("teams", "global_teams", "global_players"):
                row = conn.execute(
                    f"SELECT name FROM {table} WHERE id = ?", (ref,)).fetchone()
                if row:
                    return row["name"]
        return ref

    def _resolve_account(self, conn, ref):
        """Resolve a form reference to (owner_id, display_name, account).

        Accepts `team:<season-or-global-team-id>`, `player:<owner-id>`, or a
        bare season team id (legacy forms/tests). The account is always a
        player wallet (manager's wallet == team money).
        """
        ref = (ref or "").strip()
        if not ref:
            raise ValueError("Account is required")
        owner_id = display = None
        if ":" in ref:
            prefix, value = ref.split(":", 1)
            value = (value or "").strip()
            if prefix == "team":
                team = conn.execute(
                    "SELECT * FROM teams WHERE id = ?", (value,)).fetchone()
                if not team:
                    team = conn.execute(
                        "SELECT * FROM global_teams WHERE id = ?", (value,)).fetchone()
                if not team:
                    raise ValueError("Team not found")
                if not team["manager_player_id"]:
                    raise ValueError("Team has no manager to adjust")
                owner_id = team["manager_player_id"]
                display = team["name"]
            elif prefix == "player":
                owner_id = value
                gp = conn.execute(
                    "SELECT name FROM global_players WHERE id = ?", (owner_id,)).fetchone()
                display = gp["name"] if gp else owner_id
            else:
                raise ValueError(f"Unknown account type '{prefix}'")
        else:
            team = conn.execute(
                "SELECT * FROM teams WHERE id = ?", (ref,)).fetchone()
            if team and team["manager_player_id"]:
                owner_id = team["manager_player_id"]
                display = team["name"]
            else:
                owner_id = ref
                gp = conn.execute(
                    "SELECT name FROM global_players WHERE id = ?", (owner_id,)).fetchone()
                display = gp["name"] if gp else owner_id
        account = self.bank.get_or_create_account("player", owner_id, conn=conn)
        return owner_id, display, account

    def _account_of(self, conn, ref):
        """Resolve a stored ledger reference (owner id, `team:`/`player:` ref, or
        legacy season team id) to a (owner_id, account) pair for undo."""
        ref = (ref or "").strip()
        if not ref:
            raise ValueError("Missing account reference")
        if ":" in ref:
            prefix, value = ref.split(":", 1)
            value = (value or "").strip()
            if prefix == "team":
                team = conn.execute(
                    "SELECT * FROM teams WHERE id = ?", (value,)).fetchone()
                if not team:
                    team = conn.execute(
                        "SELECT * FROM global_teams WHERE id = ?", (value,)).fetchone()
                if not team or not team["manager_player_id"]:
                    raise ValueError("Team no longer exists")
                owner_id = team["manager_player_id"]
            elif prefix == "player":
                owner_id = value
            else:
                raise ValueError(f"Unknown account type '{prefix}'")
        else:
            team = conn.execute(
                "SELECT * FROM teams WHERE id = ?", (ref,)).fetchone()
            owner_id = team["manager_player_id"] if (team and team["manager_player_id"]) else ref
        return owner_id, self.bank.get_or_create_account("player", owner_id, conn=conn)

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
    def post_adjust(self, season_id: str, account_ref: str, operation: str, amount: int,
                    comment: str, actor: str = "admin") -> dict:
        """Add/remove funds on ANY wallet (playing team, non-playing team, or
        individual player). Adds respect auto mode (credit() routes auto
        accounts straight to the vault); removes always come from liquid cash."""
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
            owner_id, display, account = self._resolve_account(conn, account_ref)
            before = int(account["liquid_cash"])
            if operation == "add":
                self.bank.credit(account["id"], amount, comment, tx_type="admin_adjust",
                                 season_id=season_id, conn=conn)
            else:
                self.bank.adjust(account["id"], -amount, comment, tx_type="admin_adjust",
                                 conn=conn)
            after = int(conn.execute(
                "SELECT liquid_cash FROM bank_accounts WHERE id = ?", (account["id"],)
            ).fetchone()["liquid_cash"])
            conn.execute(
                "INSERT INTO season_finance_entries (id, season_id, team_id, team_name, type, "
                "operation, amount, comment, created_by, before_wallet, after_wallet, created_at) "
                "VALUES (?, ?, ?, ?, 'adjust', ?, ?, ?, ?, ?, ?, ?)",
                (secrets.token_hex(8), season_id, owner_id, display, operation, amount,
                 comment, actor, before, after, _now()))
        return {"ok": True, "owner_id": owner_id, "operation": operation, "amount": amount}

    def post_transfer(self, season_id: str, from_ref: str, to_ref: str, amount: int,
                      comment: str, actor: str = "admin") -> dict:
        """Move funds between any two wallets (teams or individual players)."""
        season_id = (season_id or "").strip().lower()
        amount = int(amount or 0)
        if amount <= 0:
            raise ValueError("Amount must be a positive integer")
        if not from_ref or not to_ref:
            raise ValueError("Both accounts are required")
        if not comment:
            raise ValueError("Comment is required")
        with self.db.write() as conn:
            f_owner, f_display, from_account = self._resolve_account(conn, from_ref)
            t_owner, t_display, to_account = self._resolve_account(conn, to_ref)
            if f_owner == t_owner:
                raise ValueError("Source and target accounts must be different")
            from_before = int(from_account["liquid_cash"])
            to_before = int(to_account["liquid_cash"])
            self.bank.adjust(from_account["id"], -amount, comment, tx_type="admin_adjust",
                             conn=conn)
            self.bank.adjust(to_account["id"], amount, comment, tx_type="admin_adjust",
                             conn=conn)
            conn.execute(
                "INSERT INTO season_finance_entries (id, season_id, team_id, team_name, type, "
                "operation, amount, comment, created_by, from_team_id, to_team_id, "
                "before_wallet, after_wallet, created_at) VALUES (?, ?, ?, ?, 'transfer', NULL, "
                "?, ?, ?, ?, ?, ?, ?, ?)",
                (secrets.token_hex(8), season_id, f_owner, f_display, amount,
                 comment, actor, from_ref, to_ref, from_before,
                 from_before - amount, _now()))
        return {"ok": True, "from_ref": from_ref, "to_ref": to_ref,
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

            def account_of(ref):
                if not ref:
                    raise ValueError("Account no longer exists")
                return self._account_of(conn, ref)

            if ttype == "adjust":
                _, account = account_of(entry["team_id"])
                delta = amount if entry.get("operation") == "remove" else -amount
                if delta:
                    self.bank.adjust(account["id"], delta, f"Undo ({entry.get('comment') or ''})",
                                     tx_type="match_finance", conn=conn)
            elif ttype == "transfer":
                _, from_account = account_of(entry["from_team_id"])
                _, to_account = account_of(entry["to_team_id"])
                self.bank.adjust(from_account["id"], amount, "Undo transfer",
                                 tx_type="match_finance", conn=conn)
                self.bank.adjust(to_account["id"], -amount, "Undo transfer",
                                 tx_type="match_finance", conn=conn)
            elif ttype == "match_reward":
                if entry.get("team_id"):
                    # Legacy per-team reward: reverse that manager's wallet.
                    _, account = account_of(entry["team_id"])
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
