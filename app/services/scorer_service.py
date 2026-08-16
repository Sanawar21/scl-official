"""Matches & stats engine, ported from the reference app's scorer_service.py.

- match_registry / match_stats / match_team_stats / match_player_stats tables.
- Ball-by-ball CSV import (local ids normalized to global ids), walkover handling,
  overwrite confirmation, undo.
- On-demand league table (points 2/1/0 -> NRR -> head-to-head -> boundaries),
  leaderboards, match summaries, and team/player profiles.
Fantasy points use the ported scoreCard formula (per-ball points, tier multipliers,
matchup upsets, 25pt match bonus; substitutes score 0).
"""
import csv
import io
import json
import re
import secrets
from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal
from datetime import datetime, timezone
from functools import cmp_to_key
from pathlib import Path

from ..db import json_dumps, json_loads, row_to_dict, rows_to_dicts


class MatchOverwriteConfirmationRequired(ValueError):
    def __init__(self, season_id: str, match_id: str):
        self.season_id = (season_id or "").strip().lower()
        self.match_id = (match_id or "").strip()
        self.requires_confirmation = True
        super().__init__(
            f"Match {self.match_id or '-'} already has uploaded data for season "
            f"{self.season_id or '-'}. Confirm overwrite to replace it."
        )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _round_nearest_int(value, default=0):
    try:
        return int(Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    except (TypeError, ValueError, ArithmeticError):
        return default


def _overs_string(valid_balls: int):
    balls = max(0, int(valid_balls or 0))
    return f"{balls // 6}.{balls % 6}"


def _norm(value: str):
    return " ".join((value or "").strip().lower().split())


def _speciality_to_role(speciality: str):
    value = (speciality or "").strip().upper()
    return value if value in {"BATTER", "BOWLER", "ALL_ROUNDER"} else "ALL_ROUNDER"


def _match_key(season_id: str, match_id: str) -> str:
    season = (season_id or "global").strip().lower() or "global"
    safe = re.sub(r"[^a-z0-9_-]+", "-", (match_id or "").strip().lower()) or "match"
    return f"{season}:{safe}"


def _match_number_sort_value(match_number: str):
    text = str(match_number or "").strip()
    if not text:
        return (10 ** 9, "")
    match = re.search(r"\d+", text)
    if match:
        return (int(match.group(0)), text.lower())
    return (10 ** 9 - 1, text.lower())


def _slugify_fragment(value: str):
    text = re.sub(r"[^a-z0-9]+", "-", (value or "").strip().lower())
    return text.strip("-") or "item"


def team_profile_slug(team_id: str, team_name: str) -> str:
    safe_id = (team_id or "").strip().lower()
    suffix = safe_id[:8] if safe_id else "team"
    return f"{_slugify_fragment(team_name)}-{suffix}"


def player_profile_slug(player_id: str, player_name: str) -> str:
    safe_id = (player_id or "").strip().lower()
    suffix = safe_id[:8] if safe_id else "player"
    return f"{_slugify_fragment(player_name)}-{suffix}"


def _season_sort_key(slug: str):
    safe = (slug or "").strip().lower()
    if not safe:
        return (10 ** 9, "")
    match = re.search(r"(\d+)$", safe)
    if match:
        return (int(match.group(1)), safe)
    return (10 ** 9 - 1, safe)


class ScorerService:
    DEFAULT_CONFIG = {
        "title": "SCL Scorer",
        "version": "1.3.0",
        "season_slug": "",
        "max_overs": 3,
    }

    CSV_REQUIRED_COLUMNS = (
        "Match ID", "Match", "Venue", "Innings Order", "Batting Team",
        "Batting Team ID", "Batting Manager ID", "Over Number", "Ball Number",
        "Valid Ball?", "Batter", "Batter ID", "Non Strike Batter",
        "Non Strike Batter ID", "Bowler", "Bowler ID", "Bowling Team",
        "Bowling Team ID", "Bowling Manager ID", "Runs Bat", "Runs Extra",
        "Extras Type", "Dismissed Batter", "Dismissed Batter ID",
        "Progressive Runs", "Progressive Wickets", "Match Toss", "Match Result",
    )

    FANTASY_TIERS = {
        "S": {"value": 1, "reward": 1.1, "penalty": 0.9},
        "G": {"value": 2, "reward": 1.0, "penalty": 1.0},
        "P": {"value": 3, "reward": 0.9, "penalty": 1.1},
    }
    FANTASY_BAT_POINTS = {0: -3, 1: 0, 2: +1, 3: +2, 4: +4, 6: +6, "OUT": -7}
    FANTASY_BOWL_POINTS = {0: +3, 1: +1, 2: 0, 3: -2, 4: -4, 6: -5, "WICKET": +8}
    FANTASY_MATCH_BONUS_POINTS = 25.0
    FANTASY_PLAYER_ROLES = {
        "ahmad": "BATTER", "qambar": "ALL_ROUNDER", "osama": "BOWLER",
        "talha": "BOWLER", "hashir": "BATTER", "mashaal": "ALL_ROUNDER",
        "yousuf": "BOWLER", "azen": "ALL_ROUNDER", "moiz": "ALL_ROUNDER",
        "sanawar": "BOWLER", "asad": "BOWLER", "baloch": "BATTER",
        "hassan": "ALL_ROUNDER", "owais": "BATTER", "umar": "BOWLER",
        "anas": "BOWLER", "hassin": "BOWLER",
    }
    FANTASY_PLAYER_TIERS = {
        "ahmad": "G", "qambar": "P", "osama": "G", "talha": "G", "hashir": "S",
        "mashaal": "G", "yousuf": "S", "azen": "P", "moiz": "G", "sanawar": "G",
        "asad": "S", "baloch": "S", "hassan": "P", "owais": "P", "umar": "G",
        "anas": "S", "hassin": "S",
    }
    TIER_TO_FANTASY_CODE = {
        "silver": "S", "gold": "G", "platinum": "P", "s": "S", "g": "G", "p": "P",
    }
    FANTASY_CODE_TO_TIER = {"S": "silver", "G": "gold", "P": "platinum"}

    def __init__(self, db, config_path: str = None):
        self.db = db
        if config_path:
            self.config_path = Path(config_path)
        else:
            self.config_path = Path(db.path).parent / "scorer_config.json"
        self.config_path.parent.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # config
    # ------------------------------------------------------------------
    def load_config(self) -> dict:
        if not self.config_path.exists():
            return dict(self.DEFAULT_CONFIG)
        try:
            payload = json.loads(self.config_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            payload = {}
        config = dict(self.DEFAULT_CONFIG)
        config.update({k: v for k, v in payload.items() if k in self.DEFAULT_CONFIG})
        try:
            config["max_overs"] = max(1, int(config["max_overs"]))
        except (TypeError, ValueError):
            config["max_overs"] = self.DEFAULT_CONFIG["max_overs"]
        return config

    def save_config(self, payload: dict) -> dict:
        config = dict(self.DEFAULT_CONFIG)
        config.update({k: v for k, v in payload.items() if k in self.DEFAULT_CONFIG})
        config["max_overs"] = max(1, int(config.get("max_overs") or self.DEFAULT_CONFIG["max_overs"]))
        config["season_slug"] = (config.get("season_slug") or "").strip().lower()
        self.config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
        return config

    # ------------------------------------------------------------------
    # offline scorer (downloadable HTML)
    # ------------------------------------------------------------------
    def template_source(self) -> str:
        template = Path(__file__).resolve().parent.parent / "templates" / "scorer" / "scorer.html"
        return template.read_text(encoding="utf-8")

    def download_filename(self, config: dict = None) -> str:
        cfg = config or self.load_config()
        version = re.sub(r"[^A-Za-z0-9._-]+", "-", str(cfg.get("version") or "1.0.0")).strip("-")
        return f"scorer-v{version or '1.0.0'}.html"

    def build_scorer_context(self) -> dict:
        """Context for the offline scorer template (config + payload).

        The payload carries live rosters (local ids — the CSV round-trips through
        the import's local->global maps) plus the season's match registry for
        setup-screen prefill.
        """
        config = self.load_config()
        season_slug = (config.get("season_slug") or "").strip().lower()
        with self.db.read() as conn:
            season_slug, teams, matches = self._scorer_payload(conn, season_slug)
            season_row = conn.execute(
                "SELECT name FROM seasons WHERE id = ?", (season_slug,)).fetchone() if season_slug else None
        season_name = (season_row["name"] if season_row else None) or season_slug or config["title"]
        payload = {
            "title": config["title"],
            "version": config["version"],
            "max_overs": config["max_overs"],
            "season": {"slug": season_slug, "name": season_name},
            "teams": teams,
            "matches": matches,
        }
        return {
            "scorer_config": config,
            "scorer_payload": payload,
            "scorer_download_filename": self.download_filename(config),
            "scorer_download_url": "/scorer/download",
        }

    def _scorer_payload(self, conn, season_slug: str):
        """(resolved_season_slug, roster_teams, registry_matches) for the scorer."""
        if not season_slug:
            latest = conn.execute(
                "SELECT season_id FROM teams GROUP BY season_id ORDER BY season_id DESC LIMIT 1"
            ).fetchone()
            season_slug = (latest["season_id"] if latest else "") or ""
        if not season_slug:
            return "", [], []
        rows = conn.execute(
            "SELECT * FROM teams WHERE season_id = ?", (season_slug,)).fetchall()
        if not rows:
            return season_slug, [], []

        player_names = {r["id"]: r["name"] for r in conn.execute(
            "SELECT id, name FROM players WHERE season_id = ?", (season_slug,)).fetchall()}
        gid_to_local = {}
        roster = []
        for t in rows:
            gid = (t["global_team_id"] or "").strip()
            if gid:
                gid_to_local.setdefault(gid, t["id"])
            # Teams without a global id are referenced by their local id in the
            # registry; map that too so prefill resolves either way.
            gid_to_local.setdefault(t["id"], t["id"])
            roster_ids = json_loads(t["players"], []) + json_loads(t["bench"], [])
            seen = set()
            players = []
            for pid in roster_ids:
                if pid in seen or not player_names.get(pid):
                    continue
                seen.add(pid)
                players.append({"id": pid, "name": player_names[pid]})
            # The manager is one of the 4 players but is not in the sold list;
            # include them so they can be selected as a batter/bowler.
            mgr_local = conn.execute(
                "SELECT id FROM players WHERE season_id = ? AND global_player_id = ?",
                (season_slug, t["manager_player_id"])).fetchone()
            mgr_id = (mgr_local["id"] if mgr_local else "")
            if mgr_id and mgr_id not in seen and player_names.get(mgr_id):
                seen.add(mgr_id)
                players.append({"id": mgr_id, "name": player_names[mgr_id]})
            roster.append({
                "id": t["id"],
                "name": t["name"],
                "manager_id": t["manager_player_id"],
                "players": players,
            })

        matches = []
        for r in conn.execute(
                "SELECT match_id, venue, \"between\", team_a_global_id, team_b_global_id "
                "FROM match_registry WHERE season_id = ?", (season_slug,)).fetchall():
            matches.append({
                "match_id": r["match_id"],
                "venue": r["venue"] or "",
                "between": r["between"] or "",
                "team_a_id": gid_to_local.get((r["team_a_global_id"] or "").strip(), ""),
                "team_b_id": gid_to_local.get((r["team_b_global_id"] or "").strip(), ""),
            })
        return season_slug, roster, matches

    # ------------------------------------------------------------------
    # identity maps (local csv ids -> global ids)
    # ------------------------------------------------------------------
    def _identity_maps(self):
        maps = {
            "player_local_to_global": {},
            "team_local_to_global": {},
            "players_by_name": {},
            "teams_by_name": {},
            "player_meta": {},   # global player id -> {name, tier, speciality}
            "team_names": {},    # global team id -> name
        }
        with self.db.read() as conn:
            for p in conn.execute("SELECT * FROM global_players").fetchall():
                maps["player_meta"][p["id"]] = row_to_dict(p)
                maps["players_by_name"][_norm(p["name"])] = p["id"]
            for t in conn.execute("SELECT * FROM teams").fetchall():
                gid = (t["global_team_id"] or "").strip()
                if gid:
                    maps["team_local_to_global"][t["id"]] = gid
                    maps["teams_by_name"].setdefault(_norm(t["name"]), gid)
                    maps["team_names"][gid] = t["name"]
            for p in conn.execute(
                "SELECT id, global_player_id FROM players WHERE global_player_id IS NOT NULL"
            ).fetchall():
                maps["player_local_to_global"][p["id"]] = p["global_player_id"]
        return maps

    def _normalize_player_id(self, raw_id: str, name: str, maps: dict) -> str:
        raw = (raw_id or "").strip()
        if not raw:
            return ""
        if raw in maps["player_local_to_global"]:
            return maps["player_local_to_global"][raw]
        if raw in maps["player_meta"]:
            return raw  # already a global id
        by_name = maps["players_by_name"].get(_norm(name))
        return by_name or raw

    def _normalize_team_id(self, raw_id: str, name: str, maps: dict) -> str:
        raw = (raw_id or "").strip()
        if not raw:
            return ""
        if raw in maps["team_local_to_global"]:
            return maps["team_local_to_global"][raw]
        if raw in maps["team_names"]:
            return raw  # already a global id
        by_name = maps["teams_by_name"].get(_norm(name))
        return by_name or raw

    # ------------------------------------------------------------------
    # registry CRUD
    # ------------------------------------------------------------------
    def list_match_seasons(self) -> list:
        with self.db.read() as conn:
            rows = conn.execute(
                "SELECT season_id, COUNT(*) AS matches FROM match_registry "
                "GROUP BY season_id ORDER BY season_id"
            ).fetchall()
            seasons = []
            for r in rows:
                season = conn.execute("SELECT name FROM seasons WHERE id = ?",
                                      (r["season_id"],)).fetchone()
                seasons.append({"slug": r["season_id"],
                                "name": (season["name"] if season else r["season_id"]),
                                "matches": r["matches"]})
        return seasons

    def list_match_registry(self, season_id: str = "") -> list:
        with self.db.read() as conn:
            if season_id:
                rows = conn.execute(
                    "SELECT * FROM match_registry WHERE season_id = ?", (season_id,)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM match_registry").fetchall()
        rows = [dict(r) for r in rows]
        rows.sort(key=lambda r: _match_number_sort_value(r.get("match_number") or r.get("match_id")))
        return rows

    def get_match_registry_entry(self, season_id: str, match_id: str) -> dict:
        with self.db.read() as conn:
            row = conn.execute(
                "SELECT * FROM match_registry WHERE season_id = ? AND match_id = ?",
                (season_id, match_id)).fetchone()
            return row_to_dict(row)

    def upsert_match_registry_entry(self, season_id: str, match_id: str, match_number: str = "",
                                    match_title: str = "", between: str = "",
                                    venue: str = "", match_date: str = "",
                                    team_a_global_id: str = "", team_b_global_id: str = "",
                                    walkover: bool = False,
                                    walkover_winner_team_id: str = "") -> dict:
        season_id = (season_id or "").strip().lower()
        match_id = (match_id or "").strip()
        if not season_id or not match_id:
            raise ValueError("Season and match id are required")
        with self.db.read() as conn:
            season = conn.execute("SELECT id FROM seasons WHERE id = ?", (season_id,)).fetchone()
            if not season:
                raise ValueError("Season not found")
        key = _match_key(season_id, match_id)
        with self.db.write() as conn:
            existing = conn.execute(
                "SELECT * FROM match_registry WHERE season_id = ? AND match_id = ?",
                (season_id, match_id)).fetchone()
            if existing:
                conn.execute(
                    "UPDATE match_registry SET match_number = ?, match_title = ?, \"between\" = ?, "
                    "venue = ?, match_date = ?, team_a_global_id = ?, team_b_global_id = ?, "
                    "walkover = ?, walkover_winner_team_id = ?, updated_at = ? "
                    "WHERE match_key = ?",
                    (match_number, match_title, between, venue, match_date,
                     team_a_global_id, team_b_global_id, 1 if walkover else 0,
                     walkover_winner_team_id, _now(), key))
            else:
                conn.execute(
                    "INSERT INTO match_registry (match_key, season_id, match_id, match_number, "
                    "match_title, \"between\", venue, match_date, team_a_global_id, team_b_global_id, "
                    "walkover, walkover_winner_team_id, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (key, season_id, match_id, match_number, match_title, between, venue,
                     match_date, team_a_global_id, team_b_global_id, 1 if walkover else 0,
                     walkover_winner_team_id, _now(), _now()))
            if walkover:
                self._upsert_walkover_stats(conn, key, match_id)
        return self.get_match_registry_entry(season_id, match_id)

    def delete_match_registry_entry(self, season_id: str, match_id: str) -> dict:
        key = _match_key(season_id, match_id)
        with self.db.write() as conn:
            for table in ("match_stats", "match_team_stats", "match_player_stats"):
                conn.execute(f"DELETE FROM {table} WHERE match_key = ?", (key,))
            cur = conn.execute("DELETE FROM match_registry WHERE match_key = ?", (key,))
        return {"ok": cur.rowcount > 0}

    # ------------------------------------------------------------------
    # walkover
    # ------------------------------------------------------------------
    def _upsert_walkover_stats(self, conn, match_key: str, match_id: str):
        reg = conn.execute("SELECT * FROM match_registry WHERE match_key = ?",
                           (match_key,)).fetchone()
        if not reg:
            raise ValueError("Match not found")
        winner = (reg["walkover_winner_team_id"] or "").strip()
        team_a = (reg["team_a_global_id"] or "").strip()
        team_b = (reg["team_b_global_id"] or "").strip()
        if not (winner and team_a and team_b):
            raise ValueError("Walkover match requires both teams and a winner")
        if winner not in {team_a, team_b}:
            raise ValueError("Walkover winner must be one of the two teams")

        for table in ("match_stats", "match_team_stats", "match_player_stats"):
            conn.execute(f"DELETE FROM {table} WHERE match_key = ?", (match_key,))

        name_of = {t["id"]: t["name"] for t in conn.execute(
            "SELECT id, name FROM teams").fetchall()}
        # Prefer the registry's stored global ids; fall back to local name lookup.
        team_names = {team_a: reg["between"].split(" vs ")[0].strip() if reg["between"] else team_a,
                      team_b: (reg["between"].split(" vs ")[1].strip()
                               if reg["between"] and " vs " in reg["between"] else team_b)}
        winner_name = team_names.get(winner) or name_of.get(winner) or winner
        loser = team_b if winner == team_a else team_a
        loser_name = team_names.get(loser) or name_of.get(loser) or loser

        conn.execute(
            "INSERT INTO match_stats (match_key, season_id, match_id, result, toss, "
            "winner_team_id, delivery_rows, team_rows, player_rows, source_file, uploaded_by, "
            "uploaded_at, include_in_fantasy_points, delivery_log) VALUES (?, ?, ?, ?, '', ?, 0, 2, 0, "
            "'walkover', 'admin', ?, 0, '[]')",
            (match_key, reg["season_id"], match_id, f"{winner_name} won by walkover",
             winner, _now()))

        for team_id, team_name, result, wins, losses in (
            (winner, winner_name, "win", 1, 0),
            (loser, loser_name, "loss", 0, 1),
        ):
            conn.execute(
                "INSERT INTO match_team_stats (id, match_key, season_id, team_id, team_name, "
                "result, wins, losses, ties, no_results, overs_faced, overs_bowled) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 0, '0.0', '0.0')",
                (secrets.token_hex(8), match_key, reg["season_id"], team_id, team_name,
                 result, wins, losses))

    # ------------------------------------------------------------------
    # CSV import
    # ------------------------------------------------------------------
    def list_recent_imports(self, limit=12) -> list:
        with self.db.read() as conn:
            rows = conn.execute(
                "SELECT * FROM match_stats ORDER BY uploaded_at DESC LIMIT ?",
                (max(1, int(limit or 12)),)).fetchall()
            return rows_to_dicts(rows)

    def _parse_match_csv_rows(self, text: str, file_name: str):
        reader = csv.reader(io.StringIO(text))
        try:
            header = next(reader)
        except StopIteration:
            raise ValueError(f"{file_name}: invalid CSV header")
        if not header:
            raise ValueError(f"{file_name}: invalid CSV header")
        missing = [c for c in self.CSV_REQUIRED_COLUMNS if c not in header]
        if missing:
            raise ValueError(f"{file_name}: missing columns: {', '.join(missing)}")
        width = len(header)

        def to_row(values):
            padded = list(values[:width]) + [""] * max(0, width - len(values))
            return {header[idx]: (padded[idx] or "").strip() for idx in range(width)}

        rows = []
        substitution_ins = set()
        in_sub_log = False
        for values in reader:
            if not values or not any((cell or "").strip() for cell in values):
                continue
            first = (values[0] or "").strip()
            if first == "Substitution Log":
                in_sub_log = True
                continue
            if in_sub_log:
                if first.lower() == "step":
                    continue
                player_in = (values[3] if len(values) > 3 else "").strip()
                if player_in and player_in.lower() != "none":
                    substitution_ins.add(player_in)
                continue
            row = to_row(values)
            if (row.get("Match ID") or "").strip():
                rows.append(row)
        if not rows:
            raise ValueError(f"{file_name}: no delivery rows found")
        return rows, substitution_ins

    def _archive_csv(self, season_id: str, match_id: str, rows: list):
        safe_season = re.sub(r"[^A-Za-z0-9._-]+", "-", season_id).strip(".-_") or "season"
        safe_match = re.sub(r"[^A-Za-z0-9._-]+", "-", match_id).strip(".-_") or "match"
        target = self.config_path.parent / "matches"
        target.mkdir(parents=True, exist_ok=True)
        fieldnames = list(rows[0].keys())
        with (target / f"{safe_season}-{safe_match}.csv").open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                writer.writerow({name: row.get(name, "") for name in fieldnames})

    def import_match_csv(self, file_storage, season_id: str, match_id_override: str = "",
                         venue_override: str = "", match_date: str = "",
                         uploaded_by: str = "admin", confirm_overwrite: bool = False,
                         include_in_fantasy_points: bool = True) -> dict:
        season_id = (season_id or "").strip().lower()
        if not season_id:
            raise ValueError("Season is required")
        file_name = (getattr(file_storage, "filename", "") or "match.csv").strip() or "match.csv"
        payload = file_storage.read()
        if not payload:
            raise ValueError(f"{file_name}: empty file")
        text = payload.decode("utf-8-sig") if isinstance(payload, (bytes, bytearray)) else payload

        rows, substitution_ins = self._parse_match_csv_rows(text, file_name)
        if match_id_override:
            for row in rows:
                row["Match ID"] = match_id_override
        if venue_override:
            for row in rows:
                row["Venue"] = venue_override

        match_id = (match_id_override or rows[0].get("Match ID") or "").strip()
        if not match_id:
            raise ValueError("Match ID is required")

        registry_entry = self.get_match_registry_entry(season_id, match_id)
        if not registry_entry:
            raise ValueError(
                f"Match ID {match_id} is not configured for season {season_id}. "
                "Add it from Admin > Scorer > Manage Matches first.")
        if registry_entry.get("walkover"):
            raise ValueError("Match is declared a walkover; CSV upload is not allowed.")

        with self.db.read() as conn:
            existing = conn.execute(
                "SELECT 1 FROM match_stats WHERE match_key = ?", (_match_key(season_id, match_id),)
            ).fetchone()
        if existing and not confirm_overwrite:
            raise MatchOverwriteConfirmationRequired(season_id, match_id)

        self._archive_csv(season_id, match_id, rows)
        derived = self._derive_match_stats(rows, season_id, file_name, uploaded_by,
                                           match_date, include_in_fantasy_points,
                                           substitution_ins)
        self._persist_match_stats(derived)
        return derived

    def undo_imported_match(self, match_key: str) -> dict:
        key = (match_key or "").strip()
        with self.db.write() as conn:
            removed = 0
            for table in ("match_stats", "match_team_stats", "match_player_stats"):
                cur = conn.execute(f"DELETE FROM {table} WHERE match_key = ?", (key,))
                removed += cur.rowcount
        return {"ok": True, "match_key": key, "removed_rows": removed}

    # ------------------------------------------------------------------
    # derivation (ball-by-ball -> team/player rows + fantasy)
    # ------------------------------------------------------------------
    def _batter_order_values(self, rows: list, maps: dict) -> dict:
        """player_id -> call-up order (1, 2, ...) per the scorer's 'Batter Order'.

        The scorer assigns orders at innings start (striker 1, non-striker 2) and
        to each new batter; the CSV carries the striker's order per ball, so the
        minimum per player is their call-up order. The innings-start non-striker
        who never faces a ball is fixed to order 2 from the first row of each
        innings. Players without any order data fall back to first-appearance
        order (after the known orders).
        """
        orders = {}
        appearance = {}
        first_row_by_innings = {}
        for idx, row in enumerate(rows):
            inn = str(row.get("Innings Order") or "1").strip() or "1"
            first_row_by_innings.setdefault(inn, row)
            batter_id = self._normalize_player_id(
                row.get("Batter ID"), row.get("Batter"), maps)
            if batter_id:
                appearance.setdefault(batter_id, idx)
                raw = str(row.get("Batter Order") or "").strip()
                if raw.isdigit():
                    order = int(raw)
                    if batter_id not in orders or order < orders[batter_id]:
                        orders[batter_id] = order
        for first_row in first_row_by_innings.values():
            # Openers without any order data: striker 1, non-striker 2.
            striker_id = self._normalize_player_id(
                first_row.get("Batter ID"), first_row.get("Batter"), maps)
            if striker_id and striker_id not in orders:
                orders[striker_id] = 1
            ns_id = self._normalize_player_id(
                first_row.get("Non Strike Batter ID"),
                first_row.get("Non Strike Batter"), maps)
            if ns_id:
                if ns_id not in orders or orders[ns_id] > 2:
                    orders[ns_id] = 2
        max_known = max(orders.values()) if orders else 0
        result = {}
        for player_id in set(appearance) | set(orders):
            if player_id in orders:
                result[player_id] = orders[player_id]
            elif player_id in appearance:
                # Never faced (or old CSV without the column): after the known
                # orders, in first-appearance order.
                result[player_id] = max_known + 1 + appearance[player_id]
        return result

    def _derive_match_stats(self, rows, season_id: str, source_file: str, uploaded_by: str,
                            match_date: str = "", include_in_fantasy_points: bool = True,
                            substitution_ins=None):
        first = rows[0]
        match_id = (first.get("Match ID") or "").strip() or "unknown"
        match_name = (first.get("Match") or "").strip()
        venue = (first.get("Venue") or "").strip()
        match_result = (first.get("Match Result") or "").strip()
        match_toss = (first.get("Match Toss") or "").strip()
        key = _match_key(season_id, match_id)
        maps = self._identity_maps()
        player_meta = maps["player_meta"]
        batter_orders = self._batter_order_values(rows, maps)

        team_rows = {}
        player_rows = {}

        def ensure_team(raw_id, raw_name):
            team_id = self._normalize_team_id(raw_id, raw_name, maps)
            team_name = maps["team_names"].get(team_id) or (raw_name or "").strip() or team_id
            if team_id not in team_rows:
                team_rows[team_id] = {
                    "team_id": team_id, "team_name": team_name, "runs_scored": 0,
                    "balls_faced": 0, "wickets_lost": 0, "fours": 0, "sixes": 0,
                    "wides_faced": 0, "noballs_faced": 0, "runs_conceded": 0,
                    "balls_bowled": 0, "wickets_taken": 0, "wides_bowled": 0,
                    "noballs_bowled": 0, "fantasy_points": 0,
                }
            return team_rows[team_id]

        def ensure_player(raw_id, raw_name, team_id, team_name, role_hint):
            player_id = self._normalize_player_id(raw_id, raw_name, maps)
            if not player_id:
                return None
            meta = player_meta.get(player_id, {})
            pname = (raw_name or "").strip() or meta.get("name") or player_id
            tier = (meta.get("tier") or "").strip().lower()
            role = _speciality_to_role(meta.get("speciality") or role_hint)
            if not tier:
                tier = self.FANTASY_CODE_TO_TIER.get(
                    self._tier_to_fantasy_code("", pname), "gold")
            if player_id not in player_rows:
                player_rows[player_id] = {
                    "player_id": player_id, "player_name": pname, "team_id": team_id,
                    "team_name": (team_name or "").strip(), "role": role, "tier": tier,
                    "matches": 1, "innings_batted": 0, "not_out": 0, "dismissed": 0,
                    "runs": 0, "balls_faced": 0, "fours": 0, "sixes": 0,
                    "innings_bowled": 0, "balls_bowled": 0, "runs_conceded": 0,
                    "wickets": 0, "wides": 0, "noballs": 0, "strike_rate": 0.0,
                    "economy": 0.0, "fantasy_score": 0, "fantasy_bat_points": 0.0,
                    "fantasy_bowl_points": 0.0,
                }
            return player_rows[player_id]

        for row in rows:
            bat_team_id = (row.get("Batting Team ID") or "").strip()
            bowl_team_id = (row.get("Bowling Team ID") or "").strip()
            bat_team = ensure_team(bat_team_id, row.get("Batting Team") or "")
            bowl_team = ensure_team(bowl_team_id, row.get("Bowling Team") or "")

            batter = ensure_player(row.get("Batter ID"), row.get("Batter"),
                                   bat_team["team_id"], bat_team["team_name"], "BATTER")
            non_striker = ensure_player(row.get("Non Strike Batter ID"),
                                        row.get("Non Strike Batter"),
                                        bat_team["team_id"], bat_team["team_name"], "BATTER")
            bowler = ensure_player(row.get("Bowler ID"), row.get("Bowler"),
                                   bowl_team["team_id"], bowl_team["team_name"], "BOWLER")

            if batter:
                batter["innings_batted"] = 1
            if non_striker:
                non_striker["innings_batted"] = 1
            if bowler:
                bowler["innings_bowled"] = 1

            runs_bat = _safe_int(row.get("Runs Bat"))
            runs_extra = _safe_int(row.get("Runs Extra"))
            total = runs_bat + runs_extra
            extras_type = (row.get("Extras Type") or "None").strip() or "None"
            valid_ball = (row.get("Valid Ball?") or "").strip() == "Yes"
            is_wicket = bool((row.get("Dismissed Batter") or "").strip()
                             and (row.get("Dismissed Batter") or "").strip() != "None")

            bat_team["runs_scored"] += total
            if valid_ball:
                bat_team["balls_faced"] += 1
            if extras_type == "Wide":
                bat_team["wides_faced"] += runs_extra
            elif extras_type == "No Ball":
                bat_team["noballs_faced"] += runs_extra
            if is_wicket:
                bat_team["wickets_lost"] += 1

            bowl_team["runs_conceded"] += total
            if valid_ball:
                bowl_team["balls_bowled"] += 1
            if extras_type == "Wide":
                bowl_team["wides_bowled"] += runs_extra
            elif extras_type == "No Ball":
                bowl_team["noballs_bowled"] += runs_extra
            if is_wicket:
                bowl_team["wickets_taken"] += 1

            if batter:
                if extras_type != "Wide":
                    batter["balls_faced"] += 1
                batter["runs"] += runs_bat
                if runs_bat == 4:
                    batter["fours"] += 1
                    bat_team["fours"] += 1
                elif runs_bat == 6:
                    batter["sixes"] += 1
                    bat_team["sixes"] += 1

            if bowler:
                if valid_ball:
                    bowler["balls_bowled"] += 1
                bowler["runs_conceded"] += total
                if extras_type == "Wide":
                    bowler["wides"] += runs_extra
                elif extras_type == "No Ball":
                    bowler["noballs"] += runs_extra
                if is_wicket:
                    bowler["wickets"] += 1

            if is_wicket and (row.get("Dismissed Batter ID") or "").strip():
                dismissed = ensure_player(row.get("Dismissed Batter ID"),
                                          row.get("Dismissed Batter"),
                                          bat_team["team_id"], bat_team["team_name"], "BATTER")
                if dismissed:
                    dismissed["innings_batted"] = 1
                    dismissed["dismissed"] = 1

        fantasy = self._calculate_fantasy_scores(rows, player_rows, substitution_ins)
        for player_id, f in fantasy.items():
            if player_id not in player_rows:
                continue
            score = _round_nearest_int(_safe_float(f.get("score")) + self.FANTASY_MATCH_BONUS_POINTS)
            if f.get("is_substitute"):
                score = 0
            player_rows[player_id]["fantasy_score"] = score
            player_rows[player_id]["fantasy_bat_points"] = f.get("bat_pts", 0.0)
            player_rows[player_id]["fantasy_bowl_points"] = f.get("bowl_pts", 0.0)
            team_id = player_rows[player_id]["team_id"]
            if team_id in team_rows:
                team_rows[team_id]["fantasy_points"] += score

        for p in player_rows.values():
            if p["innings_batted"] and not p["dismissed"]:
                p["not_out"] = 1
            if p["balls_faced"] > 0:
                p["strike_rate"] = round(p["runs"] * 100.0 / p["balls_faced"], 2)
            if p["balls_bowled"] > 0:
                p["economy"] = round(p["runs_conceded"] * 6.0 / p["balls_bowled"], 2)
            p["batter_order"] = batter_orders.get(p["player_id"])

        outcome, winner_id = self._build_match_outcome(
            {tid: t["team_name"] for tid, t in team_rows.items()}, match_result)
        for tid, team in team_rows.items():
            result = outcome.get(tid, "no_result")
            team.update({
                "result": result,
                "wins": 1 if result == "win" else 0,
                "losses": 1 if result == "loss" else 0,
                "ties": 1 if result == "tie" else 0,
                "no_results": 1 if result == "no_result" else 0,
                "overs_faced": _overs_string(team["balls_faced"]),
                "overs_bowled": _overs_string(team["balls_bowled"]),
                "run_rate_for": round(team["runs_scored"] * 6.0 / team["balls_faced"], 2)
                if team["balls_faced"] else 0.0,
                "run_rate_against": round(team["runs_conceded"] * 6.0 / team["balls_bowled"], 2)
                if team["balls_bowled"] else 0.0,
            })

        match_row = {
            "match_key": key, "season_id": season_id, "match_id": match_id,
            "result": match_result, "toss": match_toss, "winner_team_id": winner_id,
            "delivery_rows": len(rows), "team_rows": len(team_rows),
            "player_rows": len(player_rows), "source_file": source_file,
            "uploaded_by": uploaded_by, "uploaded_at": _now(),
            "include_in_fantasy_points": 1 if include_in_fantasy_points else 0,
        }
        return {"match_key": key, "match_row": match_row,
                "team_rows": list(team_rows.values()),
                "player_rows": list(player_rows.values()),
                "delivery_log": json_dumps(rows)}

    def _build_match_outcome(self, team_name_by_id: dict, match_result: str):
        normalized = _norm(match_result)
        outcome = {tid: "no_result" for tid in team_name_by_id}
        winner_id = ""
        if "won" in normalized:
            for tid, tname in team_name_by_id.items():
                if _norm(tname) and _norm(tname) in normalized:
                    winner_id = tid
                    break
            if winner_id:
                for tid in outcome:
                    outcome[tid] = "win" if tid == winner_id else "loss"
        elif "tied" in normalized:
            for tid in outcome:
                outcome[tid] = "tie"
        return outcome, winner_id

    def _persist_match_stats(self, derived: dict):
        key = derived["match_key"]
        with self.db.write() as conn:
            for table in ("match_stats", "match_team_stats", "match_player_stats"):
                conn.execute(f"DELETE FROM {table} WHERE match_key = ?", (key,))
            m = derived["match_row"]
            conn.execute(
                "INSERT INTO match_stats (match_key, season_id, match_id, result, toss, "
                "winner_team_id, delivery_rows, team_rows, player_rows, source_file, "
                "uploaded_by, uploaded_at, include_in_fantasy_points, delivery_log) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (m["match_key"], m["season_id"], m["match_id"], m["result"], m["toss"],
                 m["winner_team_id"], m["delivery_rows"], m["team_rows"], m["player_rows"],
                 m["source_file"], m["uploaded_by"], m["uploaded_at"],
                 m["include_in_fantasy_points"], derived.get("delivery_log") or "[]"))
            for t in derived["team_rows"]:
                conn.execute(
                    "INSERT INTO match_team_stats (id, match_key, season_id, team_id, team_name, "
                    "runs_scored, balls_faced, wickets_lost, fours, sixes, wides_faced, "
                    "noballs_faced, runs_conceded, balls_bowled, wickets_taken, wides_bowled, "
                    "noballs_bowled, overs_faced, overs_bowled, run_rate_for, run_rate_against, "
                    "result, wins, losses, ties, no_results, fantasy_points) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (secrets.token_hex(8), key, m["season_id"], t["team_id"], t["team_name"],
                     t["runs_scored"], t["balls_faced"], t["wickets_lost"], t["fours"],
                     t["sixes"], t["wides_faced"], t["noballs_faced"], t["runs_conceded"],
                     t["balls_bowled"], t["wickets_taken"], t["wides_bowled"], t["noballs_bowled"],
                     t["overs_faced"], t["overs_bowled"], t["run_rate_for"], t["run_rate_against"],
                     t["result"], t["wins"], t["losses"], t["ties"], t["no_results"],
                     t["fantasy_points"]))
            for p in derived["player_rows"]:
                conn.execute(
                    "INSERT INTO match_player_stats (id, match_key, season_id, player_id, "
                    "player_name, team_id, team_name, role, tier, matches, innings_batted, "
                    "not_out, dismissed, runs, balls_faced, fours, sixes, innings_bowled, "
                    "balls_bowled, runs_conceded, wickets, wides, noballs, strike_rate, economy, "
                    "fantasy_score, fantasy_bat_points, fantasy_bowl_points, batter_order) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (secrets.token_hex(8), key, m["season_id"], p["player_id"], p["player_name"],
                     p["team_id"], p["team_name"], p["role"], p["tier"], p["matches"],
                     p["innings_batted"], p["not_out"], p["dismissed"], p["runs"],
                     p["balls_faced"], p["fours"], p["sixes"], p["innings_bowled"],
                     p["balls_bowled"], p["runs_conceded"], p["wickets"], p["wides"],
                     p["noballs"], p["strike_rate"], p["economy"], p["fantasy_score"],
                     p["fantasy_bat_points"], p["fantasy_bowl_points"], p["batter_order"]))

    # ------------------------------------------------------------------
    # fantasy
    # ------------------------------------------------------------------
    def _tier_to_fantasy_code(self, tier: str, fallback_name: str = ""):
        code = self.TIER_TO_FANTASY_CODE.get((tier or "").strip().lower())
        if code:
            return code
        return self.FANTASY_PLAYER_TIERS.get(_norm(fallback_name), "G")

    def _fantasy_role_for_player(self, player_id, player_name, player_rows):
        meta = player_rows.get(player_id, {})
        role = _speciality_to_role(meta.get("role"))
        if role != "ALL_ROUNDER":
            return role
        return _speciality_to_role(self.FANTASY_PLAYER_ROLES.get(_norm(player_name), "ALL_ROUNDER"))

    def _fantasy_matchup_multiplier(self, bat_tier, bowl_tier, role, base_points):
        bat_val = self.FANTASY_TIERS[bat_tier]["value"]
        bowl_val = self.FANTASY_TIERS[bowl_tier]["value"]
        diff = abs(bat_val - bowl_val)
        if diff == 0:
            return 1.0
        upset_mult = 1.15 if diff == 1 else 1.65
        expected_mult = 1.0 / upset_mult
        is_positive = base_points > 0
        batter_won = is_positive if role == "BATTER" else not is_positive
        if batter_won:
            return upset_mult if bat_val < bowl_val else expected_mult
        return upset_mult if bowl_val < bat_val else expected_mult

    def _calculate_fantasy_scores(self, rows, player_rows, substitution_ins=None):
        players = {}
        substitute = set()
        observed = {}
        for row in rows:
            for name_key, id_key in (("Batter", "Batter ID"), ("Bowler", "Bowler ID"),
                                     ("Dismissed Batter", "Dismissed Batter ID")):
                name = (row.get(name_key) or "").strip()
                pid = (row.get(id_key) or "").strip()
                if name and pid and name != "None":
                    observed[_norm(name)] = pid

        for row in rows:
            details = (row.get("Substitution Details") or "").strip()
            if not details or details == "None":
                continue
            for entry in details.split("|"):
                if "->" not in entry:
                    continue
                incoming_name = entry.split("->", 1)[1].split("(", 1)[0].strip()
                incoming_id = observed.get(_norm(incoming_name), "")
                if incoming_id:
                    substitute.add(incoming_id)
        for incoming_name in (substitution_ins or set()):
            incoming_id = observed.get(_norm(incoming_name), "")
            if incoming_id:
                substitute.add(incoming_id)

        for row in rows:
            batter_id = (row.get("Batter ID") or "").strip()
            bowler_id = (row.get("Bowler ID") or "").strip()
            dismissed_id = (row.get("Dismissed Batter ID") or "").strip()
            dismissed_name = (row.get("Dismissed Batter") or "").strip()
            runs_bat = _safe_int(row.get("Runs Bat"))
            runs_extra = _safe_int(row.get("Runs Extra"))
            extras_type = (row.get("Extras Type") or "None").strip() or "None"
            is_wicket = bool(dismissed_id and dismissed_name != "None")
            is_striker_out = dismissed_id == batter_id
            is_valid_ball = (row.get("Valid Ball?") or "").strip() == "Yes"
            is_wide = extras_type == "Wide"
            is_no_ball = extras_type == "No Ball"

            init_ids = [pid for pid in (batter_id, bowler_id) if pid]
            if is_wicket and dismissed_id and dismissed_id not in init_ids:
                init_ids.append(dismissed_id)

            for pid in init_ids:
                if pid in players:
                    continue
                meta = player_rows.get(pid, {})
                players[pid] = {
                    "role": self._fantasy_role_for_player(pid, meta.get("player_name") or "", player_rows),
                    "batting_points": 0.0, "bowling_points": 0.0,
                    "balls_faced": 0, "balls_bowled": 0,
                    "team": (row.get("Batting Team ID") or "").strip()
                    if pid == batter_id else (row.get("Bowling Team ID") or "").strip(),
                }

            if not batter_id or not bowler_id:
                continue

            bat_tier = self._tier_to_fantasy_code(
                player_rows.get(batter_id, {}).get("tier", ""), player_rows.get(batter_id, {}).get("player_name", ""))
            bowl_tier = self._tier_to_fantasy_code(
                player_rows.get(bowler_id, {}).get("tier", ""), player_rows.get(bowler_id, {}).get("player_name", ""))

            if not is_wide:
                players[batter_id]["balls_faced"] += 1
                bat_role = players[batter_id]["role"]
                bat_base = self.FANTASY_BAT_POINTS["OUT"] if is_striker_out \
                    else self.FANTASY_BAT_POINTS.get(runs_bat, 0)
                bat_role_mult = 1.2 if bat_role == "BOWLER" else 1.0
                matchup = self._fantasy_matchup_multiplier(bat_tier, bowl_tier, "BATTER", bat_base)
                mult = self.FANTASY_TIERS[bat_tier]["reward"] if bat_base >= 0 \
                    else self.FANTASY_TIERS[bat_tier]["penalty"]
                players[batter_id]["batting_points"] += bat_base * mult * matchup * bat_role_mult

            if is_wicket and dismissed_id and not is_striker_out and dismissed_id in players:
                ns_tier = self._tier_to_fantasy_code(
                    player_rows.get(dismissed_id, {}).get("tier", ""),
                    player_rows.get(dismissed_id, {}).get("player_name", ""))
                ns_matchup = self._fantasy_matchup_multiplier(ns_tier, bowl_tier, "BATTER",
                                                              self.FANTASY_BAT_POINTS["OUT"])
                players[dismissed_id]["batting_points"] += (
                    self.FANTASY_BAT_POINTS["OUT"] * self.FANTASY_TIERS[ns_tier]["penalty"]
                    * ns_matchup)

            players[bowler_id]["balls_bowled"] += 1
            bowl_role = players[bowler_id]["role"]
            bowl_base = 0
            if is_wicket:
                bowl_base = self.FANTASY_BOWL_POINTS["WICKET"]
            elif is_valid_ball:
                bowl_base = self.FANTASY_BOWL_POINTS.get(runs_bat, 0)
            elif is_wide:
                bowl_base = -1.5
                if runs_extra > 1:
                    extra = runs_extra - 1
                    if extra in self.FANTASY_BOWL_POINTS and self.FANTASY_BOWL_POINTS[extra] < 0:
                        bowl_base += self.FANTASY_BOWL_POINTS[extra]
            elif is_no_ball:
                bowl_base = -2.5
                if runs_bat > 0 and self.FANTASY_BOWL_POINTS.get(runs_bat, 0) < 0:
                    bowl_base += self.FANTASY_BOWL_POINTS[runs_bat]

            bowl_role_mult = 1.2 if bowl_role == "BATTER" else 1.0
            bowl_matchup = self._fantasy_matchup_multiplier(bat_tier, bowl_tier, "BOWLER", bowl_base)
            mult = self.FANTASY_TIERS[bowl_tier]["reward"] if bowl_base >= 0 \
                else self.FANTASY_TIERS[bowl_tier]["penalty"]
            players[bowler_id]["bowling_points"] += bowl_base * mult * bowl_matchup * bowl_role_mult

        results = {}
        for pid, stats in players.items():
            if pid in substitute:
                results[pid] = {"score": 0.0, "bat_pts": 0.0, "bowl_pts": 0.0,
                                "is_substitute": True, "role": stats["role"]}
                continue
            total_balls = stats["balls_faced"] + stats["balls_bowled"]
            if total_balls == 0 and stats["batting_points"] == 0.0 and stats["bowling_points"] == 0.0:
                continue
            results[pid] = {"score": stats["batting_points"] + stats["bowling_points"],
                            "bat_pts": stats["batting_points"], "bowl_pts": stats["bowling_points"],
                            "is_substitute": False, "role": stats["role"]}
        return results

    # ------------------------------------------------------------------
    # league table, leaderboards, summaries, profiles
    # ------------------------------------------------------------------
    def league_table(self, season_id: str) -> list:
        season_id = (season_id or "").strip().lower()
        if not season_id:
            return []
        with self.db.read() as conn:
            rows = conn.execute(
                "SELECT * FROM match_team_stats WHERE season_id = ?", (season_id,)).fetchall()
            team_names = {}
            for t in conn.execute("SELECT * FROM teams WHERE season_id = ?", (season_id,)).fetchall():
                gid = (t["global_team_id"] or "").strip() or t["id"]
                # Stats rows may reference either the per-season team id or the
                # global id (imported S1 stats use the global id) — map both.
                team_names.setdefault(gid, t["name"])
                team_names.setdefault(t["id"], t["name"])
        agg = {}
        for r in rows:
            tid = r["team_id"]
            entry = agg.setdefault(tid, {
                "team_id": tid, "team_name": team_names.get(tid, r["team_name"]),
                "played": 0, "wins": 0, "draws": 0, "losses": 0, "no_results": 0,
                "points": 0, "runs_for": 0, "balls_for": 0, "runs_against": 0,
                "balls_against": 0, "boundaries": 0,
            })
            wins = _safe_int(r["wins"]); losses = _safe_int(r["losses"])
            ties = _safe_int(r["ties"]); nrs = _safe_int(r["no_results"])
            if wins == losses == ties == nrs == 0:
                result = (r["result"] or "").lower()
                if result == "win":
                    wins = 1
                elif result == "loss":
                    losses = 1
                elif result in ("tie", "draw"):
                    ties = 1
                else:
                    nrs = 1
            entry["played"] += wins + losses + ties + nrs
            entry["wins"] += wins; entry["losses"] += losses
            entry["draws"] += ties; entry["no_results"] += nrs
            entry["points"] += wins * 2 + ties
            entry["runs_for"] += _safe_int(r["runs_scored"])
            entry["balls_for"] += _safe_int(r["balls_faced"])
            entry["runs_against"] += _safe_int(r["runs_conceded"])
            entry["balls_against"] += _safe_int(r["balls_bowled"])
            entry["boundaries"] += _safe_int(r["fours"]) + _safe_int(r["sixes"])

        standings = list(agg.values())
        for e in standings:
            # Round each rate to 2dp before subtracting (matches the old app's
            # published S1 NRR figures; keeps tie-break ordering stable).
            e["run_rate_for"] = round((e["runs_for"] * 6.0 / e["balls_for"]) if e["balls_for"] else 0.0, 2)
            e["run_rate_against"] = round((e["runs_against"] * 6.0 / e["balls_against"]) if e["balls_against"] else 0.0, 2)
            e["nrr"] = round(e["run_rate_for"] - e["run_rate_against"], 6)
            e["nrr_display"] = f"{e['nrr']:.2f}"

        # Base order: points desc, NRR desc.
        standings.sort(key=lambda e: (-e["points"], -e["nrr"], e["team_name"].lower()))

        # S2 tie-breakers among teams level on points AND NRR: head-to-head, then boundaries.
        h2h = self._head_to_head(season_id)

        def tie_compare(a, b):
            aw = h2h.get((a["team_id"], b["team_id"]), 0)
            bw = h2h.get((b["team_id"], a["team_id"]), 0)
            if aw != bw:
                return -1 if aw > bw else 1
            if a["boundaries"] != b["boundaries"]:
                return -1 if a["boundaries"] > b["boundaries"] else 1
            return -1 if a["team_name"].lower() < b["team_name"].lower() else 1

        def reorder_group(group):
            if len(group) < 2:
                return group
            return sorted(group, key=cmp_to_key(tie_compare))

        final = []
        i = 0
        while i < len(standings):
            j = i
            while (j + 1 < len(standings)
                   and standings[j]["points"] == standings[j + 1]["points"]
                   and standings[j]["nrr"] == standings[j + 1]["nrr"]):
                j += 1
            final.extend(reorder_group(standings[i:j + 1]))
            i = j + 1
        for idx, item in enumerate(final, start=1):
            item["rank"] = idx
            item["slug"] = team_profile_slug(item["team_id"], item["team_name"])
        return final

    def _head_to_head(self, season_id: str) -> dict:
        """{(team_a, team_b): wins_for_a} over matches where both played."""
        with self.db.read() as conn:
            rows = conn.execute(
                "SELECT match_key, team_id, result, wins, losses FROM match_team_stats "
                "WHERE season_id = ?", (season_id,)).fetchall()
        by_match = defaultdict(list)
        for r in rows:
            by_match[r["match_key"]].append(r)
        h2h = defaultdict(int)
        for match_rows in by_match.values():
            if len(match_rows) < 2:
                continue
            for i in range(len(match_rows)):
                for j in range(i + 1, len(match_rows)):
                    a, b = match_rows[i], match_rows[j]
                    if _safe_int(a["wins"]) > _safe_int(b["wins"]):
                        h2h[(a["team_id"], b["team_id"])] += 1
                    elif _safe_int(b["wins"]) > _safe_int(a["wins"]):
                        h2h[(b["team_id"], a["team_id"])] += 1
        return dict(h2h)

    def leaderboards(self, season_id: str = "", top_n: int = 5) -> dict:
        top_n = max(1, int(top_n or 5))
        with self.db.read() as conn:
            if season_id:
                player_rows = conn.execute(
                    "SELECT * FROM match_player_stats WHERE season_id = ?", (season_id,)).fetchall()
                team_rows = conn.execute(
                    "SELECT * FROM match_team_stats WHERE season_id = ?", (season_id,)).fetchall()
            else:
                player_rows = conn.execute("SELECT * FROM match_player_stats").fetchall()
                team_rows = conn.execute("SELECT * FROM match_team_stats").fetchall()

        player_agg = {}
        for r in player_rows:
            pid = r["player_id"]
            p = player_agg.setdefault(pid, {
                "player_id": pid, "player_name": r["player_name"], "matches": 0,
                "runs": 0, "balls_faced": 0, "fours": 0, "sixes": 0, "dismissed": 0,
                "wickets": 0, "balls_bowled": 0, "runs_conceded": 0,
                "fantasy_score": 0, "innings_batted": 0,
            })
            p["matches"] += 1
            p["runs"] += _safe_int(r["runs"])
            p["balls_faced"] += _safe_int(r["balls_faced"])
            p["fours"] += _safe_int(r["fours"])
            p["sixes"] += _safe_int(r["sixes"])
            p["dismissed"] += _safe_int(r["dismissed"])
            p["wickets"] += _safe_int(r["wickets"])
            p["balls_bowled"] += _safe_int(r["balls_bowled"])
            p["runs_conceded"] += _safe_int(r["runs_conceded"])
            p["fantasy_score"] += _safe_int(r["fantasy_score"])
            p["innings_batted"] += 1

        players = list(player_agg.values())
        for p in players:
            p["strike_rate"] = round(p["runs"] * 100.0 / p["balls_faced"], 2) if p["balls_faced"] else 0.0
            p["economy"] = round(p["runs_conceded"] * 6.0 / p["balls_bowled"], 2) if p["balls_bowled"] else 0.0
            p["batting_average"] = round(p["runs"] / p["dismissed"], 2) if p["dismissed"] else 0.0

        team_agg = {}
        for r in team_rows:
            tid = r["team_id"]
            t = team_agg.setdefault(tid, {
                "team_id": tid, "team_name": r["team_name"], "matches": 0,
                "runs": 0, "wickets": 0, "fantasy_points": 0,
            })
            t["matches"] += 1
            t["runs"] += _safe_int(r["runs_scored"])
            t["wickets"] += _safe_int(r["wickets_taken"])
            t["fantasy_points"] += _safe_int(r["fantasy_points"])
        teams = list(team_agg.values())

        def top(items, key, reverse=True, predicate=None):
            filtered = [i for i in items if (predicate(i) if predicate else True)]
            filtered.sort(key=key, reverse=reverse)
            return filtered[:top_n]

        def slug(p):
            return player_profile_slug(p["player_id"], p["player_name"])

        return {
            "batters": [{"rank": i + 1, **p, "slug": slug(p)}
                        for i, p in enumerate(top(players, lambda p: (p["runs"], p["strike_rate"])))],
            "bowlers": [{"rank": i + 1, **p, "slug": slug(p)}
                        for i, p in enumerate(top(players, lambda p: (p["wickets"], -p["economy"]),
                                                  predicate=lambda p: p["wickets"] > 0))],
            "fantasy": [{"rank": i + 1, **p, "slug": slug(p)}
                        for i, p in enumerate(top(players, lambda p: p["fantasy_score"]))],
            "strike_rates": [{"rank": i + 1, **p, "slug": slug(p)}
                             for i, p in enumerate(top(players, lambda p: p["strike_rate"],
                                                       predicate=lambda p: p["balls_faced"] >= 6))],
            "economies": [{"rank": i + 1, **p, "slug": slug(p)}
                          for i, p in enumerate(top(players, lambda p: -p["economy"],
                                                    predicate=lambda p: p["balls_bowled"] >= 6))],
            "teams_points": [{"rank": i + 1, **t} for i, t in enumerate(
                top(teams, lambda t: (t["matches"] and t["fantasy_points"],)))],
            "teams_fantasy": [{"rank": i + 1, **t} for i, t in enumerate(
                top(teams, lambda t: t["fantasy_points"]))],
        }

    def match_summary(self, season_id: str, match_id: str) -> dict:
        season_id = (season_id or "").strip().lower()
        match_id = (match_id or "").strip()
        key = _match_key(season_id, match_id)
        with self.db.read() as conn:
            registry = conn.execute(
                "SELECT * FROM match_registry WHERE match_key = ?", (key,)).fetchone()
            match_row = conn.execute(
                "SELECT * FROM match_stats WHERE match_key = ?", (key,)).fetchone()
            team_rows = conn.execute(
                "SELECT * FROM match_team_stats WHERE match_key = ?", (key,)).fetchall()
            player_rows = conn.execute(
                "SELECT * FROM match_player_stats WHERE match_key = ?", (key,)).fetchall()
        if not registry and not match_row:
            return None
        registry = row_to_dict(registry) if registry else {}
        match_row = row_to_dict(match_row) if match_row else {}
        team_rows = rows_to_dicts(team_rows)
        player_rows = rows_to_dicts(player_rows)

        sections = []
        for team in team_rows:
            tid = team["team_id"]
            batting = [p for p in player_rows
                       if p["team_id"] == tid and _safe_int(p["innings_batted"]) > 0]
            # Show batsmen in call-up order (batter_order from the scorer CSV),
            # not by runs; players without an order go last, runs as tiebreak.
            batting.sort(key=lambda p: (
                0 if p.get("batter_order") is not None else 1,
                p.get("batter_order") if p.get("batter_order") is not None else 10 ** 9,
                -_safe_int(p["runs"]),
                p["player_name"].lower()))
            for b in batting:
                b["status"] = "out" if _safe_int(b["dismissed"]) > 0 else "not out"
                b["sr_display"] = (f"{_safe_float(b['strike_rate']):.1f}"
                                   if _safe_int(b["balls_faced"]) > 0 else "-")
            bowling = [p for p in player_rows
                       if p["team_id"] != tid and _safe_int(p["balls_bowled"]) > 0]
            bowling.sort(key=lambda p: (_safe_int(p["wickets"]), _safe_int(p["balls_bowled"]),
                                        -_safe_float(p["economy"])), reverse=True)
            for b in bowling:
                b["overs_display"] = _overs_string(_safe_int(b["balls_bowled"]))
                b["econ_display"] = (f"{_safe_float(b['economy']):.2f}"
                                     if _safe_int(b["balls_bowled"]) > 0 else "-")
            extras = _safe_int(team["wides_faced"]) + _safe_int(team["noballs_faced"])
            sections.append({
                "team": team,
                "team_id": tid,
                "team_name": team["team_name"],
                "extras": extras,
                "total": (f"{_safe_int(team['runs_scored'])}/{_safe_int(team['wickets_lost'])} "
                          f"({team.get('overs_faced') or _overs_string(_safe_int(team['balls_faced']))} Ov)"),
                "batting": batting,
                "bowling": bowling,
            })

        # Fall of wickets, derived from the stored ball-by-ball log (S2+; S1 has none).
        fow = {}
        delivery_log = json_loads(match_row.get("delivery_log"), []) if match_row else []
        if delivery_log:
            fow = {}
            wkt = {}
            for row in delivery_log:
                dismissed = str(row.get("Dismissed Batter") or "").strip()
                if not dismissed or dismissed == "None":
                    continue
                team = str(row.get("Batting Team") or "").strip() or "?"
                wkt[team] = wkt.get(team, 0) + 1
                fow.setdefault(team, []).append(
                    f"{row.get('Progressive Runs')}-{wkt[team]} "
                    f"({dismissed}, {row.get('Over Number')}.{row.get('Ball Number')})")
        for sec in sections:
            sec["fow"] = fow.get(sec["team_name"], [])

        fantasy = sorted(player_rows,
                         key=lambda p: (_safe_int(p["fantasy_score"]), _safe_int(p["runs"]),
                                        _safe_int(p["wickets"])), reverse=True)
        winner_name = ""
        if match_row.get("winner_team_id"):
            winner_name = next((t["team_name"] for t in team_rows
                                if t["team_id"] == match_row["winner_team_id"]), "")
        return {
            "season_id": season_id, "match_id": match_id, "match_key": key,
            "between": registry.get("between") or match_row.get("result") or "",
            "match_number": registry.get("match_number") or "",
            "match_title": registry.get("match_title") or match_row.get("result") or "",
            "venue": registry.get("venue") or match_row.get("") or "",
            "match_date": registry.get("match_date") or "",
            "walkover": bool(registry.get("walkover") or (match_row.get("source_file") == "walkover")),
            "result": match_row.get("result") or "",
            "toss": match_row.get("toss") or "",
            "winner_team_id": match_row.get("winner_team_id") or "",
            "winner_name": winner_name,
            "has_uploaded_data": bool(match_row),
            "team_sections": sections,
            "fantasy_leaderboard": fantasy,
            "registry": registry,
            "delivery_log": delivery_log,
        }

    def ball_by_ball(self, season_id: str, match_id: str) -> dict:
        """Play-by-play view of a match, derived from the stored delivery log.

        Groups deliveries into innings -> overs -> balls, tagging each ball with
        its outcome (runs, boundary, wicket, wide/no-ball) and deriving the Fall
        of Wickets and partnerships per innings. Returns None when the match
        doesn't exist; innings stay empty when no ball-by-ball data is stored
        (pre-dates the scorer, e.g. S1's imported matches).
        """
        season_id = (season_id or "").strip().lower()
        match_id = (match_id or "").strip()
        key = _match_key(season_id, match_id)
        with self.db.read() as conn:
            registry = conn.execute(
                "SELECT * FROM match_registry WHERE match_key = ?", (key,)).fetchone()
            match_row = conn.execute(
                "SELECT * FROM match_stats WHERE match_key = ?", (key,)).fetchone()
        if not registry and not match_row:
            return None
        registry = row_to_dict(registry) if registry else {}
        match_row = row_to_dict(match_row) if match_row else {}

        innings = []
        fow = []
        partnerships = []
        deliveries = json_loads(match_row.get("delivery_log"), []) if match_row else []
        if deliveries:
            # Group by Innings Order, preserving row order (scorer writes balls in order).
            inn_teams = {}
            inn_balls = {}
            for row in deliveries:
                inn = str(row.get("Innings Order") or "").strip() or "1"
                inn_teams.setdefault(inn, str(row.get("Batting Team") or "").strip())
                inn_balls.setdefault(inn, []).append(row)
            for inn in sorted(inn_teams, key=_safe_int):
                team = inn_teams[inn]
                rows = inn_balls[inn]
                overs = []
                cur_over = None
                runs_total = 0
                wkts_total = 0
                # Partnership tracking: runs since the last wicket (exclusive of
                # the wicket ball itself), resets on each dismissal.
                part_runs = 0
                part_wkts = 0
                last_partner = ""
                for row in rows:
                    over_no = _safe_int(row.get("Over Number"))
                    ball_no = _safe_int(row.get("Ball Number"))
                    if cur_over is None or cur_over["number"] != over_no:
                        cur_over = {"number": over_no, "balls": []}
                        overs.append(cur_over)
                    runs_bat = _safe_int(row.get("Runs Bat"))
                    runs_extra = _safe_int(row.get("Runs Extra"))
                    runs = runs_bat + runs_extra
                    extras_type = str(row.get("Extras Type") or "").strip()
                    dismissed = str(row.get("Dismissed Batter") or "").strip()
                    is_wicket = bool(dismissed and dismissed != "None")
                    runs_total += runs
                    if is_wicket:
                        wkts_total += 1
                        # Runs on the wicket ball belong to the outgoing
                        # partnership only if no runs were taken (run-out edge
                        # cases are ignored; close the partnership first).
                        part_runs += runs
                        part_wkts += 1
                        partnerships.append({
                            "innings": inn, "team": team, "runs": part_runs,
                            "wickets": part_wkts, "partners": last_partner,
                            "dismissed": dismissed,
                            "at": f"{runs_total}-{wkts_total}",
                        })
                        part_runs = 0
                        part_wkts = 0
                        last_partner = ""
                    else:
                        part_runs += runs
                        if not last_partner:
                            last_partner = str(row.get("Batter") or "").strip()
                    label = "W" if is_wicket else ""
                    if not label:
                        if extras_type == "Wide":
                            label = f"{runs}wd"
                        elif extras_type == "No Ball":
                            label = f"{runs}nb"
                        else:
                            label = str(runs)
                    cur_over["balls"].append({
                        "ball_no": ball_no,
                        "over_no": over_no,
                        "label": label,
                        "runs": runs,
                        "runs_bat": runs_bat,
                        "runs_extra": runs_extra,
                        "extras_type": extras_type,
                        "batter": str(row.get("Batter") or "").strip(),
                        "bowler": str(row.get("Bowler") or "").strip(),
                        "dismissed": dismissed if is_wicket else "",
                        "progressive": f"{runs_total}/{wkts_total}",
                    })
                # Unbroken (current) partnership when the innings ends.
                if part_runs or last_partner:
                    partnerships.append({
                        "innings": inn, "team": team, "runs": part_runs,
                        "wickets": part_wkts or 0, "partners": last_partner,
                        "dismissed": "", "current": True,
                        "at": f"{runs_total}/{wkts_total}",
                    })
                fow_entry = []
                wkt_count = 0
                for row in rows:
                    dismissed = str(row.get("Dismissed Batter") or "").strip()
                    if dismissed and dismissed != "None":
                        wkt_count += 1
                        fow_entry.append(
                            f"{row.get('Progressive Runs') or runs_total}-{wkt_count} "
                            f"({dismissed}, {row.get('Over Number')}.{row.get('Ball Number')})")
                fow.append({"innings": inn, "team": team, "entries": fow_entry})
                innings.append({
                    "innings": inn, "team": team,
                    "total": f"{runs_total}/{wkts_total}",
                    "runs": runs_total, "wickets": wkts_total,
                    "overs": overs,
                })

        return {
            "season_id": season_id, "match_id": match_id, "match_key": key,
            "between": registry.get("between") or match_row.get("result") or "",
            "match_number": registry.get("match_number") or "",
            "match_title": registry.get("match_title") or match_row.get("result") or "",
            "venue": registry.get("venue") or "",
            "match_date": registry.get("match_date") or "",
            "result": match_row.get("result") or "",
            "toss": match_row.get("toss") or "",
            "has_ball_by_ball": bool(deliveries),
            "innings": innings,
            "fow": fow,
            "partnerships": partnerships,
        }

    def team_profile(self, team_slug: str) -> dict:
        with self.db.read() as conn:
            teams = [dict(r) for r in conn.execute(
                "SELECT * FROM teams").fetchall()]
            global_teams = [dict(r) for r in conn.execute(
                "SELECT * FROM global_teams").fetchall()]
        # Global-only teams (not in any season) resolve through global_teams too.
        pool = list(teams) + [{
            "id": gt["id"], "name": gt["name"], "global_team_id": gt["id"],
            "season_id": None, "manager_player_id": gt["manager_player_id"],
        } for gt in global_teams]
        target = self._resolve_team_slug(team_slug, pool)
        if not target:
            return None
        gid = (target["global_team_id"] or "").strip() or target["id"]
        profile = next((gt for gt in global_teams if gt["id"] == gid), None)
        name = (profile or {}).get("name") or target["name"]
        logo = (profile or {}).get("logo") or ""
        about = (profile or {}).get("about") or ""
        manager_player_id = (profile or {}).get("manager_player_id") or target.get("manager_player_id")

        with self.db.read() as conn:
            rows = conn.execute(
                "SELECT * FROM match_team_stats WHERE team_id = ?", (gid,)).fetchall()
        per_season = {}
        totals = {"matches": 0, "wins": 0, "losses": 0, "ties": 0, "no_results": 0,
                  "points": 0, "runs_for": 0, "balls_for": 0, "runs_against": 0,
                  "balls_against": 0, "fantasy_points": 0}
        for r in rows:
            season = r["season_id"]
            e = per_season.setdefault(season, {
                "season_id": season, "matches": 0, "wins": 0, "losses": 0, "ties": 0,
                "no_results": 0, "points": 0, "runs_for": 0, "balls_for": 0,
                "runs_against": 0, "balls_against": 0, "fantasy_points": 0,
            })
            wins = _safe_int(r["wins"]); losses = _safe_int(r["losses"])
            ties = _safe_int(r["ties"]); nrs = _safe_int(r["no_results"])
            if wins == losses == ties == nrs == 0:
                result = (r["result"] or "").lower()
                wins = 1 if result == "win" else 0
                losses = 1 if result == "loss" else 0
                ties = 1 if result in ("tie", "draw") else 0
                nrs = 1 if not (wins or losses or ties) else 0
            e["matches"] += wins + losses + ties + nrs
            e["wins"] += wins; e["losses"] += losses; e["ties"] += ties
            e["no_results"] += nrs; e["points"] += wins * 2 + ties
            e["runs_for"] += _safe_int(r["runs_scored"])
            e["balls_for"] += _safe_int(r["balls_faced"])
            e["runs_against"] += _safe_int(r["runs_conceded"])
            e["balls_against"] += _safe_int(r["balls_bowled"])
            e["fantasy_points"] += _safe_int(r["fantasy_points"])
        for e in per_season.values():
            e["run_rate_for"] = round((e["runs_for"] * 6.0 / e["balls_for"]) if e["balls_for"] else 0.0, 2)
            e["run_rate_against"] = round((e["runs_against"] * 6.0 / e["balls_against"]) if e["balls_against"] else 0.0, 2)
            e["nrr"] = round(e["run_rate_for"] - e["run_rate_against"], 2)
            e["nrr_display"] = f"{e['nrr']:.2f}"
            for k in totals:
                if k != "matches" or True:
                    totals[k] += e.get(k, 0) if k not in ("nrr", "run_rate_for", "run_rate_against", "nrr_display") else 0
        totals["nrr"] = round(
            round((totals["runs_for"] * 6.0 / totals["balls_for"]) if totals["balls_for"] else 0.0, 2)
            - round((totals["runs_against"] * 6.0 / totals["balls_against"]) if totals["balls_against"] else 0.0, 2), 2)
        totals["nrr_display"] = f"{totals['nrr']:.2f}"

        squads = []
        for t in teams:
            if (t["global_team_id"] or "").strip() != gid and t["id"] != gid:
                continue
            squads.append({
                "season_id": t["season_id"],
                "name": t["name"],
                "manager_global_player_id": t["manager_player_id"],
                "players": json_loads(t["players"], []),
                "bench": json_loads(t["bench"], []),
            })
        squads.sort(key=lambda s: _season_sort_key(s.get("season_id") or ""))

        return {
            "team_id": gid,
            "team_name": name,
            "team_slug": team_profile_slug(gid, name),
            "logo": logo,
            "about": about,
            "manager_player_id": manager_player_id,
            "global_stats": totals,
            "season_stats": [dict(sorted(e.items())) for e in per_season.values()],
            "squads": squads,
        }

    def player_profile(self, player_slug: str) -> dict:
        with self.db.read() as conn:
            global_row = conn.execute(
                "SELECT * FROM global_players").fetchall()
            player_rows = conn.execute(
                "SELECT * FROM match_player_stats").fetchall()
        players = []
        for g in global_row:
            players.append({"player_id": g["id"], "player_name": g["name"]})
        for r in player_rows:
            players.append({"player_id": r["player_id"], "player_name": r["player_name"]})
        pid = self._resolve_player_slug(player_slug, players)
        if not pid:
            return None

        meta = next((g for g in global_row if g["id"] == pid), None)
        meta = row_to_dict(meta) if meta else {}
        agg = {"matches": 0, "runs": 0, "balls_faced": 0, "dismissed": 0,
               "wickets": 0, "balls_bowled": 0, "runs_conceded": 0,
               "fantasy_score": 0, "fours": 0, "sixes": 0}
        per_season = {}
        per_team = {}
        matches_list = []
        for r in player_rows:
            if r["player_id"] != pid:
                continue
            r = row_to_dict(r)
            matches_list.append(r)
            agg["matches"] += 1
            agg["runs"] += _safe_int(r["runs"])
            agg["balls_faced"] += _safe_int(r["balls_faced"])
            agg["dismissed"] += _safe_int(r["dismissed"])
            agg["wickets"] += _safe_int(r["wickets"])
            agg["balls_bowled"] += _safe_int(r["balls_bowled"])
            agg["runs_conceded"] += _safe_int(r["runs_conceded"])
            agg["fantasy_score"] += _safe_int(r["fantasy_score"])
            agg["fours"] += _safe_int(r["fours"])
            agg["sixes"] += _safe_int(r["sixes"])
            for bucket, key in ((per_season, r["season_id"]), (per_team, r["team_id"])):
                e = bucket.setdefault(key, {"key": key, "matches": 0, "runs": 0, "wickets": 0,
                                            "fantasy_score": 0})
                e["matches"] += 1
                e["runs"] += _safe_int(r["runs"])
                e["wickets"] += _safe_int(r["wickets"])
                e["fantasy_score"] += _safe_int(r["fantasy_score"])
        for bucket in (per_season, per_team):
            for e in bucket.values():
                e.pop("key", None)
        agg["strike_rate"] = round(agg["runs"] * 100.0 / agg["balls_faced"], 2) if agg["balls_faced"] else 0.0
        agg["batting_average"] = round(agg["runs"] / agg["dismissed"], 2) if agg["dismissed"] else 0.0
        agg["economy"] = round(agg["runs_conceded"] * 6.0 / agg["balls_bowled"], 2) if agg["balls_bowled"] else 0.0
        matches_list.sort(key=lambda m: m.get("created_at") or "")

        name = meta.get("name") or (matches_list[0]["player_name"] if matches_list else pid)
        return {
            "player_id": pid,
            "player_name": name,
            "player_slug": player_profile_slug(pid, name),
            "meta": {k: meta.get(k) for k in ("name", "tier", "speciality")},
            "global_stats": agg,
            "per_season": sorted(per_season.items(), key=lambda kv: _season_sort_key(kv[0])),
            "per_team": sorted(per_team.items(), key=lambda kv: kv[0]),
            "matches": matches_list,
        }

    # ------------------------------------------------------------------
    # slug resolution
    # ------------------------------------------------------------------
    def _resolve_team_slug(self, slug: str, teams: list) -> dict:
        safe = (slug or "").strip().lower()
        if not safe:
            return None
        base, suffix = (safe.rsplit("-", 1) if "-" in safe else (safe, ""))
        for t in teams:
            gid = (t["global_team_id"] or "").strip() or t["id"]
            if team_profile_slug(gid, t["name"]) == safe:
                return t
        if suffix:
            for t in teams:
                gid = (t["global_team_id"] or "").strip() or t["id"]
                if gid.lower().startswith(suffix):
                    return t
        if base:
            for t in teams:
                if _slugify_fragment(t["name"]) == base:
                    return t
        return None

    def _resolve_player_slug(self, slug: str, players: list) -> str:
        safe = (slug or "").strip().lower()
        if not safe:
            return ""
        base, suffix = (safe.rsplit("-", 1) if "-" in safe else (safe, ""))
        for p in players:
            if player_profile_slug(p["player_id"], p["player_name"]) == safe:
                return p["player_id"]
        if suffix:
            matches = [p for p in players if p["player_id"].lower().startswith(suffix)]
            if len(matches) == 1:
                return matches[0]["player_id"]
        if base:
            matches = [p for p in players if _slugify_fragment(p["player_name"]) == base]
            if len(matches) == 1:
                return matches[0]["player_id"]
        return ""

    def season_team_options(self, season_id: str = "") -> list:
        with self.db.read() as conn:
            if season_id:
                rows = conn.execute(
                    "SELECT * FROM teams WHERE season_id = ?", (season_id,)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM teams").fetchall()
        options = []
        for t in rows:
            gid = (t["global_team_id"] or "").strip() or t["id"]
            options.append({"id": gid, "name": t["name"]})
        return options
