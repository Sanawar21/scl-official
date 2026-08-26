"""Wager markets, per the SCL Wager & Risk Management Protocol (wager.txt).

Pooled Yes/No AMM on the central bank (liquid cash):

    propose + first stake
      -> admin calibration (blind estimates; consensus = average)
      -> financial veto (solvency check; veto refunds everything)
      -> peer phase (betting open on Yes/No pools)
      -> house injection (admin balances pools with House funds)
      -> resolution (winning side splits the pot proportionally; the House
         tops up to guarantee fair odds when peer interest is thin)
    or -> voided (ambiguous/impossible/fixing -> 100% refunds)

Payout model (doc-faithful):
  - calibration locks p_b = probability of side_b; p_a = 100 - p_b.
  - fair(side) = 100 / p(side).
  - pot = yes stakes + no stakes + house injections.
  - at resolution, winners receive max(proportional split of the pot,
    stake * fair(side)); the House pays the difference when the pot is thin.
"""
import secrets
from datetime import datetime, timezone

from ..db import json_dumps, json_loads, row_to_dict, rows_to_dicts

# Wager statuses
STATUS_PROPOSED = "proposed"
STATUS_CALIBRATING = "calibrating"
STATUS_VETTED = "vetted"
STATUS_FROZEN = "frozen"
STATUS_RESOLVED = "resolved"
STATUS_VOIDED = "voided"

# Bet statuses
BET_OPEN = "open"
BET_SETTLED = "settled"
BET_REFUNDED = "refunded"

# The House is a bank account of its own.
HOUSE_TYPE = "house"
HOUSE_ID = "house"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class WagerService:
    def __init__(self, db, bank_service):
        self.db = db
        self.bank = bank_service

    # ------------------------------------------------------------------
    # queries
    # ------------------------------------------------------------------
    @staticmethod
    def house_coverage(wager: dict) -> dict:
        """The House's automatic guarantee, live at any point during the wager.

        Mirrors `resolve()`: if side X wins, winners are guaranteed
        round(stakes_X * fair_X); when that exceeds the pot, the House tops up
        the difference. Returns {"cover_a": n, "cover_b": n} — how much the
        House covers if side_a wins, and if side_b wins. Auto-adjusts as new
        bids land because it reads the live totals each call.
        """
        yes = int(wager.get("yes_total") or 0)
        no = int(wager.get("no_total") or 0)
        pot = yes + no + int(wager.get("house_injected") or 0)
        p_b = float(wager.get("house_probability") or 50.0)
        fair_a = 100.0 / (100.0 - p_b) if p_b < 100 else 0.0
        fair_b = 100.0 / p_b if p_b > 0 else 0.0
        return {
            "cover_a": max(0, int(round(yes * fair_a)) - pot),
            "cover_b": max(0, int(round(no * fair_b)) - pot),
        }

    def list_wagers(self, season_id: str = None) -> list:
        with self.db.read() as conn:
            rows = conn.execute(
                "SELECT w.*, "
                "COALESCE((SELECT SUM(amount) FROM wager_bets b WHERE b.wager_id = w.id "
                "          AND b.side = w.side_a AND b.status = 'open'), 0) AS yes_total, "
                "COALESCE((SELECT SUM(amount) FROM wager_bets b WHERE b.wager_id = w.id "
                "          AND b.side = w.side_b AND b.status = 'open'), 0) AS no_total, "
                "COALESCE((SELECT COUNT(*) FROM wager_bets b WHERE b.wager_id = w.id), 0) "
                "AS bet_count "
                "FROM wagers w ORDER BY w.created_at DESC"
            ).fetchall()
        wagers = [dict(r) for r in rows]
        for w in wagers:
            w["pot"] = int(w["yes_total"] or 0) + int(w["no_total"] or 0) + int(w["house_injected"] or 0)
            w["calibration_estimates"] = json_loads(w.get("calibration_estimates"), [])
            w.update(self.house_coverage(w))
        return wagers

    def get_wager(self, wager_id: str) -> dict:
        with self.db.read() as conn:
            row = conn.execute("SELECT * FROM wagers WHERE id = ?", (wager_id,)).fetchone()
            if not row:
                return None
            wager = row_to_dict(row)
            wager["calibration_estimates"] = json_loads(wager.get("calibration_estimates"), [])
            wager["history"] = json_loads(wager.get("history"), [])
            bets = rows_to_dicts(conn.execute(
                "SELECT * FROM wager_bets WHERE wager_id = ? ORDER BY created_at ASC",
                (wager_id,),
            ).fetchall())
            wager["bets"] = bets
            yes = sum(int(b["amount"]) for b in bets if b["side"] == wager["side_a"] and b["status"] == BET_OPEN)
            no = sum(int(b["amount"]) for b in bets if b["side"] == wager["side_b"] and b["status"] == BET_OPEN)
            wager["yes_total"] = yes
            wager["no_total"] = no
            wager["pot"] = yes + no + int(wager["house_injected"] or 0)
            wager.update(self.house_coverage(wager))
            return wager

    def my_bets(self, user_id: str) -> list:
        with self.db.read() as conn:
            rows = conn.execute(
                "SELECT b.*, w.title, w.status AS wager_status, w.side_a, w.side_b, "
                "w.winning_side, w.resolved_at, w.void_reason, w.veto_reason "
                "FROM wager_bets b JOIN wagers w ON w.id = b.wager_id "
                "WHERE b.user_id = ? ORDER BY b.created_at DESC",
                (user_id,),
            ).fetchall()
            return rows_to_dicts(rows)

    def house_account(self) -> dict:
        with self.db.read() as conn:
            return row_to_dict(conn.execute(
                "SELECT * FROM bank_accounts WHERE owner_type = ? AND owner_id = ?",
                (HOUSE_TYPE, HOUSE_ID),
            ).fetchone())

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _fair_odds(wager: dict, side: str) -> float:
        p_b = float(wager.get("house_probability") or 50.0)
        p = p_b if side == wager.get("side_b") else 100.0 - p_b
        if p <= 0:
            p = 0.01
        return 100.0 / p

    def _history(self, conn, wager_id: str, action: str, actor: str, note: str = ""):
        row = conn.execute("SELECT history FROM wagers WHERE id = ?", (wager_id,)).fetchone()
        history = json_loads(row["history"], []) if row else []
        history.append({"at": _now(), "action": action, "actor": actor, "note": note})
        conn.execute(
            "UPDATE wagers SET history = ?, updated_at = ? WHERE id = ?",
            (json_dumps(history), _now(), wager_id),
        )

    def _get_wager_row(self, conn, wager_id: str) -> dict:
        row = conn.execute("SELECT * FROM wagers WHERE id = ?", (wager_id,)).fetchone()
        if not row:
            raise ValueError("Wager not found")
        return row_to_dict(row)

    def _require_status(self, wager: dict, allowed: tuple):
        if wager["status"] not in allowed:
            raise ValueError(f"Wager is not in an allowed state for this action ({wager['status']})")

    def _linked_account(self, conn, user: dict) -> dict:
        """Player's bank account; raises unless the user is linked to a player."""
        gp_id = (user or {}).get("global_player_id")
        if not gp_id:
            raise ValueError("Your account must be linked to a player profile to wager")
        return self.bank.get_or_create_account("player", gp_id, conn=conn)

    def _refund_open_bets(self, conn, wager_id: str, note: str):
        """Refund every open bet 100% (void/veto path)."""
        bets = conn.execute(
            "SELECT * FROM wager_bets WHERE wager_id = ? AND status = ?",
            (wager_id, BET_OPEN),
        ).fetchall()
        for bet in bets:
            self._credit(conn, bet, int(bet["amount"]), "wager_refund",
                         f"{note} — refund of {bet['amount']}")
        return len(bets)

    def _credit(self, conn, bet, amount: int, tx_type: str, comment: str):
        """Credit a bettor's liquid cash (payout or refund) and settle the bet."""
        user = conn.execute("SELECT global_player_id FROM users WHERE id = ?",
                            (bet["user_id"],)).fetchone()
        if user and user["global_player_id"]:
            account = self.bank.get_or_create_account("player", user["global_player_id"], conn=conn)
            self.bank.adjust(account["id"], amount, comment, tx_type=tx_type, conn=conn)
        conn.execute(
            "UPDATE wager_bets SET status = ?, payout = ?, settled_at = ? WHERE id = ?",
            (BET_SETTLED if tx_type != "wager_refund" else BET_REFUNDED,
             amount, _now(), bet["id"]),
        )

    # ------------------------------------------------------------------
    # player actions
    # ------------------------------------------------------------------
    def create_wager(self, user: dict, title: str, description: str,
                     side_a: str, side_b: str, side: str, amount: int,
                     season_id: str = None) -> dict:
        title = (title or "").strip()
        side_a = (side_a or "").strip() or "Yes"
        side_b = (side_b or "").strip() or "No"
        amount = int(amount or 0)
        if not title:
            raise ValueError("A title is required")
        if len(title) > 200:
            raise ValueError("Title is too long")
        if side_a == side_b:
            raise ValueError("The two sides must be different")
        if side not in (side_a, side_b):
            raise ValueError("Invalid side for this market")
        if amount <= 0:
            raise ValueError("The opening stake must be positive")

        wager_id = secrets.token_hex(8)
        with self.db.write() as conn:
            account = self._linked_account(conn, user)
            if int(account["liquid_cash"]) < amount:
                raise ValueError("Insufficient liquid cash for the opening stake")
            account = self.bank.adjust(account["id"], -amount,
                                       f"Opening stake on '{title}' ({side})",
                                       tx_type="wager_stake", conn=conn)
            stake_tx = conn.execute(
                "SELECT id FROM bank_transactions WHERE account_id = ? ORDER BY rowid DESC LIMIT 1",
                (account["id"],),
            ).fetchone()
            conn.execute(
                "INSERT INTO wagers (id, season_id, title, description, side_a, side_b, status, "
                "accepting_bets, initiator_user_id, initiator_name, house_probability, "
                "calibration_estimates, house_injected, history, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 'proposed', 0, ?, ?, NULL, '[]', 0, '[]', ?, ?)",
                (wager_id, (season_id or "").strip() or None, title, (description or "").strip(),
                 side_a, side_b, (user or {}).get("id"), (user or {}).get("username") or "?",
                 _now(), _now()),
            )
            conn.execute(
                "INSERT INTO wager_bets (id, wager_id, user_id, username, side, amount, status, "
                "stake_tx_id, created_at) VALUES (?, ?, ?, ?, ?, ?, 'open', ?, ?)",
                (secrets.token_hex(8), wager_id, (user or {}).get("id"),
                 (user or {}).get("username") or "?", side, amount,
                 stake_tx["id"] if stake_tx else None, _now()),
            )
            self._history(conn, wager_id, "propose", (user or {}).get("username") or "?",
                          f"opened with a {amount} stake on {side}")
        return self.get_wager(wager_id)

    def place_bet(self, user: dict, wager_id: str, side: str, amount: int) -> dict:
        amount = int(amount or 0)
        if amount <= 0:
            raise ValueError("Wager amount must be positive")
        bet_id = secrets.token_hex(8)
        with self.db.write() as conn:
            wager = self._get_wager_row(conn, wager_id)
            self._require_status(wager, (STATUS_VETTED,))
            if not wager["accepting_bets"]:
                raise ValueError("This market is not accepting bets")
            if side not in (wager["side_a"], wager["side_b"]):
                raise ValueError("Invalid side for this market")
            account = self._linked_account(conn, user)
            if int(account["liquid_cash"]) < amount:
                raise ValueError("Insufficient liquid cash")
            account = self.bank.adjust(account["id"], -amount,
                                       f"Stake on '{wager['title']}' ({side})",
                                       tx_type="wager_stake", conn=conn)
            stake_tx = conn.execute(
                "SELECT id FROM bank_transactions WHERE account_id = ? ORDER BY rowid DESC LIMIT 1",
                (account["id"],),
            ).fetchone()
            conn.execute(
                "INSERT INTO wager_bets (id, wager_id, user_id, username, side, amount, status, "
                "stake_tx_id, created_at) VALUES (?, ?, ?, ?, ?, ?, 'open', ?, ?)",
                (bet_id, wager_id, (user or {}).get("id"), (user or {}).get("username") or "?",
                 side, amount, stake_tx["id"] if stake_tx else None, _now()),
            )
            self._history(conn, wager_id, "bet", (user or {}).get("username") or "?",
                          f"{amount} on {side}")
        return self.get_wager(wager_id)

    # ------------------------------------------------------------------
    # admin: create wager + bet on behalf of players
    # ------------------------------------------------------------------
    def admin_create_wager(self, actor: str, title: str, description: str,
                           side_a: str, side_b: str,
                           season_id: str = None) -> dict:
        """Admin creates a wager directly (no opening stake from a player)."""
        title = (title or "").strip()
        side_a = (side_a or "").strip() or "Yes"
        side_b = (side_b or "").strip() or "No"
        if not title:
            raise ValueError("A title is required")
        if len(title) > 200:
            raise ValueError("Title is too long")
        if side_a == side_b:
            raise ValueError("The two sides must be different")
        wager_id = secrets.token_hex(8)
        with self.db.write() as conn:
            conn.execute(
                "INSERT INTO wagers (id, season_id, title, description, side_a, side_b, status, "
                "accepting_bets, initiator_user_id, initiator_name, house_probability, "
                "calibration_estimates, house_injected, history, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 'proposed', 0, NULL, ?, NULL, '[]', 0, '[]', ?, ?)",
                (wager_id, (season_id or "").strip() or None, title,
                 (description or "").strip(), side_a, side_b, actor, _now(), _now()),
            )
            self._history(conn, wager_id, "propose", actor, "created by admin")
        return self.get_wager(wager_id)

    def admin_bet_on_behalf(self, actor: str, wager_id: str,
                            global_player_id: str, side: str, amount: int) -> dict:
        """Admin places a bet on behalf of a player, deducting from their wallet."""
        amount = int(amount or 0)
        if amount <= 0:
            raise ValueError("Bet amount must be positive")
        side = (side or "").strip()
        global_player_id = (global_player_id or "").strip()
        if not global_player_id:
            raise ValueError("Player is required")
        bet_id = secrets.token_hex(8)
        with self.db.write() as conn:
            wager = self._get_wager_row(conn, wager_id)
            self._require_status(wager, (STATUS_PROPOSED, STATUS_CALIBRATING,
                                         STATUS_VETTED, STATUS_FROZEN))
            if side not in (wager["side_a"], wager["side_b"]):
                raise ValueError("Invalid side for this market")
            # Get player name for the bet record
            gp = conn.execute("SELECT name FROM global_players WHERE id = ?",
                              (global_player_id,)).fetchone()
            player_name = gp["name"] if gp else global_player_id
            account = self.bank.get_or_create_account("player", global_player_id, conn=conn)
            if int(account["liquid_cash"]) < amount:
                raise ValueError(f"Insufficient liquid cash ({account['liquid_cash']})")
            account = self.bank.adjust(account["id"], -amount,
                                       f"Bet on '{wager['title']}' ({side}) by admin",
                                       tx_type="wager_stake", conn=conn)
            stake_tx = conn.execute(
                "SELECT id FROM bank_transactions WHERE account_id = ? ORDER BY rowid DESC LIMIT 1",
                (account["id"],),
            ).fetchone()
            # Admin-initiated bets use 'admin' as user_id; username stores the player name
            conn.execute(
                "INSERT INTO wager_bets (id, wager_id, user_id, username, side, amount, status, "
                "stake_tx_id, created_at) VALUES (?, ?, 'admin', ?, ?, ?, 'open', ?, ?)",
                (bet_id, wager_id, player_name, side, amount,
                 stake_tx["id"] if stake_tx else None, _now()),
            )
            self._history(conn, wager_id, "bet", actor,
                          f"{amount} on {side} for {player_name}")
        return self.get_wager(wager_id)

    # ------------------------------------------------------------------
    # admin: calibration & lifecycle
    # ------------------------------------------------------------------
    def calibrate(self, wager_id: str, actor: str, estimate) -> dict:
        estimate = float(estimate or 0)
        if estimate <= 0 or estimate >= 100:
            raise ValueError("Probability estimate must be between 0 and 100 exclusive")
        with self.db.write() as conn:
            wager = self._get_wager_row(conn, wager_id)
            self._require_status(wager, (STATUS_PROPOSED, STATUS_CALIBRATING))
            estimates = json_loads(wager["calibration_estimates"], [])
            estimates.append({"admin": actor, "estimate": estimate})
            house_probability = sum(e["estimate"] for e in estimates) / len(estimates)
            conn.execute(
                "UPDATE wagers SET house_probability = ?, calibration_estimates = ?, "
                "status = 'calibrating', updated_at = ? WHERE id = ?",
                (house_probability, json_dumps(estimates), _now(), wager_id),
            )
            self._history(conn, wager_id, "calibrate", actor,
                          f"estimate {estimate}% (consensus {round(house_probability, 2)}%)")
        return self.get_wager(wager_id)

    def finalize_calibration(self, wager_id: str, actor: str) -> dict:
        with self.db.write() as conn:
            wager = self._get_wager_row(conn, wager_id)
            self._require_status(wager, (STATUS_CALIBRATING,))
            if wager.get("house_probability") is None:
                raise ValueError("Calibrate the market before finalizing")
            conn.execute(
                "UPDATE wagers SET status = 'vetted', accepting_bets = 1, updated_at = ? "
                "WHERE id = ?",
                (_now(), wager_id),
            )
            self._history(conn, wager_id, "finalize", actor, "betting opened")
        return self.get_wager(wager_id)

    def veto(self, wager_id: str, actor: str, reason: str = "") -> dict:
        """Financial (bankruptcy) veto: cancel the market, refund every stake."""
        with self.db.write() as conn:
            wager = self._get_wager_row(conn, wager_id)
            self._require_status(wager, (STATUS_PROPOSED, STATUS_CALIBRATING))
            conn.execute(
                "UPDATE wagers SET status = 'voided', accepting_bets = 0, veto_reason = ?, "
                "updated_at = ? WHERE id = ?",
                ((reason or "").strip(), _now(), wager_id),
            )
            refunded = self._refund_open_bets(conn, wager_id, "Vetoed")
            self._history(conn, wager_id, "veto", actor,
                          f"{reason or 'bankruptcy veto'} — refunded {refunded} stake(s)")
        return self.get_wager(wager_id)

    def freeze(self, wager_id: str, actor: str) -> dict:
        with self.db.write() as conn:
            wager = self._get_wager_row(conn, wager_id)
            self._require_status(wager, (STATUS_VETTED,))
            conn.execute(
                "UPDATE wagers SET status = 'frozen', accepting_bets = 0, updated_at = ? "
                "WHERE id = ?",
                (_now(), wager_id),
            )
            self._history(conn, wager_id, "freeze", actor, "pools frozen")
        return self.get_wager(wager_id)

    def unfreeze(self, wager_id: str, actor: str) -> dict:
        with self.db.write() as conn:
            wager = self._get_wager_row(conn, wager_id)
            self._require_status(wager, (STATUS_FROZEN,))
            conn.execute(
                "UPDATE wagers SET status = 'vetted', accepting_bets = 1, updated_at = ? "
                "WHERE id = ?",
                (_now(), wager_id),
            )
            self._history(conn, wager_id, "unfreeze", actor, "pools reopened")
        return self.get_wager(wager_id)

    def remove_bet(self, wager_id: str, bet_id: str, actor: str) -> dict:
        """Admin-only: remove a single open bet and refund its stake.

        A correction tool for erroneous stakes — the bettor's liquid cash is
        refunded 100% and the bet is marked `refunded`; pools/pot/house
        coverage recompute automatically since they read live open bets.
        Only allowed while the market is still open (not resolved/voided)
        and only for bets still in `open` status.
        """
        with self.db.write() as conn:
            wager = self._get_wager_row(conn, wager_id)
            self._require_status(wager, (STATUS_PROPOSED, STATUS_CALIBRATING,
                                         STATUS_VETTED, STATUS_FROZEN))
            bet = conn.execute(
                "SELECT * FROM wager_bets WHERE id = ? AND wager_id = ?",
                (bet_id, wager_id)).fetchone()
            if not bet:
                raise ValueError("Bet not found on this wager")
            if bet["status"] != BET_OPEN:
                raise ValueError("Only open bets can be removed")
            amount = int(bet["amount"])
            self._credit(conn, bet, amount, "wager_refund",
                         f"Bet removed by {actor} — refund of {amount}")
            self._history(conn, wager_id, "remove_bet", actor,
                          f"removed {bet['username']}'s {amount} stake on {bet['side']}")
        return self.get_wager(wager_id)

    def inject_house(self, wager_id: str, actor: str, amount: int) -> dict:
        amount = int(amount or 0)
        if amount <= 0:
            raise ValueError("Injection amount must be positive")
        with self.db.write() as conn:
            wager = self._get_wager_row(conn, wager_id)
            self._require_status(wager, (STATUS_PROPOSED, STATUS_CALIBRATING,
                                         STATUS_VETTED, STATUS_FROZEN))
            house = self.bank.get_or_create_account(HOUSE_TYPE, HOUSE_ID, conn=conn)
            if int(house["liquid_cash"]) < amount:
                raise ValueError("Insufficient House funds")
            self.bank.adjust(house["id"], -amount,
                             f"House injection into '{wager['title']}'",
                             tx_type="house_inject", conn=conn)
            conn.execute(
                "UPDATE wagers SET house_injected = house_injected + ?, updated_at = ? "
                "WHERE id = ?",
                (amount, _now(), wager_id),
            )
            self._history(conn, wager_id, "inject", actor, f"House injected {amount}")
        return self.get_wager(wager_id)

    # ------------------------------------------------------------------
    # admin: resolution
    # ------------------------------------------------------------------
    def resolve(self, wager_id: str, actor: str, winning_side: str) -> dict:
        with self.db.write() as conn:
            wager = self._get_wager_row(conn, wager_id)
            self._require_status(wager, (STATUS_VETTED, STATUS_FROZEN))
            if winning_side not in (wager["side_a"], wager["side_b"]):
                raise ValueError("Invalid winning side")
            if wager.get("house_probability") is None:
                raise ValueError("Market must be calibrated before resolution")

            bets = conn.execute(
                "SELECT * FROM wager_bets WHERE wager_id = ? AND status = ?",
                (wager_id, BET_OPEN),
            ).fetchall()
            winners = [b for b in bets if b["side"] == winning_side]
            losers = [b for b in bets if b["side"] != winning_side]
            winning_stakes = sum(int(b["amount"]) for b in winners)
            pot = winning_stakes + sum(int(b["amount"]) for b in losers) + int(wager["house_injected"] or 0)

            if not winners:
                raise ValueError("Nobody wagered on the winning side — void the market instead")

            fair = self._fair_odds(wager, winning_side)
            guaranteed = int(round(winning_stakes * fair))
            house = self.bank.get_or_create_account(HOUSE_TYPE, HOUSE_ID, conn=conn)

            if guaranteed > pot:
                top_up = guaranteed - pot
                if int(house["liquid_cash"]) < top_up:
                    raise ValueError(
                        f"House cannot guarantee payouts: needs {top_up} but has only "
                        f"{house['liquid_cash']} liquid. Inject House funds or void the market.")
                self.bank.adjust(house["id"], -top_up,
                                 f"Guarantee top-up for '{wager['title']}'",
                                 tx_type="house_inject", conn=conn)
                payouts = {b["id"]: int(round(int(b["amount"]) * fair)) for b in winners}
                house_note = f"; House topped up {top_up}"
            else:
                payouts = {b["id"]: int(round(int(b["amount"]) * pot / winning_stakes))
                           for b in winners}
                house_note = ""

            for bet in winners:
                self._credit(conn, bet, payouts[bet["id"]], "wager_payout",
                             f"Winning payout on '{wager['title']}' ({winning_side}){house_note}")
            for bet in losers:
                conn.execute(
                    "UPDATE wager_bets SET status = 'settled', payout = 0, settled_at = ? "
                    "WHERE id = ?",
                    (_now(), bet["id"]),
                )
            conn.execute(
                "UPDATE wagers SET status = 'resolved', winning_side = ?, accepting_bets = 0, "
                "resolved_at = ?, updated_at = ? WHERE id = ?",
                (winning_side, _now(), _now(), wager_id),
            )
            self._history(conn, wager_id, "resolve", actor,
                          f"{winning_side} won{house_note}")
        return self.get_wager(wager_id)

    def void(self, wager_id: str, actor: str, reason: str = "") -> dict:
        """Ambiguous/impossible condition or integrity issue -> 100% refunds."""
        with self.db.write() as conn:
            wager = self._get_wager_row(conn, wager_id)
            self._require_status(wager, (STATUS_VETTED, STATUS_FROZEN))
            conn.execute(
                "UPDATE wagers SET status = 'voided', accepting_bets = 0, void_reason = ?, "
                "updated_at = ? WHERE id = ?",
                ((reason or "").strip(), _now(), wager_id),
            )
            refunded = self._refund_open_bets(conn, wager_id, "Voided")
            self._history(conn, wager_id, "void", actor,
                          f"{reason or 'ambiguous condition'} — refunded {refunded} stake(s)")
        return self.get_wager(wager_id)
