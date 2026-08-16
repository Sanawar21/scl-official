"""Auction engine.

Ported from the reference app (`../SCL/app/services/auction_service.py`) onto
SQLite, with three additions from the requirements:
- every mutating action is logged to `auction_action_log` and undoable by admin
- post-auction admin-controlled transfers
- admin takeover of fumbling teams + pre-auction purse gifts
All money/credit/phase rules come from the season's ruleset (fluid per season).
"""
import secrets
from datetime import datetime, timezone

from .. import rules as R
from ..db import json_dumps, json_loads, row_to_dict, rows_to_dicts
from ..ruleset import Ruleset
from .branding_service import BrandingService


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fmt_bid_time(ts: str) -> str:
    try:
        dt = datetime.fromisoformat(ts)
        return f"{dt.strftime('%H:%M:%S')}.{int(dt.microsecond / 1000):03d}"
    except Exception:  # noqa: BLE001
        return ts


class AuctionService:
    def __init__(self, db, bank_service=None):
        self.db = db
        self.bank = bank_service

    # --- team wallet (= manager's player bank account) -------------------
    # The team purse IS the manager's account (user decision). Every purse
    # mutation below also moves the manager's wallet inside the same write
    # transaction, so the two can never drift. `self.bank` may be None for
    # import/read-only contexts; those paths never mutate money.
    def _team_wallet(self, conn, team) -> dict:
        if self.bank is None:
            raise ValueError("Bank service not available")
        return self.bank.get_or_create_account("player", team["manager_player_id"], conn=conn)

    def _wallet_adjust(self, conn, team, delta: int, comment: str, tx_type: str = "purse") -> None:
        if delta == 0:
            return
        account = self._team_wallet(conn, team)
        self.bank.adjust(account["id"], delta, comment, tx_type=tx_type, conn=conn)

    # ------------------------------------------------------------------
    # seasons & rulesets
    # ------------------------------------------------------------------
    def create_season(self, name: str, ruleset_overrides: dict = None) -> dict:
        overrides = ruleset_overrides or {}
        season_id = self._slugify(name)
        with self.db.write() as conn:
            exists = conn.execute("SELECT id FROM seasons WHERE id = ?", (season_id,)).fetchone()
            if exists:
                raise ValueError("A season with this name already exists")
            conn.execute(
                "INSERT INTO seasons (id, name, status, created_at) VALUES (?, ?, 'setup', ?)",
                (season_id, name, _now()),
            )
            ruleset_id = secrets.token_hex(8)
            conn.execute(
                "INSERT INTO rulesets (id, season_id, phase_order, tier_purses, tier_base_prices, "
                "tier_credits, total_credits, bid_increment, phase_b_price, credit_refund_rate, "
                "required_players, roster_size, break_minutes, match_reward_amount) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    ruleset_id,
                    season_id,
                    json_dumps(overrides.get("phase_order", R.DEFAULT_PHASE_ORDER)),
                    json_dumps(overrides.get("tier_purses", R.DEFAULT_TIER_PURSES)),
                    json_dumps(overrides.get("tier_base_prices", R.DEFAULT_TIER_BASE_PRICES)),
                    json_dumps(overrides.get("tier_credits", R.DEFAULT_TIER_CREDITS)),
                    int(overrides.get("total_credits", R.TOTAL_CREDITS)),
                    int(overrides.get("bid_increment", R.BID_INCREMENT)),
                    int(overrides.get("phase_b_price", R.PHASE_B_PRICE)),
                    int(overrides.get("credit_refund_rate", R.CREDIT_REFUND_RATE)),
                    int(overrides.get("required_players", R.REQUIRED_PLAYERS)),
                    int(overrides.get("roster_size", R.ROSTER_SIZE)),
                    int(overrides.get("break_minutes", R.BREAK_MINUTES)),
                    int(overrides.get("match_reward_amount", R.MATCH_REWARD_AMOUNT)),
                ),
            )
            conn.execute(
                "INSERT INTO auction_meta (season_id, phase, current_player_id, nomination_history) "
                "VALUES (?, 'setup', NULL, '[]')",
                (season_id,),
            )
        return self.get_season(season_id)

    def update_ruleset(self, season_id: str, overrides: dict) -> dict:
        with self.db.write() as conn:
            season = conn.execute("SELECT * FROM seasons WHERE id = ?", (season_id,)).fetchone()
            if not season:
                raise ValueError("Season not found")
            if season["status"] != "setup":
                raise ValueError("Ruleset can only be edited during setup")
            current = self._get_ruleset(conn, season_id).as_dict()
            merged = {**current, **{k: v for k, v in overrides.items() if v is not None}}
            conn.execute(
                "UPDATE rulesets SET phase_order = ?, tier_purses = ?, tier_base_prices = ?, "
                "tier_credits = ?, total_credits = ?, bid_increment = ?, phase_b_price = ?, "
                "credit_refund_rate = ?, required_players = ?, roster_size = ?, break_minutes = ?, "
                "match_reward_amount = ? WHERE season_id = ?",
                (
                    json_dumps(merged["phase_order"]),
                    json_dumps(merged["tier_purses"]),
                    json_dumps(merged["tier_base_prices"]),
                    json_dumps(merged["tier_credits"]),
                    merged["total_credits"], merged["bid_increment"], merged["phase_b_price"],
                    merged["credit_refund_rate"], merged["required_players"], merged["roster_size"],
                    merged["break_minutes"], merged["match_reward_amount"], season_id,
                ),
            )
        return self.get_season(season_id)

    def _slugify(self, name: str) -> str:
        slug = "".join(c if c.isalnum() else "-" for c in (name or "").strip().lower()).strip("-")
        if not slug:
            raise ValueError("Invalid season name")
        return slug

    def list_global_players(self) -> list:
        with self.db.read() as conn:
            return rows_to_dicts(conn.execute(
                "SELECT * FROM global_players ORDER BY name"
            ).fetchall())

    def list_transfers(self, season_id: str) -> list:
        with self.db.read() as conn:
            return rows_to_dicts(conn.execute(
                "SELECT * FROM transfers WHERE season_id = ? ORDER BY created_at DESC",
                (season_id,),
            ).fetchall())

    def list_seasons(self) -> list:
        with self.db.read() as conn:
            return rows_to_dicts(conn.execute(
                "SELECT * FROM seasons ORDER BY created_at DESC"
            ).fetchall())

    def get_season(self, season_id: str) -> dict:
        with self.db.read() as conn:
            row = conn.execute("SELECT * FROM seasons WHERE id = ?", (season_id,)).fetchone()
            if not row:
                return None
            season = row_to_dict(row)
            season["ruleset"] = self._get_ruleset(conn, season_id).as_dict()
            return season

    def delete_season(self, season_id: str, actor: str = "admin") -> dict:
        """Permanently delete a season and everything scoped to it.

        Removes the per-season rows (players, teams, bids, trades, transfers,
        action log, auction meta, snapshots, ruleset, wagers + bets, match
        data, finance entries). Vault positions for the season are released
        back to liquid cash first — money is never destroyed. Global
        players/teams are untouched: they persist across seasons, and manager
        status is derived from them (never stored on the user row)."""
        with self.db.write() as conn:
            season = conn.execute("SELECT * FROM seasons WHERE id = ?", (season_id,)).fetchone()
            if not season:
                raise ValueError("Season not found")
            season_name = season["name"]

            # Vault positions -> release any locked capital back to liquid.
            positions = conn.execute(
                "SELECT * FROM vault_positions WHERE season_id = ?", (season_id,)).fetchall()
            for pos in positions:
                locked = int(pos["locked_capital"])
                if locked > 0 and self.bank is not None:
                    self.bank.unlock_amount(
                        pos["account_id"], season_id, locked, conn=conn,
                        comment=f"Season {season_name} deleted — vault released")
                conn.execute("DELETE FROM vault_positions WHERE id = ?", (pos["id"],))

            # Children before parents (foreign keys are enforced).
            conn.execute(
                "DELETE FROM wager_bets WHERE wager_id IN "
                "(SELECT id FROM wagers WHERE season_id = ?)", (season_id,))
            conn.execute("DELETE FROM wagers WHERE season_id = ?", (season_id,))
            conn.execute("DELETE FROM match_player_stats WHERE season_id = ?", (season_id,))
            conn.execute("DELETE FROM match_team_stats WHERE season_id = ?", (season_id,))
            conn.execute("DELETE FROM match_stats WHERE season_id = ?", (season_id,))
            conn.execute("DELETE FROM match_registry WHERE season_id = ?", (season_id,))
            conn.execute("DELETE FROM season_finance_entries WHERE season_id = ?", (season_id,))
            conn.execute("DELETE FROM transfers WHERE season_id = ?", (season_id,))
            conn.execute("DELETE FROM trade_requests WHERE season_id = ?", (season_id,))
            conn.execute("DELETE FROM bids WHERE season_id = ?", (season_id,))
            conn.execute("DELETE FROM teams WHERE season_id = ?", (season_id,))
            conn.execute("DELETE FROM players WHERE season_id = ?", (season_id,))
            conn.execute("DELETE FROM auction_action_log WHERE season_id = ?", (season_id,))
            conn.execute("DELETE FROM auction_meta WHERE season_id = ?", (season_id,))
            conn.execute("DELETE FROM season_snapshots WHERE season_id = ?", (season_id,))
            conn.execute("DELETE FROM rulesets WHERE season_id = ?", (season_id,))
            conn.execute("DELETE FROM seasons WHERE id = ?", (season_id,))
        return {"ok": True, "season_id": season_id, "name": season_name}

    def _get_ruleset(self, conn, season_id: str) -> Ruleset:
        row = conn.execute("SELECT * FROM rulesets WHERE season_id = ?", (season_id,)).fetchone()
        if not row:
            # Fall back to defaults (should not happen for new seasons).
            defaults = {
                "id": secrets.token_hex(8), "season_id": season_id,
                "phase_order": json_dumps(R.DEFAULT_PHASE_ORDER),
                "tier_purses": json_dumps(R.DEFAULT_TIER_PURSES),
                "tier_base_prices": json_dumps(R.DEFAULT_TIER_BASE_PRICES),
                "tier_credits": json_dumps(R.DEFAULT_TIER_CREDITS),
                "total_credits": R.TOTAL_CREDITS, "bid_increment": R.BID_INCREMENT,
                "phase_b_price": R.PHASE_B_PRICE, "credit_refund_rate": R.CREDIT_REFUND_RATE,
                "required_players": R.REQUIRED_PLAYERS, "roster_size": R.ROSTER_SIZE,
                "break_minutes": R.BREAK_MINUTES, "match_reward_amount": R.MATCH_REWARD_AMOUNT,
            }
            return Ruleset(defaults)
        return Ruleset(row_to_dict(row))

    def _get_meta(self, conn, season_id: str) -> dict:
        row = conn.execute("SELECT * FROM auction_meta WHERE season_id = ?", (season_id,)).fetchone()
        if not row:
            conn.execute(
                "INSERT INTO auction_meta (season_id, phase, current_player_id, nomination_history) "
                "VALUES (?, 'setup', NULL, '[]')",
                (season_id,),
            )
            row = conn.execute("SELECT * FROM auction_meta WHERE season_id = ?", (season_id,)).fetchone()
        meta = row_to_dict(row)
        meta["nomination_history"] = json_loads(meta.get("nomination_history"), [])
        return meta

    def get_phase(self, season_id: str) -> str:
        """Lightweight current-phase lookup (no full state assembly)."""
        with self.db.read() as conn:
            return self._get_meta(conn, season_id).get("phase", R.PHASE_SETUP)

    # ------------------------------------------------------------------
    # players & teams (setup)
    # ------------------------------------------------------------------
    def add_player(self, season_id: str, name: str, tier: str, speciality: str = "ALL_ROUNDER",
                   global_player_id: str = None) -> dict:
        tier = (tier or "").strip().lower()
        if tier not in R.TIERS:
            raise ValueError("Tier must be platinum, gold or silver")
        name = (name or "").strip()
        if not name:
            raise ValueError("Player name is required")
        player_id = secrets.token_hex(8)
        with self.db.write() as conn:
            ruleset = self._get_ruleset(conn, season_id)
            if not global_player_id:
                gp_id = secrets.token_hex(8)
                conn.execute(
                    "INSERT INTO global_players (id, name, tier, speciality, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (gp_id, name, tier, speciality.upper(), _now()),
                )
                global_player_id = gp_id
            else:
                gp = conn.execute("SELECT id FROM global_players WHERE id = ?", (global_player_id,)).fetchone()
                if not gp:
                    raise ValueError("Global player not found")
            conn.execute(
                "INSERT INTO players (id, season_id, global_player_id, name, tier, speciality, "
                "base_price, credits, status, current_bid, nominated_phase_a) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'unsold', 0, 0)",
                (player_id, season_id, global_player_id, name, tier, speciality.upper(),
                 ruleset.base_price_for(tier), ruleset.credits_for(tier)),
            )
            self._log(conn, season_id, "add_player", "admin",
                      before={"player_id": player_id},
                      after={"player_id": player_id, "name": name, "tier": tier},
                      ref_player_id=player_id)
        return self._get_player(season_id, player_id)

    def update_player(self, season_id: str, player_id: str, name: str = None, tier: str = None,
                      speciality: str = None) -> dict:
        with self.db.write() as conn:
            season = conn.execute("SELECT status FROM seasons WHERE id = ?", (season_id,)).fetchone()
            if not season:
                raise ValueError("Season not found")
            if season["status"] != "setup":
                raise ValueError("Players can only be modified before the auction starts")
            player = conn.execute("SELECT * FROM players WHERE id = ? AND season_id = ?",
                                  (player_id, season_id)).fetchone()
            if not player:
                raise ValueError("Player not found")
            if player["status"] != "unsold":
                raise ValueError("Sold players cannot be modified")
            before = row_to_dict(player)
            updates = {}
            if name is not None:
                updates["name"] = (name or "").strip() or before["name"]
            if tier is not None:
                tier = tier.strip().lower()
                if tier not in R.TIERS:
                    raise ValueError("Tier must be platinum, gold or silver")
                ruleset = self._get_ruleset(conn, season_id)
                updates["tier"] = tier
                updates["base_price"] = ruleset.base_price_for(tier)
                updates["credits"] = ruleset.credits_for(tier)
            if speciality is not None:
                updates["speciality"] = speciality.upper()
            if updates:
                sets = ", ".join(f"{k} = ?" for k in updates)
                conn.execute(f"UPDATE players SET {sets} WHERE id = ?", (*updates.values(), player_id))
            self._log(conn, season_id, "update_player", "admin",
                      before={"player_id": player_id, "row": before},
                      after={"player_id": player_id},
                      ref_player_id=player_id)
        return self._get_player(season_id, player_id)

    def update_manager(self, season_id: str, team_id: str, name: str = None,
                       tier: str = None, speciality: str = None) -> dict:
        """Edit a team's manager player (their name/tier/speciality).

        Managers are their own team's roster slot — they are NOT in the season's
        auction pool, so they can't be edited through update_player. Their tier
        feeds the team's credits (manager_tier), so changing it recalculates
        the team's remaining credits."""
        with self.db.write() as conn:
            season = conn.execute("SELECT status FROM seasons WHERE id = ?",
                                  (season_id,)).fetchone()
            if not season:
                raise ValueError("Season not found")
            if season["status"] != "setup":
                raise ValueError("Managers can only be modified before the auction starts")
            team = conn.execute("SELECT * FROM teams WHERE id = ? AND season_id = ?",
                                (team_id, season_id)).fetchone()
            if not team:
                raise ValueError("Team not found")
            mgr_id = team["manager_player_id"]
            if not mgr_id:
                raise ValueError("This team has no manager")
            gp = conn.execute("SELECT * FROM global_players WHERE id = ?", (mgr_id,)).fetchone()
            if not gp:
                raise ValueError("Manager player not found")
            before_gp = row_to_dict(gp)
            updates = {}
            if name is not None:
                updates["name"] = (name or "").strip() or before_gp["name"]
            if speciality is not None:
                updates["speciality"] = (speciality or "").strip().upper() or before_gp["speciality"]
            tier_changed = False
            if tier is not None:
                tier = tier.strip().lower()
                if tier not in R.TIERS:
                    raise ValueError("Tier must be platinum, gold or silver")
                if tier != before_gp["tier"]:
                    updates["tier"] = tier
                    tier_changed = True
            if updates:
                sets = ", ".join(f"{k} = ?" for k in updates)
                conn.execute(f"UPDATE global_players SET {sets} WHERE id = ?",
                             (*updates.values(), mgr_id))
            if tier_changed:
                ruleset = self._get_ruleset(conn, season_id)
                conn.execute("UPDATE teams SET manager_tier = ? WHERE id = ?",
                             (tier, team_id))
                players_list = json_loads(team["players"], [])
                conn.execute("UPDATE teams SET credits_remaining = ? WHERE id = ?",
                             (self._recalculate_team_credits(conn, season_id, team_id,
                                                              players_list), team_id))
            self._log(conn, season_id, "update_manager", "admin",
                      before={"team_id": team_id, "row": before_gp},
                      after={"team_id": team_id, "global_player_id": mgr_id},
                      ref_team_id=team_id)
        return self._get_team(season_id, team_id)

    def delete_player(self, season_id: str, player_id: str) -> dict:
        with self.db.write() as conn:
            season = conn.execute("SELECT status FROM seasons WHERE id = ?", (season_id,)).fetchone()
            if not season or season["status"] != "setup":
                raise ValueError("Players can only be removed before the auction starts")
            player = conn.execute("SELECT * FROM players WHERE id = ? AND season_id = ?",
                                  (player_id, season_id)).fetchone()
            if not player:
                raise ValueError("Player not found")
            if player["status"] != "unsold":
                raise ValueError("Sold players cannot be removed")
            conn.execute("DELETE FROM players WHERE id = ?", (player_id,))
            self._log(conn, season_id, "delete_player", "admin",
                      before={"player_id": player_id, "row": row_to_dict(player)},
                      after={"player_id": player_id},
                      ref_player_id=player_id)
        return {"ok": True}

    # ------------------------------------------------------------------
    # persistent team identity (global_teams) + per-season participation
    # ------------------------------------------------------------------
    def get_global_team(self, team_id: str) -> dict:
        with self.db.read() as conn:
            row = conn.execute("SELECT * FROM global_teams WHERE id = ?", (team_id,)).fetchone()
            if not row:
                return None
            team = row_to_dict(row)
            team["wallet"] = self._global_team_wallet(conn, team)
            return team

    def _global_team_wallet(self, conn, gt: dict) -> int:
        if not gt.get("manager_player_id"):
            return 0
        acct = conn.execute(
            "SELECT liquid_cash FROM bank_accounts WHERE owner_type = 'player' "
            "AND owner_id = ?", (gt["manager_player_id"],)).fetchone()
        return int(acct["liquid_cash"]) if acct else 0

    def create_team_account(self, manager_player_id: str, name: str) -> dict:
        """Create a persistent team (global_teams) owned by a player.

        Works any time, for any season (or none): the team exists as a profile
        (logo/about editable by the manager) even if it never plays. The team's
        money is the manager's player wallet. No purse is funded."""
        name = (name or "").strip()
        if not name:
            raise ValueError("Team name is required")
        with self.db.write() as conn:
            gp = conn.execute("SELECT * FROM global_players WHERE id = ?",
                              (manager_player_id,)).fetchone()
            if not gp:
                raise ValueError("Player not found")
            owns = conn.execute(
                "SELECT 1 FROM global_teams WHERE manager_player_id = ?",
                (manager_player_id,)).fetchone()
            if owns:
                raise ValueError("This player already manages a team")
            dup = conn.execute(
                "SELECT id FROM global_teams WHERE lower(name) = lower(?)",
                (name,)).fetchone()
            if dup:
                raise ValueError("A team with this name already exists")
            team_id = secrets.token_hex(8)
            conn.execute(
                "INSERT INTO global_teams (id, name, logo, about, manager_player_id, created_at) "
                "VALUES (?, ?, '', '', ?, ?)",
                (team_id, name, manager_player_id, _now()),
            )
        return self.get_global_team(team_id)

    def update_team_profile(self, team_id: str, name: str = None, logo: str = None,
                            banner: str = None, about: str = None) -> dict:
        with self.db.write() as conn:
            gt = conn.execute("SELECT * FROM global_teams WHERE id = ?", (team_id,)).fetchone()
            if not gt:
                raise ValueError("Team not found")
            new_name = (name or gt["name"]).strip()
            if not new_name:
                raise ValueError("Team name is required")
            dup = conn.execute(
                "SELECT id FROM global_teams WHERE lower(name) = lower(?) AND id != ?",
                (new_name, team_id)).fetchone()
            if dup:
                raise ValueError("A team with this name already exists")
            # Only overwrite fields the caller actually passed; None keeps the
            # stored value (so a name/about edit never wipes uploaded assets).
            new_logo = (logo if logo is not None else gt["logo"] or "").strip()
            new_banner = (banner if banner is not None else gt["banner"] or "").strip()
            new_about = (about if about is not None else gt["about"] or "").strip()
            conn.execute(
                "UPDATE global_teams SET name = ?, logo = ?, banner = ?, about = ? WHERE id = ?",
                (new_name, new_logo, new_banner, new_about, team_id),
            )
        return self.get_global_team(team_id)

    def create_team(self, season_id: str, name: str, manager_player_id: str,
                    global_team_id: str = None) -> dict:
        """Register a team for a season (per-season row) + ensure its identity.

        The persistent team (global_teams) is created on first use or reused by
        id / exact name. No tier purse is funded — the team's money is the
        manager's own wallet. The per-season row is only written while the
        season is in setup (auction integrity); outside setup the team profile
        still gets created/updated but isn't registered for the season."""
        name = (name or "").strip()
        if not name:
            raise ValueError("Team name is required")
        with self.db.write() as conn:
            season = conn.execute("SELECT status FROM seasons WHERE id = ?", (season_id,)).fetchone()
            if not season:
                raise ValueError("Season not found")
            gp = conn.execute("SELECT * FROM global_players WHERE id = ?", (manager_player_id,)).fetchone()
            if not gp:
                raise ValueError("Manager player not found")
            manager_tier = gp["tier"]
            gid = (global_team_id or "").strip()
            if gid:
                gt = conn.execute("SELECT * FROM global_teams WHERE id = ?", (gid,)).fetchone()
                if not gt:
                    raise ValueError("Global team not found")
                if gt["manager_player_id"] and gt["manager_player_id"] != manager_player_id:
                    raise ValueError("This team already has a different manager")
                if not gt["manager_player_id"]:
                    conn.execute(
                        "UPDATE global_teams SET manager_player_id = ? WHERE id = ?",
                        (manager_player_id, gid))
            else:
                gt = conn.execute(
                    "SELECT * FROM global_teams WHERE lower(name) = lower(?)", (name,)).fetchone()
                if gt:
                    gid = gt["id"]
                    if gt["manager_player_id"] and gt["manager_player_id"] != manager_player_id:
                        raise ValueError("A team with this name already exists under a different manager")
                else:
                    gid = secrets.token_hex(8)
                    conn.execute(
                        "INSERT INTO global_teams (id, name, logo, about, manager_player_id, created_at) "
                        "VALUES (?, ?, '', '', ?, ?)",
                        (gid, name, manager_player_id, _now()),
                    )
            registered = season["status"] == "setup"
            team_id = None
            if registered:
                dup = conn.execute(
                    "SELECT id FROM teams WHERE season_id = ? AND (name = ? OR global_team_id = ?)",
                    (season_id, name, gid)).fetchone()
                if dup:
                    raise ValueError("A team with this name already exists in this season")
                taken = conn.execute(
                    "SELECT id FROM teams WHERE season_id = ? AND manager_player_id = ?",
                    (season_id, manager_player_id),
                ).fetchone()
                if taken:
                    raise ValueError("This player already manages a team in this season")
                ruleset = self._get_ruleset(conn, season_id)
                team_id = secrets.token_hex(8)
                conn.execute(
                    "INSERT INTO teams (id, season_id, name, manager_player_id, manager_tier, "
                    "spent, credits_remaining, global_team_id) "
                    "VALUES (?, ?, ?, ?, ?, 0, ?, ?)",
                    (team_id, season_id, name, manager_player_id, manager_tier,
                     ruleset.total_credits - ruleset.credits_for(manager_tier), gid),
                )
                self._log(conn, season_id, "create_team", "admin",
                          before={"team_id": team_id},
                          after={"team_id": team_id, "name": name, "global_team_id": gid},
                          ref_team_id=team_id)
        if registered:
            return self._get_team(season_id, team_id)
        team = self.get_global_team(gid)
        team["registered"] = False
        team["season_id"] = season_id
        return team

    def delete_team(self, season_id: str, team_id: str) -> dict:
        """Remove a team from a season. The manager's wallet is never touched."""
        with self.db.write() as conn:
            season = conn.execute("SELECT status FROM seasons WHERE id = ?", (season_id,)).fetchone()
            if not season or season["status"] != "setup":
                raise ValueError("Teams can only be removed during setup")
            team = conn.execute("SELECT * FROM teams WHERE id = ? AND season_id = ?",
                                (team_id, season_id)).fetchone()
            if not team:
                raise ValueError("Team not found")
            before_row = row_to_dict(team)
            conn.execute("DELETE FROM teams WHERE id = ?", (team_id,))
            self._log(conn, season_id, "delete_team", "admin",
                      before={"team_id": team_id, "row": before_row},
                      after={"team_id": team_id},
                      ref_team_id=team_id)
        return {"ok": True}

    # ------------------------------------------------------------------
    # season setup: pick managers + auction players from the global pool
    # ------------------------------------------------------------------
    def season_setup_context(self, season_id: str) -> dict:
        """All global players + teams with their membership in this season.

        Returns the data the setup page needs: every persistent player (with
        whether they're in this season's auction and whether they manage a
        team this season) and every persistent team (with whether it plays
        this season)."""
        with self.db.read() as conn:
            season = conn.execute("SELECT * FROM seasons WHERE id = ?", (season_id,)).fetchone()
            if not season:
                return None
            gps = rows_to_dicts(conn.execute(
                "SELECT * FROM global_players ORDER BY name").fetchall())
            gts = rows_to_dicts(conn.execute(
                "SELECT * FROM global_teams ORDER BY name").fetchall())
            season_players = rows_to_dicts(conn.execute(
                "SELECT * FROM players WHERE season_id = ?", (season_id,)).fetchall())
            season_teams = rows_to_dicts(conn.execute(
                "SELECT * FROM teams WHERE season_id = ?", (season_id,)).fetchall())

        gt_by_mgr = {gt["manager_player_id"]: gt for gt in gts if gt.get("manager_player_id")}
        sp_by_gp = {sp["global_player_id"]: sp for sp in season_players
                    if sp.get("global_player_id")}
        st_by_mgr = {st["manager_player_id"]: st for st in season_teams
                     if st.get("manager_player_id")}
        st_by_gid = {(st.get("global_team_id") or "").strip(): st for st in season_teams}

        for gp in gps:
            gt = gt_by_mgr.get(gp["id"])
            sp = sp_by_gp.get(gp["id"])
            st = st_by_mgr.get(gp["id"])
            gp["team"] = gt  # global team this player manages, if any
            gp["in_auction"] = sp is not None
            gp["season_player_id"] = sp["id"] if sp else None
            gp["is_manager"] = st is not None
            gp["season_team_id"] = st["id"] if st else None
            gp["season_team_name"] = st["name"] if st else (gt["name"] if gt else "")
        for gt in gts:
            st = st_by_gid.get(gt["id"])
            gt["in_season"] = st is not None
            gt["season_team_id"] = st["id"] if st else None

        # Resolve manager names on the per-season team rows for the UI.
        gp_names = {gp["id"]: gp["name"] for gp in gps}
        for st in season_teams:
            st["manager_name"] = gp_names.get(st.get("manager_player_id"), "—")

        return {
            "season": row_to_dict(season),
            "players": gps,
            "teams": gts,
            "season_players": season_players,
            "season_teams": season_teams,
        }

    def sync_season_setup(self, season_id: str, auction_player_ids=(),
                          manager_team_names=None) -> dict:
        """Apply the setup form: which players are in the auction + managers.

        ``auction_player_ids``: global player ids that will be in the auction
        (per-season ``players`` rows are created from the global pool, reusing
        the global identity; deselected players are removed from the season).
        ``manager_team_names``: {global_player_id: team_name} — the manager
        keeps their existing global team if they have one (added to the season
        automatically), or a team is created for them with the given name.

        Only valid during setup."""
        manager_team_names = manager_team_names or {}
        manager_ids = set(manager_team_names.keys())
        # Managers are their own team's roster slot, never auction lots —
        # exclude them from the auction pool even if the form sends them.
        auction_player_ids = set(auction_player_ids or []) - manager_ids
        with self.db.read() as conn:
            season = conn.execute("SELECT status FROM seasons WHERE id = ?",
                                  (season_id,)).fetchone()
            if not season:
                raise ValueError("Season not found")
            if season["status"] != "setup":
                raise ValueError("Setup can only be edited before the auction starts")
            gps = {r["id"]: dict(r) for r in conn.execute(
                "SELECT * FROM global_players").fetchall()}
            gts = rows_to_dicts(conn.execute(
                "SELECT * FROM global_teams").fetchall())
        gt_by_mgr = {gt["manager_player_id"]: gt for gt in gts if gt.get("manager_player_id")}

        # --- auction players ----------------------------------------------
        existing = set()
        with self.db.read() as conn:
            existing = {r["global_player_id"] for r in conn.execute(
                "SELECT global_player_id FROM players WHERE season_id = ?",
                (season_id,)).fetchall() if r["global_player_id"]}
        for gp_id in sorted(auction_player_ids - existing):
            gp = gps.get(gp_id)
            if not gp:
                raise ValueError("Unknown player in auction selection")
            self.add_player(season_id, gp["name"], gp["tier"],
                            gp.get("speciality") or "ALL_ROUNDER",
                            global_player_id=gp_id)
        for gp_id in sorted(existing - auction_player_ids):
            with self.db.read() as conn:
                prow = conn.execute(
                    "SELECT id FROM players WHERE season_id = ? AND global_player_id = ?",
                    (season_id, gp_id)).fetchone()
            if prow:
                self.delete_player(season_id, prow["id"])

        # --- managers / teams ---------------------------------------------
        for gp_id, team_name in manager_team_names.items():
            gp = gps.get(gp_id)
            if not gp:
                raise ValueError("Unknown manager selection")
            with self.db.read() as conn:
                already = conn.execute(
                    "SELECT id FROM teams WHERE season_id = ? AND manager_player_id = ?",
                    (season_id, gp_id)).fetchone()
            if already:
                continue
            gt = gt_by_mgr.get(gp_id)
            if gt:
                # Existing team -> added to this season automatically.
                self.create_team(season_id, gt["name"], gp_id,
                                 global_team_id=gt["id"])
            else:
                name = (team_name or "").strip()
                if not name:
                    raise ValueError(f"Team name required for {gp['name']}")
                self.create_team(season_id, name, gp_id)
        # Deselected managers -> their team leaves this season's auction.
        with self.db.read() as conn:
            season_teams = rows_to_dicts(conn.execute(
                "SELECT * FROM teams WHERE season_id = ?", (season_id,)).fetchall())
        selected = set(manager_team_names.keys())
        for st in season_teams:
            if st.get("manager_player_id") and st["manager_player_id"] not in selected:
                self.delete_team(season_id, st["id"])
        return self.season_setup_context(season_id)

    def reassign_team_manager(self, season_id: str, team_id: str,
                              new_manager_player_id: str) -> dict:
        """Change which player manages a team (setup only)."""
        with self.db.write() as conn:
            season = conn.execute("SELECT status FROM seasons WHERE id = ?",
                                  (season_id,)).fetchone()
            if not season or season["status"] != "setup":
                raise ValueError("Managers can only be changed during setup")
            team = conn.execute("SELECT * FROM teams WHERE id = ? AND season_id = ?",
                                (team_id, season_id)).fetchone()
            if not team:
                raise ValueError("Team not found")
            gp = conn.execute("SELECT * FROM global_players WHERE id = ?",
                              (new_manager_player_id,)).fetchone()
            if not gp:
                raise ValueError("Manager player not found")
            other = conn.execute(
                "SELECT id FROM teams WHERE season_id = ? AND manager_player_id = ? AND id != ?",
                (season_id, new_manager_player_id, team_id)).fetchone()
            if other:
                raise ValueError("This player already manages another team this season")
            before = row_to_dict(team)
            conn.execute("UPDATE teams SET manager_player_id = ?, manager_tier = ? "
                         "WHERE id = ?",
                         (new_manager_player_id, gp["tier"], team_id))
            gid = (team["global_team_id"] or "").strip()
            # Repoint the persistent team's manager too, if unowned.
            if gid:
                gt = conn.execute("SELECT * FROM global_teams WHERE id = ?",
                                  (gid,)).fetchone()
                if gt and not gt["manager_player_id"]:
                    conn.execute("UPDATE global_teams SET manager_player_id = ? WHERE id = ?",
                                 (new_manager_player_id, gid))
            self._log(conn, season_id, "reassign_manager", "admin",
                      before={"team_id": team_id, "old_manager": before["manager_player_id"]},
                      after={"team_id": team_id, "new_manager": new_manager_player_id},
                      ref_team_id=team_id)
        return self._get_team(season_id, team_id)

    def gift_team(self, season_id: str, team_id: str, amount: int, operation: str,
                  comment: str = "", actor: str = "admin") -> dict:
        amount = int(amount)
        if amount <= 0:
            raise ValueError("Amount must be positive")
        if operation not in ("add", "remove"):
            raise ValueError("Operation must be add or remove")
        with self.db.write() as conn:
            season = conn.execute("SELECT status FROM seasons WHERE id = ?", (season_id,)).fetchone()
            if not season:
                raise ValueError("Season not found")
            if season["status"] != "setup":
                raise ValueError("Gifts can only be given before the auction starts")
            team = conn.execute("SELECT * FROM teams WHERE id = ? AND season_id = ?",
                                (team_id, season_id)).fetchone()
            if not team:
                raise ValueError("Team not found")
            delta = amount if operation == "add" else -amount
            # The purse IS the manager's wallet: validate against liquid cash.
            account = self._team_wallet(conn, team)
            if int(account["liquid_cash"]) + delta < 0:
                raise ValueError("Gift would take the purse below zero")
            self._wallet_adjust(conn, team, delta, comment or f"Gift ({operation})",
                                tx_type="gift")
            self._log(conn, season_id, "gift", actor,
                      before={"team_id": team_id, "wallet": int(account["liquid_cash"])},
                      after={"team_id": team_id, "wallet": int(account["liquid_cash"]) + delta,
                             "operation": operation, "amount": amount, "comment": comment},
                      ref_team_id=team_id)
        return self._get_team(season_id, team_id)

    # ------------------------------------------------------------------
    # phase control
    # ------------------------------------------------------------------
    def set_phase(self, season_id: str, phase: str, actor: str = "admin") -> dict:
        with self.db.write() as conn:
            meta = self._get_meta(conn, season_id)
            ruleset = self._get_ruleset(conn, season_id)
            valid = {R.PHASE_SETUP, R.PHASE_COMPLETE, R.PHASE_TRANSFERS} | set(ruleset.flow())
            if phase not in valid:
                raise ValueError("Invalid phase")
            before_phase = meta["phase"]
            break_started = meta.get("break_started_at")
            if phase == R.PHASE_BREAK and before_phase != R.PHASE_BREAK:
                conn.execute("UPDATE auction_meta SET break_started_at = ? WHERE season_id = ?",
                             (_now(), season_id))
            elif phase != R.PHASE_BREAK and break_started:
                conn.execute("UPDATE auction_meta SET break_started_at = NULL WHERE season_id = ?",
                             (season_id,))
            conn.execute("UPDATE auction_meta SET phase = ? WHERE season_id = ?", (phase, season_id))
            if phase in (R.PHASE_COMPLETE, R.PHASE_TRANSFERS):
                conn.execute("UPDATE seasons SET status = 'completed' WHERE id = ?", (season_id,))
            self._log(conn, season_id, "set_phase", actor,
                      before={"phase": before_phase}, after={"phase": phase})
        return self.get_state(season_id)

    def nominate_next(self, season_id: str, actor: str = "admin") -> dict:
        with self.db.write() as conn:
            meta = self._get_meta(conn, season_id)
            phase = meta["phase"]
            if phase not in set(self._get_ruleset(conn, season_id).flow()):
                raise ValueError("No players can be nominated in this phase")
            history = list(meta["nomination_history"])
            if R.is_tier_phase(phase):
                tier = R.phase_tier(phase)
                player = conn.execute(
                    "SELECT * FROM players WHERE season_id = ? AND status = 'unsold' AND tier = ? "
                    "AND nominated_phase_a = 0 ORDER BY rowid LIMIT 1",
                    (season_id, tier),
                ).fetchone()
            elif phase == R.PHASE_B:
                player = conn.execute(
                    "SELECT * FROM players WHERE season_id = ? AND status = 'unsold' "
                    "ORDER BY rowid LIMIT 1",
                    (season_id,),
                ).fetchone()
            else:
                player = None
            if not player:
                raise ValueError("No unsold players left in this phase")
            prev_current = meta["current_player_id"]
            if prev_current:
                history.append(prev_current)
            player_id = player["id"]
            conn.execute(
                "UPDATE players SET current_bid = 0, current_bidder_team_id = NULL, "
                "nominated_phase_a = 1 WHERE id = ?",
                (player_id,),
            )
            conn.execute(
                "UPDATE auction_meta SET current_player_id = ?, nomination_history = ? WHERE season_id = ?",
                (player_id, json_dumps(history), season_id),
            )
            self._log(conn, season_id, "nominate", actor,
                      before={"previous_current_player_id": prev_current, "history": history},
                      after={"player_id": player_id},
                      ref_player_id=player_id)
        return self._get_player(season_id, player_id)

    def previous_player(self, season_id: str) -> dict:
        """Step back to the previous lot (admin correction; itself not undo-logged)."""
        with self.db.write() as conn:
            meta = self._get_meta(conn, season_id)
            current_player_id = meta["current_player_id"]
            history = list(meta["nomination_history"])
            if not current_player_id:
                raise ValueError("No active player to step back from")
            if not history:
                raise ValueError("No previous player available")
            current = conn.execute("SELECT * FROM players WHERE id = ?", (current_player_id,)).fetchone()
            if not current:
                raise ValueError("Current player not found")
            if current["current_bid"] > 0 or current["current_bidder_team_id"]:
                raise ValueError("Cannot step back after bidding has started")
            previous_id = history.pop()
            previous = conn.execute("SELECT * FROM players WHERE id = ?", (previous_id,)).fetchone()
            if not previous:
                raise ValueError("Previous player not found")

            # Undo an accidental close of the previous lot.
            if previous["status"] == "sold" and previous["sold_to_team_id"]:
                team = conn.execute("SELECT * FROM teams WHERE id = ?", (previous["sold_to_team_id"],)).fetchone()
                if team:
                    players_list = json_loads(team["players"], [])
                    bench_list = json_loads(team["bench"], [])
                    if previous_id in players_list:
                        players_list.remove(previous_id)
                    if previous_id in bench_list:
                        bench_list.remove(previous_id)
                    refund_price = int(previous["sold_price"] or 0)
                    refund_credits = int(previous["credits"] or 0)
                    conn.execute(
                        "UPDATE teams SET players = ?, bench = ?, "
                        "spent = ?, credits_remaining = ? WHERE id = ?",
                        (json_dumps(players_list), json_dumps(bench_list),
                         max(0, int(team["spent"]) - refund_price),
                         int(team["credits_remaining"]) + refund_credits,
                         team["id"]),
                    )
                    if refund_price:
                        self._wallet_adjust(conn, team, refund_price,
                                            "Refund for stepped-back lot", tx_type="refund")
                conn.execute(
                    "UPDATE players SET status = 'unsold', sold_to_team_id = NULL, "
                    "sold_price = 0, phase_sold = NULL WHERE id = ?",
                    (previous_id,),
                )

            # Restore top bid for the reopened lot.
            top = conn.execute(
                "SELECT * FROM bids WHERE player_id = ? AND kind = 'bid' "
                "ORDER BY amount DESC, ts DESC, rowid DESC LIMIT 1",
                (previous_id,),
            ).fetchone()
            if top:
                prev_update = {"current_bid": top["amount"], "current_bidder_team_id": top["team_id"]}
            else:
                prev_update = {"current_bid": 0, "current_bidder_team_id": None}

            cur_update = {"current_bid": 0, "current_bidder_team_id": None}
            if R.is_tier_phase(meta["phase"]):
                cur_update["nominated_phase_a"] = 0
            sets = ", ".join(f"{k} = ?" for k in cur_update)
            conn.execute(f"UPDATE players SET {sets} WHERE id = ?", (*cur_update.values(), current_player_id))

            prev_update["nominated_phase_a"] = 1
            sets = ", ".join(f"{k} = ?" for k in prev_update)
            conn.execute(f"UPDATE players SET {sets} WHERE id = ?", (*prev_update.values(), previous_id))

            conn.execute(
                "UPDATE auction_meta SET current_player_id = ?, nomination_history = ? WHERE season_id = ?",
                (previous_id, json_dumps(history), season_id),
            )
        return self._get_player(season_id, previous_id)

    # ------------------------------------------------------------------
    # bidding
    # ------------------------------------------------------------------
    def place_bid(self, season_id: str, team_id: str, amount: int, actor: str = "manager") -> dict:
        amount = int(amount)
        with self.db.write() as conn:
            meta = self._get_meta(conn, season_id)
            phase = meta["phase"]
            player_id = meta["current_player_id"]
            if not player_id:
                raise ValueError("No active player nominated")
            ruleset = self._get_ruleset(conn, season_id)
            player = conn.execute("SELECT * FROM players WHERE id = ?", (player_id,)).fetchone()
            if not player or player["status"] != "unsold":
                raise ValueError("Player is no longer available")
            team = conn.execute("SELECT * FROM teams WHERE id = ? AND season_id = ?",
                                (team_id, season_id)).fetchone()
            if not team:
                raise ValueError("Invalid team")
            if not team["is_active"]:
                raise ValueError("This team is excluded from auction participation")
            if team["control_status"] == R.CONTROL_TAKEOVER and actor != R.ROLE_ADMIN:
                raise ValueError("This team is under admin control; the manager cannot bid")
            # Never let the current highest bidder bid against themselves — it
            # would only push the price up on their own wallet.
            if player["current_bidder_team_id"] and player["current_bidder_team_id"] == team_id:
                raise ValueError("You already hold the highest bid — wait for another team to bid")

            if phase == R.PHASE_B:
                if len(json_loads(team["players"], [])) < ruleset.required_players:
                    raise ValueError("Incomplete teams cannot participate in Phase B")
                required = ruleset.phase_b_price
                if amount != required:
                    raise ValueError(f"Phase B price is fixed at {required}")
            else:
                if phase not in set(ruleset.flow()) or not R.is_tier_phase(phase):
                    raise ValueError("Bidding is not open in this phase")
                base = int(player["base_price"] or 0)
                current_bid = int(player["current_bid"] or 0)
                required = max(base, current_bid + ruleset.bid_increment)
                if amount < required:
                    raise ValueError(f"Bid must be at least {required}")
                if (amount - base) % ruleset.bid_increment != 0:
                    raise ValueError(f"Bids must rise in {ruleset.bid_increment} increments")

            # The purse IS the manager's wallet; guard against money spent out
            # of it (wagers/vault) between the bid and the close.
            wallet = self._team_wallet(conn, team)
            if int(wallet["liquid_cash"]) < amount:
                raise ValueError("Not enough funds in the team account")
            credits_cost = int(player["credits"] or 0)
            if int(team["credits_remaining"]) < credits_cost:
                raise ValueError("Not enough credits")

            conn.execute(
                "UPDATE players SET current_bid = ?, current_bidder_team_id = ? WHERE id = ?",
                (amount, team_id, player_id),
            )
            bid_id = secrets.token_hex(8)
            conn.execute(
                "INSERT INTO bids (id, season_id, ts, team_id, player_id, amount, phase, kind) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'bid')",
                (bid_id, season_id, _now(), team_id, player_id, amount, phase),
            )
            self._log(conn, season_id, "bid", actor,
                      before={"player_id": player_id, "team_id": team_id},
                      after={"bid_id": bid_id, "player_id": player_id, "team_id": team_id,
                             "amount": amount},
                      ref_player_id=player_id, ref_team_id=team_id)
        return self._get_player(season_id, player_id)

    def delete_bid(self, season_id: str, bid_id: str, actor: str = "admin") -> dict:
        """Admin removes a mistaken bid on the CURRENT lot mid-auction.

        The player's top bid / bidder reverts to the previous bid (or to
        0/none if it was the only bid). Bids on already-sold lots cannot be
        deleted — the sale is settled history."""
        with self.db.write() as conn:
            meta = self._get_meta(conn, season_id)
            current_player_id = meta["current_player_id"]
            bid = conn.execute(
                "SELECT * FROM bids WHERE id = ? AND season_id = ?", (bid_id, season_id)).fetchone()
            if not bid:
                raise ValueError("Bid not found")
            if not current_player_id or bid["player_id"] != current_player_id:
                raise ValueError("Only bids on the current lot can be deleted")
            bid = row_to_dict(bid)
            conn.execute("DELETE FROM bids WHERE id = ?", (bid_id,))
            top = conn.execute(
                "SELECT * FROM bids WHERE player_id = ? AND kind = 'bid' "
                "ORDER BY amount DESC, ts DESC, rowid DESC LIMIT 1",
                (bid["player_id"],),
            ).fetchone()
            if top:
                conn.execute(
                    "UPDATE players SET current_bid = ?, current_bidder_team_id = ? WHERE id = ?",
                    (top["amount"], top["team_id"], bid["player_id"]),
                )
            else:
                conn.execute(
                    "UPDATE players SET current_bid = 0, current_bidder_team_id = NULL "
                    "WHERE id = ?", (bid["player_id"],),
                )
            self._log(conn, season_id, "delete_bid", actor,
                      before={"bid_id": bid["id"], "season_id": season_id, "ts": bid["ts"],
                              "team_id": bid["team_id"], "player_id": bid["player_id"],
                              "amount": bid["amount"], "phase": bid["phase"]},
                      after={"player_id": bid["player_id"]},
                      ref_player_id=bid["player_id"], ref_team_id=bid["team_id"])
        return {"ok": True, "deleted": bid_id}

    def pass_current(self, season_id: str, team_id: str, actor: str = "manager") -> dict:
        with self.db.write() as conn:
            meta = self._get_meta(conn, season_id)
            player_id = meta["current_player_id"]
            if not player_id:
                raise ValueError("No active player")
            team = conn.execute("SELECT * FROM teams WHERE id = ? AND season_id = ?",
                                (team_id, season_id)).fetchone()
            if not team:
                raise ValueError("Invalid team")
            if not team["is_active"]:
                raise ValueError("This team is excluded from auction participation")
            if team["control_status"] == R.CONTROL_TAKEOVER and actor != R.ROLE_ADMIN:
                raise ValueError("This team is under admin control; the manager cannot pass")
            bid_id = secrets.token_hex(8)
            conn.execute(
                "INSERT INTO bids (id, season_id, ts, team_id, player_id, amount, phase, kind) "
                "VALUES (?, ?, ?, ?, ?, 0, ?, 'pass')",
                (bid_id, season_id, _now(), team_id, player_id, meta["phase"]),
            )
            self._log(conn, season_id, "pass", actor,
                      before={"player_id": player_id, "team_id": team_id},
                      after={"bid_id": bid_id, "player_id": player_id, "team_id": team_id},
                      ref_player_id=player_id, ref_team_id=team_id)
        return {"ok": True}

    def close_current(self, season_id: str, actor: str = "admin") -> dict:
        with self.db.write() as conn:
            meta = self._get_meta(conn, season_id)
            player_id = meta["current_player_id"]
            if not player_id:
                raise ValueError("No active player")
            player = conn.execute("SELECT * FROM players WHERE id = ?", (player_id,)).fetchone()
            if not player:
                raise ValueError("Invalid player")

            if not player["current_bidder_team_id"]:
                conn.execute("UPDATE auction_meta SET current_player_id = NULL WHERE season_id = ?",
                             (season_id,))
                self._log(conn, season_id, "close_unsold", actor,
                          before={"player_id": player_id},
                          after={"player_id": player_id}, ref_player_id=player_id)
                return {"sold": False, "reason": "No bid"}

            team = conn.execute("SELECT * FROM teams WHERE id = ?", (player["current_bidder_team_id"],)).fetchone()
            if not team:
                raise ValueError("Bidder team not found")
            phase = meta["phase"]
            players_list = json_loads(team["players"], [])
            bench_list = json_loads(team["bench"], [])
            ruleset = self._get_ruleset(conn, season_id)
            is_bench = phase == R.PHASE_B and len(players_list) >= ruleset.required_players
            squad = "bench" if is_bench else "players"
            if is_bench:
                bench_list.append(player_id)
            else:
                players_list.append(player_id)

            price = int(player["current_bid"] or 0)
            credits = int(player["credits"] or 0)
            conn.execute(
                "UPDATE teams SET players = ?, bench = ?, spent = ?, "
                "credits_remaining = ? WHERE id = ?",
                (json_dumps(players_list), json_dumps(bench_list),
                 int(team["spent"]) + price,
                 int(team["credits_remaining"]) - credits,
                 team["id"]),
            )
            if price:
                self._wallet_adjust(conn, team, -price,
                                    f"Sold {player['name']} for {price}", tx_type="auction_close")
            conn.execute(
                "UPDATE players SET status = 'sold', sold_to_team_id = ?, sold_price = ?, "
                "phase_sold = ?, current_bid = 0, current_bidder_team_id = NULL WHERE id = ?",
                (team["id"], price, phase, player_id),
            )
            conn.execute("UPDATE auction_meta SET current_player_id = NULL WHERE season_id = ?",
                         (season_id,))
            self._log(conn, season_id, "close_sold", actor,
                      before={"player_id": player_id, "team_id": team["id"], "price": price,
                              "credits": credits, "squad": squad},
                      after={"player_id": player_id}, ref_player_id=player_id, ref_team_id=team["id"])
            return {"sold": True, "team_name": team["name"], "price": price}

    # ------------------------------------------------------------------
    # trades (break phase)
    # ------------------------------------------------------------------
    def request_trade(self, season_id: str, from_team_id: str, to_team_id: str,
                      offered_player_id: str, requested_player_id: str = None,
                      cash_from_initiator: int = 0, cash_from_target: int = 0,
                      actor: str = "manager") -> dict:
        with self.db.write() as conn:
            meta = self._get_meta(conn, season_id)
            if meta["phase"] != R.PHASE_BREAK:
                raise ValueError("Trades are allowed only during the break phase")
            from_team = conn.execute("SELECT * FROM teams WHERE id = ? AND season_id = ?",
                                     (from_team_id, season_id)).fetchone()
            to_team = conn.execute("SELECT * FROM teams WHERE id = ? AND season_id = ?",
                                   (to_team_id, season_id)).fetchone()
            if not from_team or not to_team:
                raise ValueError("Invalid teams for trade")
            if not from_team["is_active"] or not to_team["is_active"]:
                raise ValueError("Inactive teams cannot trade")
            if from_team["control_status"] == R.CONTROL_TAKEOVER and actor != R.ROLE_ADMIN:
                raise ValueError("This team is under admin control; the manager cannot trade")
            from_players = json_loads(from_team["players"], [])
            to_players = json_loads(to_team["players"], [])
            if offered_player_id not in from_players:
                raise ValueError("You can only offer a player you own")
            if requested_player_id and requested_player_id not in to_players:
                raise ValueError("Requested player is not owned by target team")
            trade_id = secrets.token_hex(8)
            conn.execute(
                "INSERT INTO trade_requests (id, season_id, status, created_at, from_team_id, "
                "to_team_id, offered_player_id, requested_player_id, cash_from_initiator, "
                "cash_from_target) VALUES (?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?)",
                (trade_id, season_id, _now(), from_team_id, to_team_id, offered_player_id,
                 requested_player_id, max(0, int(cash_from_initiator)), max(0, int(cash_from_target))),
            )
            self._log(conn, season_id, "trade_request", actor,
                      before={"trade_id": trade_id},
                      after={"trade_id": trade_id}, ref_team_id=from_team_id)
            return {"id": trade_id, "status": "pending"}

    def respond_trade(self, season_id: str, trade_id: str, target_team_id: str, action: str,
                      actor: str = "manager") -> dict:
        with self.db.write() as conn:
            meta = self._get_meta(conn, season_id)
            if meta["phase"] != R.PHASE_BREAK:
                raise ValueError("Trade responses are allowed only during the break phase")
            trade = conn.execute("SELECT * FROM trade_requests WHERE id = ?", (trade_id,)).fetchone()
            if not trade:
                raise ValueError("Trade request not found")
            if trade["status"] != "pending":
                raise ValueError("Trade request is already resolved")
            if trade["to_team_id"] != target_team_id:
                raise ValueError("Only the target manager can respond to this trade")
            if action == "reject":
                conn.execute("UPDATE trade_requests SET status = 'rejected', responded_at = ?, "
                             "responded_by_team_id = ? WHERE id = ?",
                             (_now(), target_team_id, trade_id))
                return {"id": trade_id, "status": "rejected"}
            if action != "accept":
                raise ValueError("Invalid action")
            self._execute_trade(conn, season_id, row_to_dict(trade), actor)
            return {"id": trade_id, "status": "accepted"}

    def _execute_trade(self, conn, season_id: str, trade: dict, actor: str):
        from_team = conn.execute("SELECT * FROM teams WHERE id = ?", (trade["from_team_id"],)).fetchone()
        to_team = conn.execute("SELECT * FROM teams WHERE id = ?", (trade["to_team_id"],)).fetchone()
        if not from_team or not to_team:
            raise ValueError("Teams no longer available")
        if not from_team["is_active"] or not to_team["is_active"]:
            raise ValueError("Inactive teams cannot trade")
        from_players = json_loads(from_team["players"], [])
        to_players = json_loads(to_team["players"], [])
        offered = trade["offered_player_id"]
        requested = trade.get("requested_player_id")

        if offered not in from_players:
            raise ValueError("Offered player is no longer owned by initiator")
        if requested and requested not in to_players:
            raise ValueError("Requested player is no longer owned by target")

        if requested:
            from_players.remove(offered)
            to_players.remove(requested)
            from_players.append(requested)
            to_players.append(offered)
        else:
            from_players.remove(offered)
            to_players.append(offered)

        cash_from_initiator = int(trade.get("cash_from_initiator") or 0)
        cash_from_target = int(trade.get("cash_from_target") or 0)

        from_credits = self._recalculate_team_credits(conn, season_id, from_team["id"],
                                                      from_players)
        to_credits = self._recalculate_team_credits(conn, season_id, to_team["id"], to_players)
        if from_credits < 0 or to_credits < 0:
            raise ValueError("Trade violates the team credit limit")

        from_delta = cash_from_target - cash_from_initiator
        to_delta = cash_from_initiator - cash_from_target
        from_account = self._team_wallet(conn, from_team)
        to_account = self._team_wallet(conn, to_team)
        if int(from_account["liquid_cash"]) + from_delta < 0:
            raise ValueError("Trade cash transfer exceeds available purse")
        if int(to_account["liquid_cash"]) + to_delta < 0:
            raise ValueError("Trade cash transfer exceeds available purse")

        conn.execute(
            "UPDATE teams SET players = ?, credits_remaining = ? WHERE id = ?",
            (json_dumps(from_players), from_credits, from_team["id"]),
        )
        conn.execute(
            "UPDATE teams SET players = ?, credits_remaining = ? WHERE id = ?",
            (json_dumps(to_players), to_credits, to_team["id"]),
        )
        if from_delta:
            self._wallet_adjust(conn, from_team, from_delta, "Trade cash (break)", tx_type="trade")
        if to_delta:
            self._wallet_adjust(conn, to_team, to_delta, "Trade cash (break)", tx_type="trade")
        conn.execute("UPDATE players SET sold_to_team_id = ? WHERE id = ?",
                     (to_team["id"], offered))
        if requested:
            conn.execute("UPDATE players SET sold_to_team_id = ? WHERE id = ?",
                         (from_team["id"], requested))
        conn.execute("UPDATE trade_requests SET status = 'accepted', responded_at = ?, "
                     "responded_by_team_id = ? WHERE id = ?",
                     (_now(), to_team["id"], trade["id"]))
        self._log(conn, season_id, "trade_accept", actor,
                  before={"trade_id": trade["id"], "from_team_id": from_team["id"],
                          "to_team_id": to_team["id"], "offered_player_id": offered,
                          "requested_player_id": requested,
                          "cash_from_initiator": cash_from_initiator,
                          "cash_from_target": cash_from_target},
                  after={"trade_id": trade["id"]},
                  ref_team_id=from_team["id"])

    def _recalculate_team_credits(self, conn, season_id: str, team_id: str, players_list: list) -> int:
        team = conn.execute("SELECT * FROM teams WHERE id = ?", (team_id,)).fetchone()
        ruleset = self._get_ruleset(conn, season_id)
        used = ruleset.credits_for(team["manager_tier"])
        if players_list:
            placeholders = ",".join("?" for _ in players_list)
            rows = conn.execute(
                f"SELECT id, credits FROM players WHERE id IN ({placeholders})", players_list
            ).fetchall()
        else:
            rows = []
        for p in rows:
            used += int(p["credits"] or 0)
        return ruleset.total_credits - used

    def get_trade_requests_for_team(self, season_id: str, team_id: str) -> dict:
        with self.db.read() as conn:
            rows = conn.execute(
                "SELECT * FROM trade_requests WHERE from_team_id = ? OR to_team_id = ? "
                "ORDER BY created_at DESC",
                (team_id, team_id),
            ).fetchall()
            players_by_id = {p["id"]: p for p in conn.execute(
                "SELECT * FROM players WHERE season_id = ?", (season_id,)).fetchall()}
            teams_by_id = {t["id"]: t for t in conn.execute(
                "SELECT * FROM teams WHERE season_id = ?", (season_id,)).fetchall()}

            def enrich(item):
                offered = players_by_id.get(item.get("offered_player_id"), {})
                requested = players_by_id.get(item.get("requested_player_id")) if item.get("requested_player_id") else None
                return {
                    **item,
                    "offered_player_name": offered.get("name", "Unknown"),
                    "requested_player_name": requested.get("name", "-") if requested else "-",
                    "from_team_name": teams_by_id.get(item.get("from_team_id"), {}).get("name", "Unknown"),
                    "to_team_name": teams_by_id.get(item.get("to_team_id"), {}).get("name", "Unknown"),
                }

            incoming = [enrich(dict(r)) for r in rows
                        if r["to_team_id"] == team_id and r["status"] == "pending"]
            outgoing = [enrich(dict(r)) for r in rows if r["from_team_id"] == team_id]
            return {"incoming": incoming, "outgoing": outgoing}

    # ------------------------------------------------------------------
    # draft completion, transfers, takeover, publish
    # ------------------------------------------------------------------
    def complete_draft(self, season_id: str, actor: str = "admin") -> dict:
        with self.db.write() as conn:
            ruleset = self._get_ruleset(conn, season_id)
            before_teams = rows_to_dicts(conn.execute(
                "SELECT * FROM teams WHERE season_id = ?", (season_id,)).fetchall())
            before = {
                "phase": self._get_meta(conn, season_id)["phase"],
                "teams": before_teams,
                # Wallet snapshot per team so undo can restore the forfeited
                # wallets of incomplete teams (teams.purse_remaining is gone;
                # the wallet is the purse now).
                "team_wallets": {
                    t["id"]: int(self._team_wallet(conn, t)["liquid_cash"]) for t in before_teams
                },
                "players": rows_to_dicts(conn.execute(
                    "SELECT * FROM players WHERE season_id = ?", (season_id,)).fetchall()),
            }
            unsold = conn.execute(
                "SELECT * FROM players WHERE season_id = ? AND status = 'unsold' ORDER BY rowid",
                (season_id,),
            ).fetchall()
            unsold = list(unsold)
            for team in conn.execute("SELECT * FROM teams WHERE season_id = ?", (season_id,)).fetchall():
                if not team["is_active"]:
                    continue
                players_list = json_loads(team["players"], [])
                if len(players_list) >= ruleset.required_players:
                    continue
                needed = ruleset.required_players - len(players_list)
                assign = unsold[:needed]
                unsold = unsold[needed:]
                players_list = players_list + [p["id"] for p in assign]
                conn.execute(
                    "UPDATE teams SET players = ?, credits_remaining = ? "
                    "WHERE id = ?",
                    (json_dumps(players_list),
                     self._recalculate_team_credits(conn, season_id, team["id"], players_list),
                     team["id"]),
                )
                # Penalty: the incomplete team's remaining money (its wallet) is forfeit.
                wallet = self._team_wallet(conn, team)
                liquid = int(wallet["liquid_cash"])
                if liquid > 0:
                    self.bank.adjust(wallet["id"], -liquid,
                                     "Draft completion penalty (incomplete team)",
                                     tx_type="penalty", conn=conn)
                for p in assign:
                    conn.execute(
                        "UPDATE players SET status = 'sold', sold_to_team_id = ?, sold_price = 0, "
                        "phase_sold = 'phase_b' WHERE id = ?",
                        (team["id"], p["id"]),
                    )
            conn.execute("UPDATE auction_meta SET phase = 'complete', current_player_id = NULL "
                         "WHERE season_id = ?", (season_id,))
            conn.execute("UPDATE seasons SET status = 'completed' WHERE id = ?", (season_id,))
            self._log(conn, season_id, "complete_draft", actor,
                      before={"snapshot": before}, after={"phase": "complete"})
        return self.get_state(season_id)

    def admin_transfer(self, season_id: str, team_to: str, player_id: str, team_from: str = None,
                       price: int = 0, credits: int = 0, note: str = "", actor: str = "admin") -> dict:
        price = int(price or 0)
        credits = int(credits or 0)
        with self.db.write() as conn:
            season = conn.execute("SELECT status FROM seasons WHERE id = ?", (season_id,)).fetchone()
            if not season or season["status"] != "completed":
                raise ValueError("Transfers are allowed after the auction is completed")
            player = conn.execute("SELECT * FROM players WHERE id = ? AND season_id = ?",
                                  (player_id, season_id)).fetchone()
            if not player:
                raise ValueError("Player not found")
            to_team = conn.execute("SELECT * FROM teams WHERE id = ? AND season_id = ?",
                                   (team_to, season_id)).fetchone()
            if not to_team:
                raise ValueError("Target team not found")
            current_owner = player["sold_to_team_id"]
            if team_from and team_from != current_owner:
                raise ValueError("Player is not owned by the source team")
            from_team = None
            if current_owner:
                from_team = conn.execute("SELECT * FROM teams WHERE id = ?", (current_owner,)).fetchone()

            to_players = json_loads(to_team["players"], [])
            to_bench = json_loads(to_team["bench"], [])
            from_players = json_loads(from_team["players"], []) if from_team else []
            from_bench = json_loads(from_team["bench"], []) if from_team else []

            # Move out of source.
            if current_owner and player_id in from_players:
                from_players.remove(player_id)
            elif current_owner and player_id in from_bench:
                from_bench.remove(player_id)
            elif current_owner:
                raise ValueError("Player not found on source team roster")

            # Add to target (bench only if the active XI is full).
            ruleset = self._get_ruleset(conn, season_id)
            if len(to_players) >= ruleset.required_players:
                to_bench.append(player_id)
            else:
                to_players.append(player_id)

            # Money: target pays price to source (or to treasury if free agent).
            to_account = self._team_wallet(conn, to_team)
            if int(to_account["liquid_cash"]) < price:
                raise ValueError("Target team cannot afford this transfer price")
            new_to_credits = int(to_team["credits_remaining"]) - credits
            new_from_credits = int(from_team["credits_remaining"]) + credits if from_team else None
            if new_to_credits < 0:
                raise ValueError("Target team does not have enough credits for this player")

            conn.execute(
                "UPDATE teams SET players = ?, bench = ?, credits_remaining = ? "
                "WHERE id = ?",
                (json_dumps(to_players), json_dumps(to_bench), new_to_credits, to_team["id"]),
            )
            if from_team:
                conn.execute(
                    "UPDATE teams SET players = ?, bench = ?, credits_remaining = ? "
                    "WHERE id = ?",
                    (json_dumps(from_players), json_dumps(from_bench),
                     new_from_credits, from_team["id"]),
                )
            if price:
                self._wallet_adjust(conn, to_team, -price, note or f"Transfer of {player['name']}",
                                    tx_type="transfer")
                if from_team:
                    self._wallet_adjust(conn, from_team, price,
                                        note or f"Transfer of {player['name']}",
                                        tx_type="transfer")
            conn.execute("UPDATE players SET sold_to_team_id = ? WHERE id = ?", (team_to, player_id))
            transfer_id = secrets.token_hex(8)
            conn.execute(
                "INSERT INTO transfers (id, season_id, team_from, team_to, player_id, price, "
                "credits, created_by, created_at, note) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (transfer_id, season_id, current_owner, team_to, player_id, price, credits,
                 actor, _now(), note),
            )
            self._log(conn, season_id, "transfer", actor,
                      before={"transfer_id": transfer_id, "team_from": current_owner,
                              "team_to": team_to, "player_id": player_id, "price": price,
                              "credits": credits},
                      after={"transfer_id": transfer_id},
                      ref_player_id=player_id, ref_team_id=team_to)
            return {"id": transfer_id, "ok": True}

    def takeover_team(self, season_id: str, team_id: str, reason: str, actor: str = "admin") -> dict:
        with self.db.write() as conn:
            team = conn.execute("SELECT * FROM teams WHERE id = ? AND season_id = ?",
                                (team_id, season_id)).fetchone()
            if not team:
                raise ValueError("Team not found")
            if team["control_status"] == R.CONTROL_TAKEOVER:
                raise ValueError("Team is already under admin control")
            before = row_to_dict(team)
            conn.execute(
                "UPDATE teams SET control_status = 'admin_takeover', takeover_reason = ?, "
                "takeover_by = ?, takeover_at = ? WHERE id = ?",
                ((reason or "").strip(), actor, _now(), team_id),
            )
            self._log(conn, season_id, "takeover", actor,
                      before={"team_id": team_id, "row": before},
                      after={"team_id": team_id, "reason": reason},
                      ref_team_id=team_id)
        return self._get_team(season_id, team_id)

    def restore_team(self, season_id: str, team_id: str, actor: str = "admin") -> dict:
        with self.db.write() as conn:
            team = conn.execute("SELECT * FROM teams WHERE id = ? AND season_id = ?",
                                (team_id, season_id)).fetchone()
            if not team:
                raise ValueError("Team not found")
            if team["control_status"] != R.CONTROL_TAKEOVER:
                raise ValueError("Team is not under admin control")
            before = row_to_dict(team)
            conn.execute(
                "UPDATE teams SET control_status = 'manager_controlled', takeover_reason = NULL, "
                "takeover_by = NULL, takeover_at = NULL WHERE id = ?",
                (team_id,),
            )
            self._log(conn, season_id, "restore", actor,
                      before={"team_id": team_id, "row": before},
                      after={"team_id": team_id}, ref_team_id=team_id)
        return self._get_team(season_id, team_id)

    def publish(self, season_id: str, name: str = None, actor: str = "admin") -> dict:
        state = self.get_state(season_id)
        snapshot_id = secrets.token_hex(8)
        with self.db.write() as conn:
            conn.execute(
                "INSERT INTO season_snapshots (id, season_id, name, published_at, payload) "
                "VALUES (?, ?, ?, ?, ?)",
                (snapshot_id, season_id, (name or "").strip() or season_id, _now(),
                 json_dumps(state)),
            )
            conn.execute("UPDATE seasons SET status = 'completed' WHERE id = ?", (season_id,))
            self._log(conn, season_id, "publish", actor,
                      before={"snapshot_id": snapshot_id}, after={"snapshot_id": snapshot_id})
        return {"id": snapshot_id, "ok": True}

    # ------------------------------------------------------------------
    # action log & undo
    # ------------------------------------------------------------------
    def _log(self, conn, season_id: str, action_type: str, actor: str,
             before: dict = None, after: dict = None,
             ref_player_id: str = None, ref_team_id: str = None) -> str:
        action_id = secrets.token_hex(8)
        conn.execute(
            "INSERT INTO auction_action_log (id, season_id, action_type, actor, ref_player_id, "
            "ref_team_id, before_state, after_state, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (action_id, season_id, action_type, actor, ref_player_id, ref_team_id,
             json_dumps(before), json_dumps(after), _now()),
        )
        return action_id

    def action_log(self, season_id: str, limit: int = 50) -> list:
        with self.db.read() as conn:
            return rows_to_dicts(conn.execute(
                "SELECT * FROM auction_action_log WHERE season_id = ? "
                "ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (season_id, limit),
            ).fetchall())

    def undo_last_action(self, season_id: str, actor: str = "admin") -> dict:
        with self.db.write() as conn:
            row = conn.execute(
                "SELECT * FROM auction_action_log WHERE season_id = ? AND undone_at IS NULL "
                "ORDER BY created_at DESC, rowid DESC LIMIT 1",
                (season_id,),
            ).fetchone()
            if not row:
                raise ValueError("Nothing to undo")
            handler = _UNDO_HANDLERS.get(row["action_type"])
            if not handler:
                raise ValueError(f"Action '{row['action_type']}' cannot be undone")
            handler(self, conn, season_id, row_to_dict(row))
            conn.execute("UPDATE auction_action_log SET undone_at = ?, undo_of = ? WHERE id = ?",
                         (_now(), row["id"], row["id"]))
            return {"ok": True, "action_type": row["action_type"], "id": row["id"]}

    # ------------------------------------------------------------------
    # state
    # ------------------------------------------------------------------
    def get_state(self, season_id: str, bid_limit: int = 25) -> dict:
        with self.db.read() as conn:
            season = conn.execute("SELECT * FROM seasons WHERE id = ?", (season_id,)).fetchone()
            if not season:
                raise ValueError("Season not found")
            meta = self._get_meta(conn, season_id)
            ruleset = self._get_ruleset(conn, season_id)
            players = rows_to_dicts(conn.execute(
                "SELECT * FROM players WHERE season_id = ? ORDER BY rowid", (season_id,)).fetchall())
            teams = rows_to_dicts(conn.execute(
                "SELECT * FROM teams WHERE season_id = ? ORDER BY name", (season_id,)).fetchall())
            players_by_id = {p["id"]: p for p in players}
            teams_by_id = {t["id"]: t for t in teams}
            gp_rows = conn.execute("SELECT * FROM global_players").fetchall()
            global_by_id = {g["id"]: row_to_dict(g) for g in gp_rows}
            gt_rows = {g["id"]: row_to_dict(g) for g in conn.execute("SELECT * FROM global_teams").fetchall()}

            all_bids = rows_to_dicts(conn.execute(
                "SELECT * FROM bids WHERE season_id = ? ORDER BY ts DESC, rowid DESC", (season_id,)).fetchall())
            enriched_bids = []
            current_lot_bids = []
            current_player_id = meta["current_player_id"]
            for bid in all_bids:
                team = teams_by_id.get(bid["team_id"])
                player = players_by_id.get(bid["player_id"])
                enriched = {
                    **bid,
                    "team_name": team["name"] if team else "-",
                    "player_name": player["name"] if player else "-",
                    "ts_display": _fmt_bid_time(bid["ts"]),
                }
                if bid_limit is None or len(enriched_bids) < bid_limit:
                    enriched_bids.append(enriched)
                if current_player_id and bid["player_id"] == current_player_id:
                    current_lot_bids.append(enriched)

            current_player = None
            if current_player_id:
                current_player = players_by_id.get(current_player_id)
                if current_player:
                    current_player = dict(current_player)
                    bidder = teams_by_id.get(current_player["current_bidder_team_id"]) if current_player["current_bidder_team_id"] else None
                    current_player["current_bidder_team_name"] = bidder["name"] if bidder else "-"

            prefix_map = {"gold": "(G)", "silver": "(S)", "platinum": "(P)"}
            enriched_teams = []
            for team in teams:
                manager_gp = global_by_id.get(team["manager_player_id"])
                player_labels = [
                    f"{prefix_map.get(players_by_id[pid]['tier'], '')} {players_by_id[pid]['name']}".strip()
                    for pid in json_loads(team["players"], []) if pid in players_by_id
                ]
                bench_labels = [
                    f"{prefix_map.get(players_by_id[pid]['tier'], '')} {players_by_id[pid]['name']}".strip()
                    for pid in json_loads(team["bench"], []) if pid in players_by_id
                ]
                acct_row = conn.execute(
                    "SELECT liquid_cash FROM bank_accounts WHERE owner_type = 'player' "
                    "AND owner_id = ?", (team["manager_player_id"],)).fetchone()
                gt = gt_rows.get((team["global_team_id"] or "").strip() or team["id"]) or {}
                enriched_teams.append({
                    **team,
                    "players": json_loads(team["players"], []),
                    "bench": json_loads(team["bench"], []),
                    "manager_name": manager_gp["name"] if manager_gp else "-",
                    "manager_speciality": manager_gp["speciality"] if manager_gp else "",
                    "wallet": int(acct_row["liquid_cash"]) if acct_row else 0,
                    "player_labels": player_labels,
                    "bench_labels": bench_labels,
                    "logo": gt.get("logo") or "",
                    "banner": gt.get("banner") or "",
                })

            unsold_count = sum(1 for p in players if p["status"] == "unsold")
            incomplete_fill = sum(
                max(0, ruleset.required_players - len(json_loads(t["players"], [])))
                for t in teams if t["is_active"]
                and len(json_loads(t["players"], [])) < ruleset.required_players
            )
            snapshots = rows_to_dicts(conn.execute(
                "SELECT * FROM season_snapshots WHERE season_id = ? ORDER BY published_at DESC",
                (season_id,)).fetchall())

            return {
                "season": row_to_dict(season),
                "ruleset": ruleset.as_dict(),
                "flow": ruleset.flow(),
                "phase": meta["phase"],
                "break_started_at": meta.get("break_started_at"),
                "current_player": current_player,
                "teams": enriched_teams,
                "players": [
                    {**p, "sold_to_team_name": teams_by_id.get(p["sold_to_team_id"], {}).get("name", "-")
                     if p["sold_to_team_id"] else "-"}
                    for p in players
                ],
                "bids": enriched_bids,
                "current_lot_bids": current_lot_bids,
                "phase_b_readiness": {
                    "unsold_players": unsold_count,
                    "incomplete_fill_needed": incomplete_fill,
                    "can_enter_phase_b": unsold_count > incomplete_fill,
                },
                "public_budget_board": [
                    {
                        "team_name": t["name"],
                        "is_active": bool(t["is_active"]),
                        "control_status": t["control_status"],
                        "purse_remaining": t["wallet"],
                        "credits_remaining": t["credits_remaining"],
                        "active_count": len(t["players"]),
                        "bench_count": len(t["bench"]),
                        "logo_url": BrandingService.team_logo({"logo": t["logo"], "banner": t["banner"]}),
                    }
                    for t in enriched_teams
                ],
                "snapshots": snapshots,
            }

    def _get_player(self, season_id: str, player_id: str) -> dict:
        with self.db.read() as conn:
            row = conn.execute("SELECT * FROM players WHERE id = ? AND season_id = ?",
                               (player_id, season_id)).fetchone()
            return row_to_dict(row)

    def _get_team(self, season_id: str, team_id: str) -> dict:
        with self.db.read() as conn:
            row = conn.execute("SELECT * FROM teams WHERE id = ? AND season_id = ?",
                               (team_id, season_id)).fetchone()
            if not row:
                return None
            team = row_to_dict(row)
            team["players"] = json_loads(team["players"], [])
            team["bench"] = json_loads(team["bench"], [])
            # The team's purse IS the manager's wallet.
            acct = conn.execute(
                "SELECT liquid_cash FROM bank_accounts WHERE owner_type = 'player' "
                "AND owner_id = ?", (team["manager_player_id"],)).fetchone()
            team["wallet"] = int(acct["liquid_cash"]) if acct else 0
            return team

# ----------------------------------------------------------------------
# undo handlers: (service, conn, season_id, action_row) -> None
# ----------------------------------------------------------------------
def _undo_bid(svc, conn, season_id, row):
    after = json_loads(row["after_state"], {})
    bid_id = after.get("bid_id")
    player_id = after.get("player_id")
    if bid_id:
        conn.execute("DELETE FROM bids WHERE id = ?", (bid_id,))
    if player_id:
        top = conn.execute(
            "SELECT * FROM bids WHERE player_id = ? AND kind = 'bid' "
            "ORDER BY amount DESC, ts DESC, rowid DESC LIMIT 1",
            (player_id,),
        ).fetchone()
        if top:
            conn.execute("UPDATE players SET current_bid = ?, current_bidder_team_id = ? WHERE id = ?",
                         (top["amount"], top["team_id"], player_id))
        else:
            conn.execute("UPDATE players SET current_bid = 0, current_bidder_team_id = NULL WHERE id = ?",
                         (player_id,))


def _undo_delete_bid(svc, conn, season_id, row):
    """Re-insert a deleted bid and restore it as the top bid if it was."""
    before = json_loads(row["before_state"], {})
    bid_id = before.get("bid_id")
    if not bid_id:
        return
    conn.execute(
        "INSERT INTO bids (id, season_id, ts, team_id, player_id, amount, phase, kind) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 'bid')",
        (bid_id, before.get("season_id") or season_id, before.get("ts"),
         before.get("team_id"), before.get("player_id"), before.get("amount"),
         before.get("phase")),
    )
    top = conn.execute(
        "SELECT * FROM bids WHERE player_id = ? AND kind = 'bid' "
        "ORDER BY amount DESC, ts DESC, rowid DESC LIMIT 1",
        (before.get("player_id"),),
    ).fetchone()
    if top:
        conn.execute("UPDATE players SET current_bid = ?, current_bidder_team_id = ? WHERE id = ?",
                     (top["amount"], top["team_id"], before["player_id"]))


def _undo_pass(svc, conn, season_id, row):
    after = json_loads(row["after_state"], {})
    if after.get("bid_id"):
        conn.execute("DELETE FROM bids WHERE id = ?", (after["bid_id"],))


def _undo_close_unsold(svc, conn, season_id, row):
    before = json_loads(row["before_state"], {})
    player_id = before.get("player_id")
    conn.execute("UPDATE auction_meta SET current_player_id = ? WHERE season_id = ?",
                 (player_id, season_id))


def _undo_close_sold(svc, conn, season_id, row):
    before = json_loads(row["before_state"], {})
    player_id = before["player_id"]
    team_id = before["team_id"]
    price = int(before.get("price") or 0)
    credits = int(before.get("credits") or 0)
    squad = before.get("squad", "players")
    team = conn.execute("SELECT * FROM teams WHERE id = ?", (team_id,)).fetchone()
    if team:
        players_list = json_loads(team["players"], [])
        bench_list = json_loads(team["bench"], [])
        if player_id in players_list:
            players_list.remove(player_id)
        if player_id in bench_list:
            bench_list.remove(player_id)
        conn.execute(
            "UPDATE teams SET players = ?, bench = ?, spent = ?, "
            "credits_remaining = ? WHERE id = ?",
            (json_dumps(players_list), json_dumps(bench_list),
             max(0, int(team["spent"]) - price),
             int(team["credits_remaining"]) + credits,
             team_id),
        )
        if price:
            svc._wallet_adjust(conn, team, price, "Undo lot close", tx_type="auction_close")
    conn.execute(
        "UPDATE players SET status = 'unsold', sold_to_team_id = NULL, sold_price = 0, "
        "phase_sold = NULL, current_bid = 0, current_bidder_team_id = NULL WHERE id = ?",
        (player_id,),
    )
    conn.execute("UPDATE auction_meta SET current_player_id = ? WHERE season_id = ?",
                 (player_id, season_id))


def _undo_nominate(svc, conn, season_id, row):
    before = json_loads(row["before_state"], {})
    after = json_loads(row["after_state"], {})
    prev_current = before.get("previous_current_player_id")
    history = before.get("history") or []
    nominated = after.get("player_id")
    meta = svc._get_meta(conn, season_id)
    if R.is_tier_phase(meta["phase"]) and nominated:
        conn.execute("UPDATE players SET nominated_phase_a = 0 WHERE id = ?", (nominated,))
    conn.execute(
        "UPDATE auction_meta SET current_player_id = ?, nomination_history = ? WHERE season_id = ?",
        (prev_current, json_dumps(history), season_id),
    )


def _undo_set_phase(svc, conn, season_id, row):
    before = json_loads(row["before_state"], {})
    conn.execute("UPDATE auction_meta SET phase = ? WHERE season_id = ?",
                 (before.get("phase", "setup"), season_id))
    if before.get("phase") != R.PHASE_BREAK:
        conn.execute("UPDATE auction_meta SET break_started_at = NULL WHERE season_id = ?",
                     (season_id,))


def _undo_gift(svc, conn, season_id, row):
    before = json_loads(row["before_state"], {})
    after = json_loads(row["after_state"], {})
    team_id = before["team_id"]
    operation = after.get("operation", "add")
    amount = int(after.get("amount") or 0)
    delta = -amount if operation == "add" else amount
    team = conn.execute("SELECT * FROM teams WHERE id = ?", (team_id,)).fetchone()
    if team:
        svc._wallet_adjust(conn, team, delta, "Undo gift", tx_type="gift")


def _undo_trade_request(svc, conn, season_id, row):
    before = json_loads(row["before_state"], {})
    if before.get("trade_id"):
        conn.execute("DELETE FROM trade_requests WHERE id = ?", (before["trade_id"],))


def _undo_trade_accept(svc, conn, season_id, row):
    before = json_loads(row["before_state"], {})
    trade_id = before["trade_id"]
    from_team_id = before["from_team_id"]
    to_team_id = before["to_team_id"]
    offered = before["offered_player_id"]
    requested = before.get("requested_player_id")
    cash_from_initiator = int(before.get("cash_from_initiator") or 0)
    cash_from_target = int(before.get("cash_from_target") or 0)
    from_team = conn.execute("SELECT * FROM teams WHERE id = ?", (from_team_id,)).fetchone()
    to_team = conn.execute("SELECT * FROM teams WHERE id = ?", (to_team_id,)).fetchone()
    if not from_team or not to_team:
        return
    from_players = json_loads(from_team["players"], [])
    to_players = json_loads(to_team["players"], [])
    # Reverse: offered is now in to_team; requested (if any) is now in from_team.
    if offered in to_players:
        to_players.remove(offered)
        from_players.append(offered)
    if requested and requested in from_players:
        from_players.remove(requested)
        to_players.append(requested)
    conn.execute(
        "UPDATE teams SET players = ?, credits_remaining = ? WHERE id = ?",
        (json_dumps(from_players),
         svc._recalculate_team_credits(conn, season_id, from_team_id, from_players),
         from_team_id),
    )
    conn.execute(
        "UPDATE teams SET players = ?, credits_remaining = ? WHERE id = ?",
        (json_dumps(to_players),
         svc._recalculate_team_credits(conn, season_id, to_team_id, to_players),
         to_team_id),
    )
    from_delta = cash_from_initiator - cash_from_target
    if from_delta:
        svc._wallet_adjust(conn, from_team, from_delta, "Undo trade cash", tx_type="trade")
    to_delta = cash_from_target - cash_from_initiator
    if to_delta:
        svc._wallet_adjust(conn, to_team, to_delta, "Undo trade cash", tx_type="trade")
    conn.execute("UPDATE players SET sold_to_team_id = ? WHERE id = ?", (from_team_id, offered))
    if requested:
        conn.execute("UPDATE players SET sold_to_team_id = ? WHERE id = ?", (to_team_id, requested))
    conn.execute("UPDATE trade_requests SET status = 'pending' WHERE id = ?", (trade_id,))


def _undo_transfer(svc, conn, season_id, row):
    before = json_loads(row["before_state"], {})
    team_from = before.get("team_from")
    team_to = before.get("team_to")
    player_id = before.get("player_id")
    price = int(before.get("price") or 0)
    credits = int(before.get("credits") or 0)
    player = conn.execute("SELECT * FROM players WHERE id = ?", (player_id,)).fetchone()
    if not player:
        return
    to_team = conn.execute("SELECT * FROM teams WHERE id = ?", (team_to,)).fetchone()
    if not to_team:
        return
    to_players = json_loads(to_team["players"], [])
    to_bench = json_loads(to_team["bench"], [])
    if player_id in to_players:
        to_players.remove(player_id)
    if player_id in to_bench:
        to_bench.remove(player_id)
    conn.execute(
        "UPDATE teams SET players = ?, bench = ?, credits_remaining = ? WHERE id = ?",
        (json_dumps(to_players), json_dumps(to_bench),
         int(to_team["credits_remaining"]) + credits,
         team_to),
    )
    if price:
        svc._wallet_adjust(conn, to_team, price, "Undo transfer", tx_type="transfer")
    if team_from:
        from_team = conn.execute("SELECT * FROM teams WHERE id = ?", (team_from,)).fetchone()
        if from_team:
            from_players = json_loads(from_team["players"], [])
            from_bench = json_loads(from_team["bench"], [])
            if len(from_players) < svc._get_ruleset(conn, season_id).required_players:
                from_players.append(player_id)
            else:
                from_bench.append(player_id)
            conn.execute(
                "UPDATE teams SET players = ?, bench = ?, credits_remaining = ? "
                "WHERE id = ?",
                (json_dumps(from_players), json_dumps(from_bench),
                 int(from_team["credits_remaining"]) - credits,
                 team_from),
            )
            if price:
                svc._wallet_adjust(conn, from_team, -price, "Undo transfer", tx_type="transfer")
            conn.execute("UPDATE players SET sold_to_team_id = ? WHERE id = ?", (team_from, player_id))
        else:
            conn.execute("UPDATE players SET sold_to_team_id = NULL WHERE id = ?", (player_id,))
    else:
        conn.execute("UPDATE players SET sold_to_team_id = NULL WHERE id = ?", (player_id,))
    if before.get("transfer_id"):
        conn.execute("UPDATE transfers SET note = note || ' (undone)' WHERE id = ?",
                     (before["transfer_id"],))


def _undo_takeover(svc, conn, season_id, row):
    before = json_loads(row["before_state"], {})
    team_id = before["team_id"]
    prev = before.get("row") or {}
    conn.execute(
        "UPDATE teams SET control_status = ?, takeover_reason = ?, takeover_by = ?, takeover_at = ? "
        "WHERE id = ?",
        (prev.get("control_status", "manager_controlled"), prev.get("takeover_reason"),
         prev.get("takeover_by"), prev.get("takeover_at"), team_id),
    )


def _undo_restore(svc, conn, season_id, row):
    before = json_loads(row["before_state"], {})
    team_id = before["team_id"]
    prev = before.get("row") or {}
    conn.execute(
        "UPDATE teams SET control_status = 'admin_takeover', takeover_reason = ?, takeover_by = ?, "
        "takeover_at = ? WHERE id = ?",
        (prev.get("takeover_reason"), prev.get("takeover_by"), prev.get("takeover_at"), team_id),
    )


def _undo_complete_draft(svc, conn, season_id, row):
    before = json_loads(row["before_state"], {})
    snapshot = before.get("snapshot") or {}
    teams = snapshot.get("teams") or []
    players = snapshot.get("players") or []
    team_wallets = before.get("team_wallets") or {}
    conn.execute("DELETE FROM teams WHERE season_id = ?", (season_id,))
    conn.execute("DELETE FROM players WHERE season_id = ?", (season_id,))
    for team in teams:
        # purse_remaining was dropped from the schema — strip it from any
        # snapshot taken before the migration.
        row_dict = {k: v for k, v in team.items() if k != "purse_remaining"}
        cols = ", ".join(row_dict.keys())
        marks = ", ".join("?" for _ in row_dict)
        conn.execute(f"INSERT INTO teams ({cols}) VALUES ({marks})", list(row_dict.values()))
    for player in players:
        cols = ", ".join(player.keys())
        marks = ", ".join("?" for _ in player)
        conn.execute(f"INSERT INTO players ({cols}) VALUES ({marks})", list(player.values()))
    conn.execute("UPDATE auction_meta SET phase = ?, current_player_id = NULL WHERE season_id = ?",
                 (snapshot.get("phase", "phase_b"), season_id))
    conn.execute("UPDATE seasons SET status = 'setup' WHERE id = ?", (season_id,))
    # Restore the forfeited wallets of incomplete teams (purse was zeroed as a penalty).
    ruleset = svc._get_ruleset(conn, season_id)
    for team in teams:
        if len(json_loads(team.get("players"), [])) < ruleset.required_players:
            wallet = int(team_wallets.get(team["id"]) or 0)
            if wallet > 0:
                svc._wallet_adjust(conn, team, wallet, "Undo draft completion penalty",
                                   tx_type="penalty")


def _undo_publish(svc, conn, season_id, row):
    before = json_loads(row["before_state"], {})
    if before.get("snapshot_id"):
        conn.execute("DELETE FROM season_snapshots WHERE id = ?", (before["snapshot_id"],))


def _undo_add_player(svc, conn, season_id, row):
    before = json_loads(row["before_state"], {})
    if before.get("player_id"):
        conn.execute("DELETE FROM players WHERE id = ?", (before["player_id"],))


def _undo_update_player(svc, conn, season_id, row):
    before = json_loads(row["before_state"], {})
    player = before.get("row")
    if player:
        cols = ", ".join(f"{k} = ?" for k in player)
        conn.execute(f"UPDATE players SET {cols} WHERE id = ?", (*player.values(), player["id"]))


def _undo_delete_player(svc, conn, season_id, row):
    before = json_loads(row["before_state"], {})
    player = before.get("row")
    if player:
        cols = ", ".join(player.keys())
        marks = ", ".join("?" for _ in player)
        conn.execute(f"INSERT INTO players ({cols}) VALUES ({marks})", list(player.values()))


def _undo_create_team(svc, conn, season_id, row):
    before = json_loads(row["before_state"], {})
    team_id = before.get("team_id")
    if team_id:
        team = conn.execute("SELECT * FROM teams WHERE id = ?", (team_id,)).fetchone()
        if team:
            wallet = svc._team_wallet(conn, team)
            liquid = int(wallet["liquid_cash"])
            if liquid > 0:
                svc.bank.adjust(wallet["id"], -liquid, "Undo team creation",
                                tx_type="purse", conn=conn)
        conn.execute("DELETE FROM teams WHERE id = ?", (team_id,))


def _undo_delete_team(svc, conn, season_id, row):
    before = json_loads(row["before_state"], {})
    team = before.get("row")
    if team:
        row_dict = {k: v for k, v in team.items() if k != "purse_remaining"}
        cols = ", ".join(row_dict.keys())
        marks = ", ".join("?" for _ in row_dict)
        conn.execute(f"INSERT INTO teams ({cols}) VALUES ({marks})", list(row_dict.values()))
        wallet = int(before.get("wallet") or 0)
        if wallet > 0:
            svc._wallet_adjust(conn, row_dict, wallet, "Undo team deletion", tx_type="purse")


_UNDO_HANDLERS = {
    "bid": _undo_bid,
    "delete_bid": _undo_delete_bid,
    "pass": _undo_pass,
    "close_unsold": _undo_close_unsold,
    "close_sold": _undo_close_sold,
    "nominate": _undo_nominate,
    "set_phase": _undo_set_phase,
    "gift": _undo_gift,
    "trade_request": _undo_trade_request,
    "trade_accept": _undo_trade_accept,
    "transfer": _undo_transfer,
    "takeover": _undo_takeover,
    "restore": _undo_restore,
    "complete_draft": _undo_complete_draft,
    "publish": _undo_publish,
    "add_player": _undo_add_player,
    "update_player": _undo_update_player,
    "delete_player": _undo_delete_player,
    "create_team": _undo_create_team,
    "delete_team": _undo_delete_team,
}
