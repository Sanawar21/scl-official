"""Import the deployed Season 1 data (prod-data/) into the rebuild's SQLite DB.

Phase 1 (core): global players, season + S1 ruleset, players, teams, bids,
and a rebuilt published snapshot. Manager *user accounts* are NOT imported
(players self-signup and the admin links them).
Phase 2 (stats): match registry + scorer stats (team/player match rows), the
teams.global_team_id backfill, and a league-table cross-check vs the old
aggregates. Run `--phase stats` after core (or after a fresh core import).
Finance data is deferred until their schema tables exist
(see PROD_IMPORT_PLAN.md).

Usage:
    ./.venv/Scripts/python.exe scripts/import_prod.py [--data prod-data] [--db data/scl.db] [--force]
    ./.venv/Scripts/python.exe scripts/import_prod.py --phase stats [--data prod-data] [--db data/scl.db] [--force]

Refuses to run if the target DB already has imported rows (beyond the seeded
admin user) unless --force is given.
"""
import argparse
import json
import secrets
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.db import Database, json_dumps  # noqa: E402
from app.services.auction_service import AuctionService  # noqa: E402
from app.services.scorer_service import ScorerService  # noqa: E402

# Expected row counts from the source files (sanity checks).
EXPECTED = {
    "global_players": 17,
    "players": 17,
    "teams": 4,
    "users": 4,          # source count (managers in old prod; not imported)
    "bids": 66,
    "season_snapshots": 1,
}

# Season 1 ruleset (S1 economy != S2 defaults). Reconstructed from the old setup
# snapshot + season-1 data — see PROD_IMPORT_PLAN.md §4.3.
S1_RULESET = {
    "phase_order": ["silver", "gold", "break", "platinum", "phase_b"],
    "tier_purses": {"platinum": 4000, "gold": 4800, "silver": 5500},
    "tier_base_prices": {"platinum": 1500, "gold": 800, "silver": 400},
    "tier_credits": {"platinum": 3, "gold": 2, "silver": 1},
    "total_credits": 8,
    "bid_increment": 50,
    "phase_b_price": 0,
    "credit_refund_rate": 500,
    "required_players": 3,
    "roster_size": 4,
    "break_minutes": 5,
    "match_reward_amount": 200,
}

SEASON_ID = "season-1"
SEASON_NAME = "Season 1"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_json(path: Path) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _tiny_rows(db_file: dict, table: str) -> list:
    """Return the list of docs in a TinyDB-style {table: {doc_id: doc}} file."""
    return list((db_file.get(table) or {}).values())


def _int_bool(value) -> int:
    return 1 if value else 0


def target_has_data(db: Database) -> list:
    """Which core tables already have rows (excluding the seeded admin user)."""
    touched = ["global_players", "seasons", "players", "teams", "bids", "season_snapshots"]
    existing = []
    with db.read() as conn:
        for table in touched:
            n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            if n:
                existing.append(f"{table}={n}")
    return existing


def stats_has_data(db: Database) -> list:
    """Which stats tables already have rows."""
    touched = ["match_registry", "match_stats", "match_team_stats", "match_player_stats"]
    existing = []
    with db.read() as conn:
        for table in touched:
            n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            if n:
                existing.append(f"{table}={n}")
    return existing


def import_core(db: Database, data_dir: Path) -> dict:
    league = _load_json(data_dir / "global_league_db.json")
    season_file = _load_json(data_dir / "season_dbs" / "season-1.json")
    live = _load_json(data_dir / "auction_live_db.json")

    global_players = _tiny_rows(league, "global_players")
    players = _tiny_rows(season_file, "players")
    teams = _tiny_rows(season_file, "teams")
    users = _tiny_rows(season_file, "users")
    season_meta = _tiny_rows(season_file, "season_meta")
    bids = _tiny_rows(live, "bids")

    if len(global_players) != EXPECTED["global_players"]:
        raise SystemExit(f"Unexpected global_players count: {len(global_players)}")
    if len(players) != EXPECTED["players"]:
        raise SystemExit(f"Unexpected players count: {len(players)}")
    if len(teams) != EXPECTED["teams"]:
        raise SystemExit(f"Unexpected teams count: {len(teams)}")
    if len(users) - 1 != EXPECTED["users"]:
        raise SystemExit(f"Unexpected users count: {len(users)}")
    if len(bids) != EXPECTED["bids"]:
        raise SystemExit(f"Unexpected bids count: {len(bids)}")

    season_meta = season_meta[0] if season_meta else {}
    published_at = season_meta.get("published_at") or _now()

    with db.write() as conn:
        # 1. global players
        for gp in global_players:
            conn.execute(
                "INSERT INTO global_players (id, name, tier, speciality, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (gp["id"], gp["name"], gp["tier"], (gp.get("speciality") or "ALL_ROUNDER").upper(),
                 gp.get("created_at") or _now()),
            )

        # 2. season + ruleset + auction meta
        conn.execute(
            "INSERT INTO seasons (id, name, status, created_at) VALUES (?, ?, 'completed', ?)",
            (SEASON_ID, SEASON_NAME, season_meta.get("created_at") or _now()),
        )
        conn.execute(
            "INSERT INTO rulesets (id, season_id, phase_order, tier_purses, tier_base_prices, "
            "tier_credits, total_credits, bid_increment, phase_b_price, credit_refund_rate, "
            "required_players, roster_size, break_minutes, match_reward_amount) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                secrets.token_hex(8), SEASON_ID,
                json_dumps(S1_RULESET["phase_order"]),
                json_dumps(S1_RULESET["tier_purses"]),
                json_dumps(S1_RULESET["tier_base_prices"]),
                json_dumps(S1_RULESET["tier_credits"]),
                S1_RULESET["total_credits"], S1_RULESET["bid_increment"],
                S1_RULESET["phase_b_price"], S1_RULESET["credit_refund_rate"],
                S1_RULESET["required_players"], S1_RULESET["roster_size"],
                S1_RULESET["break_minutes"], S1_RULESET["match_reward_amount"],
            ),
        )
        conn.execute(
            "INSERT INTO auction_meta (season_id, phase, current_player_id, nomination_history) "
            "VALUES (?, 'complete', NULL, '[]')",
            (SEASON_ID,),
        )

        # 3. players (13 auction + 4 managers)
        for p in players:
            conn.execute(
                "INSERT INTO players (id, season_id, global_player_id, name, tier, speciality, "
                "base_price, credits, status, sold_to_team_id, sold_price, phase_sold, "
                "current_bid, current_bidder_team_id, nominated_phase_a, nomination_order) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, NULL, ?, NULL)",
                (
                    p["id"], SEASON_ID, p.get("global_player_id"), p["name"], p["tier"],
                    (p.get("speciality") or "ALL_ROUNDER").upper(),
                    int(p.get("base_price") or 0), int(p.get("credits") or 0),
                    p.get("status") or "unsold", p.get("sold_to"),
                    int(p.get("sold_price") or 0), p.get("phase_sold"),
                    _int_bool(p.get("nominated_phase_a")),
                ),
            )

        # 4. teams (final purses already include all season finance transactions;
        #    the purse lives in the manager's bank account now, seeded by the
        #    finance phase from the source JSON)
        for t in teams:
            conn.execute(
                "INSERT INTO teams (id, season_id, name, manager_player_id, manager_tier, "
                "spent, credits_remaining, players, bench, is_active, "
                "control_status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 'manager_controlled')",
                (
                    t["id"], SEASON_ID, t["name"], t.get("manager_global_player_id"),
                    t.get("manager_tier") or "silver",
                    int(t.get("spent") or 0),
                    int(t.get("credits_remaining") or 0),
                    json_dumps(t.get("players") or []), json_dumps(t.get("bench") or []),
                ),
            )

        # 5. bids (verbatim, including legacy phase strings)
        #    NOTE: manager *user accounts* are deliberately NOT imported. In the
        #    rebuild, players create their own accounts and the admin links them
        #    to their player profile + team (auth.link_page). Importing the old
        #    prod accounts would (a) bypass self-signup and (b) carry over old
        #    password hashes (a shared default) into live accounts — a security
        #    hole. Teams/players/wallets are imported; logins are not.
        for b in bids:
            conn.execute(
                "INSERT INTO bids (id, season_id, ts, team_id, player_id, amount, phase, kind) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (b["id"], SEASON_ID, b["ts"], b["team_id"], b["player_id"],
                 int(b.get("amount") or 0), b.get("phase"), b.get("kind") or "bid"),
            )

    # 7. published snapshot — rebuilt from live state (WAL: must be after commit,
    #    and the shape must match what published.html expects).
    auction = AuctionService(db)
    state = auction.get_state(SEASON_ID)
    with db.write() as conn:
        conn.execute(
            "INSERT INTO season_snapshots (id, season_id, name, published_at, payload) "
            "VALUES (?, ?, ?, ?, ?)",
            (secrets.token_hex(8), SEASON_ID, SEASON_NAME, published_at, json_dumps(state)),
        )

    return {
        "global_players": len(global_players),
        "players": len(players),
        "teams": len(teams),
        "users": 0,  # logins are self-signup now; never imported
        "bids": len(bids),
        "season_snapshots": 1,
    }


# ---------------------------------------------------------------------------
# Phase 2: match registry + scorer stats
# ---------------------------------------------------------------------------
EXPECTED_STATS = {
    "scorer_match_registry": 13,
    "scorer_match_stats": 13,
    "scorer_team_match_stats": 26,
    "scorer_player_match_stats": 93,
    "season_team_links": 4,
}

TEAM_STATS_COLUMNS = (
    "runs_scored", "balls_faced", "wickets_lost", "fours", "sixes",
    "wides_faced", "noballs_faced", "runs_conceded", "balls_bowled",
    "wickets_taken", "wides_bowled", "noballs_bowled", "overs_faced",
    "overs_bowled", "run_rate_for", "run_rate_against", "result", "wins",
    "losses", "ties", "no_results",
)

PLAYER_STATS_COLUMNS = (
    "matches", "innings_batted", "not_out", "dismissed", "runs", "balls_faced",
    "fours", "sixes", "innings_bowled", "balls_bowled", "runs_conceded",
    "wickets", "wides", "noballs", "strike_rate", "economy",
)


def import_stats(db: Database, data_dir: Path) -> dict:
    """Phase 2: match registry, match stats, team/player match rows, global_team_id.

    Rows are imported verbatim (they already use global team/player ids); the
    registry gains venue/date/teams/winner derived from the stats tables (the
    old registry table did not store them). M6 is a walkover whose team rows
    exist in the source — imported as-is. After the load, the league table is
    recomputed and compared against the old scorer_team_global_stats.
    """
    league = _load_json(data_dir / "global_league_db.json")
    registry_rows = _tiny_rows(league, "scorer_match_registry")
    match_rows = _tiny_rows(league, "scorer_match_stats")
    team_rows = _tiny_rows(league, "scorer_team_match_stats")
    player_rows = _tiny_rows(league, "scorer_player_match_stats")
    links = _tiny_rows(league, "season_team_links")
    old_aggregates = _tiny_rows(league, "scorer_team_global_stats")

    for table, rows, expected in (
        ("scorer_match_registry", registry_rows, EXPECTED_STATS["scorer_match_registry"]),
        ("scorer_match_stats", match_rows, EXPECTED_STATS["scorer_match_stats"]),
        ("scorer_team_match_stats", team_rows, EXPECTED_STATS["scorer_team_match_stats"]),
        ("scorer_player_match_stats", player_rows, EXPECTED_STATS["scorer_player_match_stats"]),
        ("season_team_links", links, EXPECTED_STATS["season_team_links"]),
    ):
        if len(rows) != expected:
            raise SystemExit(f"Unexpected {table} count: {len(rows)} (expected {expected})")

    stats_by_key = {s["match_key"]: s for s in match_rows}
    team_by_key = {}
    for t in team_rows:
        team_by_key.setdefault(t["match_key"], []).append(t)

    with db.write() as conn:
        # 1. match registry (derive venue/date/teams/winner from the stats rows)
        for r in registry_rows:
            key = r["match_key"]
            stat = stats_by_key.get(key) or {}
            teams = team_by_key.get(key) or []
            team_ids = [t["team_id"] for t in teams][:2]
            winner = stat.get("winner_team_id") or ""
            conn.execute(
                "INSERT INTO match_registry (match_key, season_id, match_id, match_number, "
                "match_title, \"between\", venue, match_date, team_a_global_id, team_b_global_id, "
                "walkover, walkover_winner_team_id, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (key, SEASON_ID, r["match_id"], r.get("match_number") or "",
                 r.get("match_title") or "", r.get("between") or "",
                 stat.get("venue") or "", stat.get("match_date") or "",
                 team_ids[0] if len(team_ids) > 0 else "",
                 team_ids[1] if len(team_ids) > 1 else "",
                 _int_bool(r.get("walkover")),
                 winner if r.get("walkover") else "",
                 r.get("created_at") or _now(), r.get("updated_at") or _now()),
            )

        # 2. match stats (upload metadata + result)
        for s in match_rows:
            conn.execute(
                "INSERT INTO match_stats (match_key, season_id, match_id, result, toss, "
                "winner_team_id, delivery_rows, team_rows, player_rows, source_file, "
                "uploaded_by, uploaded_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (s["match_key"], SEASON_ID, s["match_id"], s.get("result") or "",
                 s.get("toss") or "", s.get("winner_team_id") or "",
                 int(s.get("delivery_rows") or 0), int(s.get("team_rows") or 0),
                 int(s.get("player_rows") or 0), s.get("source_file") or "",
                 s.get("uploaded_by") or "admin", s.get("uploaded_at") or _now()),
            )

        # 3. team match stats (verbatim; already global ids)
        for t in team_rows:
            values = {
                "id": t.get("id") or secrets.token_hex(8),
                "match_key": t["match_key"], "season_id": SEASON_ID,
                "team_id": t["team_id"], "team_name": t["team_name"],
            }
            for col in TEAM_STATS_COLUMNS:
                values[col] = t.get(col)
            conn.execute(
                "INSERT INTO match_team_stats (id, match_key, season_id, team_id, team_name, "
                + ", ".join(TEAM_STATS_COLUMNS) + ") VALUES ("
                + ", ".join(["?"] * (5 + len(TEAM_STATS_COLUMNS))) + ")",
                tuple(values[col] for col in
                      ("id", "match_key", "season_id", "team_id", "team_name") + TEAM_STATS_COLUMNS),
            )

        # 4. player match stats (verbatim; already global ids)
        for p in player_rows:
            values = {
                "id": p.get("id") or secrets.token_hex(8),
                "match_key": p["match_key"], "season_id": SEASON_ID,
                "player_id": p["player_id"], "player_name": p["player_name"],
                "team_id": p["team_id"], "team_name": p["team_name"],
                "role": p.get("role"), "tier": p.get("tier"),
            }
            for col in PLAYER_STATS_COLUMNS:
                values[col] = p.get(col)
            conn.execute(
                "INSERT INTO match_player_stats (id, match_key, season_id, player_id, "
                "player_name, team_id, team_name, role, tier, "
                + ", ".join(PLAYER_STATS_COLUMNS) + ") VALUES ("
                + ", ".join(["?"] * (9 + len(PLAYER_STATS_COLUMNS))) + ")",
                tuple(values[col] for col in
                      ("id", "match_key", "season_id", "player_id", "player_name",
                       "team_id", "team_name", "role", "tier") + PLAYER_STATS_COLUMNS),
            )

        # 5. backfill teams.global_team_id
        backfilled = 0
        for link in links:
            cur = conn.execute(
                "UPDATE teams SET global_team_id = ? WHERE id = ? AND "
                "(global_team_id IS NULL OR global_team_id = '')",
                (link["global_team_id"], link["local_team_id"]))
            backfilled += cur.rowcount

    # 6. cross-check: recomputed league table vs old team aggregates
    scorer = ScorerService(db)
    standings = scorer.league_table(SEASON_ID)
    recomputed = {s["team_id"]: s for s in standings}
    old_by_id = {o["team_id"]: o for o in old_aggregates}
    mismatches = []
    for tid, old in old_by_id.items():
        new = recomputed.get(tid)
        if not new:
            mismatches.append(f"{old['team_name']}: missing from recomputed table")
            continue
        checks = {
            "wins": (int(old.get("wins") or 0), new["wins"]),
            "losses": (int(old.get("losses") or 0), new["losses"]),
            "points": (int(old.get("wins") or 0) * 2, new["points"]),
            "runs_for": (int(old.get("runs_scored") or 0), new["runs_for"]),
            "balls_for": (int(old.get("balls_faced") or 0), new["balls_for"]),
            "runs_against": (int(old.get("runs_conceded") or 0), new["runs_against"]),
            "balls_against": (int(old.get("balls_bowled") or 0), new["balls_against"]),
        }
        for label, (expected, actual) in checks.items():
            if expected != actual:
                mismatches.append(f"{old['team_name']} {label}: old={expected} new={actual}")
        # NRR tolerance (float rounding)
        old_nrr = float(old.get("net_run_rate") or 0)
        if abs(old_nrr - new["nrr"]) > 0.001:
            mismatches.append(f"{old['team_name']} nrr: old={old_nrr:.4f} new={new['nrr']:.4f}")

    return {
        "match_registry": len(registry_rows),
        "match_stats": len(match_rows),
        "match_team_stats": len(team_rows),
        "match_player_stats": len(player_rows),
        "teams_global_id_backfilled": backfilled,
        "cross_check_mismatches": mismatches,
    }


EXPECTED_FINANCE = 44


def finance_has_data(db: Database) -> list:
    touched = ["season_finance_entries"]
    existing = []
    with db.read() as conn:
        for table in touched:
            n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            if n:
                existing.append(f"{table}={n}")
    return existing


def import_finance(db: Database, data_dir: Path) -> dict:
    """Phase 3: import the 44 S1 finance transactions + seed manager wallets."""
    from app.services.bank_service import BankService

    season_file = _load_json(data_dir / "season_dbs" / "season-1.json")
    finance_rows = list((season_file.get("finance_transactions") or {}).values())

    if len(finance_rows) != EXPECTED_FINANCE:
        raise SystemExit(f"Unexpected finance_transactions count: {len(finance_rows)}")

    bank = BankService(db)
    mismatches = []

    with db.write() as conn:
        # 1. Insert all 44 ledger rows verbatim.
        for r in finance_rows:
            from_team_id = r.get("from_team_id") or r.get("team_id")
            to_team_id = r.get("to_team_id") or (r.get("from_team_id") and r.get("team_id"))
            conn.execute(
                "INSERT INTO season_finance_entries (id, season_id, match_id, team_id, team_name, "
                "type, operation, amount, comment, created_by, from_team_id, to_team_id, "
                "before_wallet, after_wallet, created_at) VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, "
                "?, ?, ?, ?, ?)",
                (
                    secrets.token_hex(8), SEASON_ID,
                    r.get("team_id"), r.get("team_name") or "",
                    r.get("type") or "adjust",
                    r.get("operation"),
                    int(r.get("amount") or 0),
                    r.get("comment") or "",
                    r.get("created_by") or "admin",
                    from_team_id, to_team_id,
                    r.get("before_purse", r.get("from_before_purse")),
                    r.get("after_purse", r.get("from_after_purse")),
                    r.get("created_at") or _now(),
                ),
            )

        # 2. Self-consistency: every adjust/transfer preserves before→after delta.
        for r in finance_rows:
            typ = (r.get("type") or "").strip().lower()
            amt = int(r.get("amount") or 0)
            if typ == "adjust":
                bp = r.get("before_purse")
                ap = r.get("after_purse")
                op = (r.get("operation") or "").strip().lower()
                expected = bp + amt if op == "add" else bp - amt
                if expected != ap:
                    mismatches.append(
                        f"{r.get('team_name','?')} {r.get('comment','?')}: "
                        f"expected {expected} got {ap}")
            elif typ == "transfer":
                if r.get("from_before_purse") is not None:
                    expected = r["from_before_purse"] - amt
                    actual = r.get("from_after_purse")
                    if expected != actual:
                        mismatches.append(
                            f"{r.get('comment','?')}: from expected {expected} got {actual}")
                if r.get("to_before_purse") is not None:
                    expected = r["to_before_purse"] + amt
                    actual = r.get("to_after_purse")
                    if expected != actual:
                        mismatches.append(
                            f"{r.get('comment','?')}: to expected {expected} got {actual}")

        # 3. Terminal-value check: last after_wallet per team == final purse from
        #    the source JSON (the teams table no longer stores a purse — the
        #    manager's wallet is the purse). Transfers move money between two
        #    teams, so both sides carry a purse (from_after_purse /
        #    to_after_purse) — the last row touching a team, whatever its side,
        #    determines its final purse.
        source_teams = {t["id"]: t for t in _tiny_rows(season_file, "teams")}
        last_after = {}
        for r in finance_rows:
            typ = (r.get("type") or "").strip().lower()
            tid = r.get("team_id")
            if tid:
                last_after[tid] = r.get("after_purse")
            if typ == "transfer":
                if r.get("from_team_id"):
                    last_after[r["from_team_id"]] = r.get("from_after_purse")
                if r.get("to_team_id"):
                    last_after[r["to_team_id"]] = r.get("to_after_purse")
        for tid, last in last_after.items():
            team = source_teams.get(tid)
            if team and int(last) != int(team.get("purse_remaining") or 0):
                mismatches.append(
                    f"{team.get('name','?')}: final ledger {last} != "
                    f"source purse {team.get('purse_remaining')}")

        # 4. Seed manager wallets with the final purse (from the source JSON).
        for tid, t in source_teams.items():
            manager = (t.get("manager_global_player_id") or "").strip()
            if not manager:
                continue
            purse = int(t.get("purse_remaining") or 0)
            account = bank.get_or_create_account("player", manager, conn=conn)
            bank.adjust(account["id"], purse, "Season 1 final purse",
                        tx_type="purse", conn=conn)

    return {
        "finance_transactions": len(finance_rows),
        "cross_check_mismatches": mismatches,
    }


def copy_assets(data_dir: Path, db_dir: Path) -> None:
    """Copy raw match CSVs + scorer config so the rebuild carries the sources."""
    matches_src = data_dir / "matches"
    if matches_src.is_dir():
        (db_dir / "matches").mkdir(parents=True, exist_ok=True)
        for csv_file in sorted(matches_src.glob("*.csv")):
            shutil.copy2(csv_file, db_dir / "matches" / csv_file.name)
    config = data_dir / "scorer_config.json"
    if config.is_file():
        shutil.copy2(config, db_dir / "scorer_config.json")


def main() -> int:
    parser = argparse.ArgumentParser(description="Import Season 1 prod data into the rebuild DB.")
    parser.add_argument("--data", default="prod-data", help="source data directory")
    parser.add_argument("--db", default="data/scl.db", help="target SQLite DB path")
    parser.add_argument("--force", action="store_true",
                        help="import even if the target DB already has data")
    parser.add_argument("--phase", choices=["core", "stats", "finance", "all"], default="core",
                        help="which phase to run (default: core; 'all' = core + stats + finance)")
    args = parser.parse_args()

    data_dir = Path(args.data)
    if not data_dir.is_dir():
        raise SystemExit(f"Data directory not found: {data_dir}")
    db = Database(args.db)
    db.bootstrap()

    phases = [args.phase] if args.phase != "all" else ["core", "stats", "finance"]
    for phase in phases:
        if phase == "stats":
            existing = stats_has_data(db)
            if existing and not args.force:
                raise SystemExit(
                    f"Stats tables already have data ({', '.join(existing)}). "
                    "Use --force to import anyway."
                )
            summary = import_stats(db, data_dir)
            print("Stats import complete:")
            for table, count in summary.items():
                if isinstance(count, list):
                    continue
                print(f"  {table:<22} {count}")
            mismatches = summary.get("cross_check_mismatches") or []
            if mismatches:
                print("  CROSS-CHECK MISMATCHES:")
                for m in mismatches:
                    print(f"    - {m}")
            else:
                print("  cross-check: league table matches old aggregates (no mismatches)")
            if mismatches:
                return 2
            continue

        if phase == "finance":
            existing = finance_has_data(db)
            if existing and not args.force:
                raise SystemExit(
                    f"Finance tables already have data ({', '.join(existing)}). "
                    "Use --force to import anyway."
                )
            summary = import_finance(db, data_dir)
            print("Finance import complete:")
            for table, count in summary.items():
                print(f"  {table:<22} {count}")
            mismatches = summary.get("cross_check_mismatches") or []
            if mismatches:
                print("  CROSS-CHECK MISMATCHES:")
                for m in mismatches:
                    print(f"    - {m}")
            else:
                print("  cross-check: ledger chains and final purses match (no mismatches)")
            if mismatches:
                return 2
            continue

        # core
        existing = target_has_data(db)
        if existing and not args.force:
            raise SystemExit(
                f"Target DB already has data ({', '.join(existing)}). "
                "Use --force to import anyway."
            )
        summary = import_core(db, data_dir)
        copy_assets(data_dir, Path(args.db).parent)
        print("Core import complete:")
        for table, count in summary.items():
            print(f"  {table:<18} {count}")
        print(f"  matches/ + scorer_config.json  copied to {Path(args.db).parent}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
