# Matches & Stats — Increment Plan ("Increment 1" of the remaining work)

Goal: make the league real — match registry + scorer CSV import, per-match team/player stats,
**league table with S2 tie-breakers**, leaderboards, and team/player profiles — then load the
Season 1 scorer data already exported in `prod-data/` so the rebuild shows the actual S1 season.

Status: **implemented** (17 tests, E2E verified, S1 data imported). Port source was
`../SCL/app/services/scorer_service.py` (3.7k lines — ported the mechanics, not the whole file)
+ `../SCL/app/routes/landing.py` + admin scorer routes. Docs: `MEMORY.md`, `PROD_IMPORT_PLAN.md`
(Phase 2 = this data, run as `scripts/import_prod.py --phase stats`).

---

## 1. What's already in place (no rework)

- `data/scl.db` has **Season 1 imported**: players/teams/users/bids carry the old **local** ids
  verbatim; `players.global_player_id` links to the 17 global players (also imported).
- `data/matches/*.csv` (13) + `data/scorer_config.json` already staged by `scripts/import_prod.py`.
- `prod-data/global_league_db.json` holds the S1 scorer tables (registry 13, match stats 13,
  team-match 26, player-match 93, team-global 4, player-global 17) — the import source.
- No match/scorer schema or code exists yet in the rebuild (confirmed).

## 2. Schema additions (`app/schema.py`)

Five tables, mirroring the old global-league shapes but keyed for the rebuild:

```sql
-- Fixture + status table (old scorer_match_registry)
CREATE TABLE IF NOT EXISTS match_registry (
  match_key TEXT PRIMARY KEY,            -- "season-1:m1"
  season_id TEXT NOT NULL REFERENCES seasons(id),
  match_id TEXT NOT NULL,                -- "M1"
  match_number TEXT,                     -- "Match 1"
  match_title TEXT,
  between TEXT,                          -- "Naan CC vs Pandiya Associates"
  venue TEXT,
  match_date TEXT,
  walkover INTEGER NOT NULL DEFAULT 0,
  walkover_winner_team_id TEXT,          -- global team id
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (season_id, match_id)
);

-- Upload metadata + result (old scorer_match_stats)
CREATE TABLE IF NOT EXISTS match_stats (
  match_key TEXT PRIMARY KEY REFERENCES match_registry(match_key),
  season_id TEXT NOT NULL,
  match_id TEXT NOT NULL,
  result TEXT, toss TEXT, winner_team_id TEXT,
  delivery_rows INTEGER, team_rows INTEGER, player_rows INTEGER,
  source_file TEXT, uploaded_by TEXT, uploaded_at TEXT,
  include_in_fantasy_points INTEGER NOT NULL DEFAULT 1
);

-- Per-team per-match (old scorer_team_match_stats) — team_id = GLOBAL team id
CREATE TABLE IF NOT EXISTS match_team_stats (
  id TEXT PRIMARY KEY,
  match_key TEXT NOT NULL REFERENCES match_registry(match_key),
  season_id TEXT NOT NULL,
  team_id TEXT NOT NULL,
  team_name TEXT NOT NULL,
  runs_scored INTEGER DEFAULT 0, balls_faced INTEGER DEFAULT 0, wickets_lost INTEGER DEFAULT 0,
  fours INTEGER DEFAULT 0, sixes INTEGER DEFAULT 0,
  wides_faced INTEGER DEFAULT 0, noballs_faced INTEGER DEFAULT 0,
  runs_conceded INTEGER DEFAULT 0, balls_bowled INTEGER DEFAULT 0, wickets_taken INTEGER DEFAULT 0,
  wides_bowled INTEGER DEFAULT 0, noballs_bowled INTEGER DEFAULT 0,
  overs_faced TEXT, overs_bowled TEXT, run_rate_for REAL, run_rate_against REAL,
  result TEXT, wins INTEGER DEFAULT 0, losses INTEGER DEFAULT 0,
  ties INTEGER DEFAULT 0, no_results INTEGER DEFAULT 0,
  fantasy_points INTEGER DEFAULT 0
);

-- Per-player per-match (old scorer_player_match_stats) — player_id = GLOBAL player id
CREATE TABLE IF NOT EXISTS match_player_stats (
  id TEXT PRIMARY KEY,
  match_key TEXT NOT NULL REFERENCES match_registry(match_key),
  season_id TEXT NOT NULL,
  player_id TEXT NOT NULL,
  player_name TEXT NOT NULL,
  team_id TEXT NOT NULL, team_name TEXT NOT NULL,
  role TEXT, tier TEXT,
  matches INTEGER DEFAULT 1,
  innings_batted INTEGER DEFAULT 0, not_out INTEGER DEFAULT 0, dismissed INTEGER DEFAULT 0,
  runs INTEGER DEFAULT 0, balls_faced INTEGER DEFAULT 0, fours INTEGER DEFAULT 0, sixes INTEGER DEFAULT 0,
  innings_bowled INTEGER DEFAULT 0, balls_bowled INTEGER DEFAULT 0,
  runs_conceded INTEGER DEFAULT 0, wickets INTEGER DEFAULT 0, wides INTEGER DEFAULT 0, noballs INTEGER DEFAULT 0,
  strike_rate REAL, economy REAL,
  fantasy_score INTEGER DEFAULT 0, fantasy_bat_points REAL, fantasy_bowl_points REAL
);
-- Indexes: match_registry(season_id), match_team_stats(season_id, team_id),
--          match_player_stats(season_id, player_id, team_id)
```

**Aggregates (league table, leaderboards, profiles) are computed on demand** from these rows
(SQL GROUP BY / Python), not stored — no cache drift, no `_rebuild_global_aggregates` equivalent.
The old `scorer_*_global_stats` tables are imported only as **cross-check reference data** (see §6).

### 2a. One migration needed: `teams.global_team_id`
Stats are keyed by **global** team/player ids (matching prod-data; enables cross-season
leaderboards). Players already link via `global_player_id`; `teams` does **not** have a global id
column. Add `global_team_id TEXT` to `teams`:
- `app/schema.py`: add column to the CREATE TABLE (fresh DBs).
- Existing `data/scl.db`: schema.py uses `CREATE TABLE IF NOT EXISTS`, so add a **tiny bootstrap
  migration** in `db.bootstrap()` — `PRAGMA table_info(teams)` → `ALTER TABLE teams ADD COLUMN
  global_team_id` if missing (first migration; keep a list for future ones).
- Backfill from `prod-data` `season_team_links` (local `8a7bb34d0a736b7a` ↔ global
  `4971c062ff605430` etc.) in the Phase-2 import, and store it in future Phase-1 imports.

## 3. Service — `app/services/scorer_service.py` (`ScorerService(db, auction_service)`)

Port the mechanics of the old service onto SQLite. Core pieces:

**Registry CRUD**
- `upsert_match_registry_entry(...)`, `delete_match_registry_entry(season, match_id)`,
  `get_match_registry_entry`, `list_match_registry(season)`, `list_match_seasons()`.
- Walkover entries → `_upsert_walkover_stats` (synthetic team rows: winner gets the win, zero
  balls; used by the table). Port from old `_upsert_walkover_stats`/`_sync_walkover_stats`.

**CSV import** — `import_match_csv(file, season_id, ...)`:
1. Parse ball-by-ball rows (28 required columns; header validation; skip trailing rows; read the
   optional **Substitution Log** section at the end for subs — port `_parse_match_csv_rows`).
2. **Normalize ids**: the S1 CSVs carry *local* ids (e.g. `ab6174c7950eb199`); the rebuild has the
   same local ids in `players`/`teams`, so map local → global via
   `players.global_player_id` / `teams.global_team_id` (much simpler than the old
   name-similarity fallbacks — keep a name-based fallback for robustness).
3. Derive per-team + per-player match rows (runs/balls/wickets/extras/overs, fantasy points via
   the ported formula: `FANTASY_BAT_POINTS`/`FANTASY_BOWL_POINTS`/tier multipliers/25pt match
   bonus — port from the old constants) — port `_derive_match_stats` + `_persist_match_stats`.
4. Guard: match must exist in the registry first; walkover matches refuse CSV; overwrite needs
   explicit confirmation (`MatchOverwriteConfirmationRequired`); archive the CSV to
   `data/matches/`.
5. `undo_imported_match(match_key)` — delete the three row kinds (no aggregate rebuild needed).

**League table** — `league_table(season_id)`:
- Points 2 win / 1 tie / 0 loss (S2 rulebook).
- NRR = `(runs_for × 6 / balls_for) − (runs_against × 6 / balls_against)`, displayed `x.xx`;
  overs strings `ov.bb`.
- Sort: **points → NRR → head-to-head → total boundaries** (S2 doc — see §7 deviation note;
  the old app sorted points → NRR → wins).

**Leaderboards** — `leaderboards(season_id, top_n=5)` (port `build_leaderboards`):
- Player: most runs, best strike rate (qualifier), most wickets, best economy, fantasy points.
- Team: points, NRR, fantasy points.

**Match summary** — `match_summary(season_id, match_id)` (port `get_match_summary`): innings
sections per team — batting table (runs, balls, 4s/6s, SR, out/not-out), bowling table (overs,
maidens n/a, runs, wickets, econ), extras, total `runs/wkts (ov)` — plus toss/result/winner and a
fantasy leaderboard.

**Profiles** — `team_profile(global_team_id)` (per-season record + NRR + squads via existing
`teams`/`players`) and `player_profile(global_player_id)` (global + per-season + per-team stats)
— port `get_team_profile`/`get_player_profile`.

## 4. Routes & screens

**Viewer (public) — extend `app/routes/viewer.py`:**
- `GET /matches` (+ `?season=`), `GET /matches/<season_id>`, `GET /matches/<season_id>/<match_id>`
  (summary; walkovers render the result without a scorecard).
- `GET /table` (default season), `GET /table/<season_id>` — league table.
- `GET /leaderboards`, `GET /leaderboards/<season_id>`.
- `GET /teams`, `GET /teams/<slug>` — team index + profile.
- `GET /players/<slug>` — player profile.
- Slugs: `team_profile_slug`/`player_profile_slug` (`name-idprefix`) with the old resolver.

**Admin — a dedicated `/admin/scorer` page** (mirrors the `/wagers/admin` pattern; the auction
control room stays focused):
- Config: season binding + max overs (`data/scorer_config.json`).
- Registry CRUD: add/delete matches, set between/title/number/date/venue, mark walkover + winner.
- Import: file upload → run `import_match_csv`; overwrite-confirm flow; undo last import.
- Quick view: recent imports, match registry list.

**Templates** (`app/templates/matches|teams|players/`): `matches/index.html`, `matches/table.html`,
`matches/leaderboard.html`, `matches/summary.html`, `teams/index.html`, `teams/detail.html`,
`players/detail.html` — mobile-first, reuse `.card/.table-wrap/.tag/.chip`; **nav** in `base.html`
gets a compact "Matches/Table" link set (nav is getting long — group stats under one link or a
small dropdown).

## 5. S1 data import — Phase 2 of `scripts/import_prod.py`

Extend with `--phase stats` (safe to run after core; guarded by table-existence like Phase 1):
1. Insert `match_registry` + `match_stats` from `scorer_match_registry`/`scorer_match_stats`
   (13 docs each; M6 missing by design — walkover; **skip `test1` by default**, `--include-test`
   keeps it).
2. Insert `match_team_stats` (26) + `match_player_stats` (93) verbatim (they already use global
   ids; `season_id='season-1'`).
3. Backfill `teams.global_team_id` from `season_team_links`.
4. Cross-check: compute the league table from the imported rows and compare standings/NRR against
   the old `scorer_team_global_stats` (imported as a reference table `stats_import_check` or just
   asserted in the script's summary) — flag any formula drift.

## Implemented notes (delta from plan)

- NRR matches the old published figures by rounding each rate to 2dp **before** subtracting
  (`round(rr_for,2) − round(rr_against,2)`) — the old app did this; the import cross-check
  compares against `scorer_team_global_stats` and passed clean.
- `between` is a SQLite keyword — quoted (`"between"`) in schema, service, and import SQL.
- M6 is a walkover with team rows in the source; imported as-is (no CSV, by design).
- `ScorerService(db)` takes no auction_service (kept decoupled); ids map via existing
  `players.global_player_id` / `teams.global_team_id` + name fallback.

## 6. Deviations & decisions (flag for review)

1. **Tie-breakers**: S2 rulebook says NRR → **head-to-head** → **boundaries**; the old app used
   points → NRR → wins. Implement the doc version (H2H among teams tied on points+NRR; then total
   fours+sixes). Small extra code, matches the rulebook.
2. **Aggregates computed on demand** vs. stored (old app stored them). Recommended: on demand;
   old aggregates used only as import cross-checks.
3. **`teams.global_team_id` migration** (§2a) — the one schema change to an existing DB; keep a
   tiny migration list in `db.bootstrap()`.
4. **Fantasy entries** (17 S1 fantasy teams) stay out of scope — no fantasy-game schema yet; only
   per-player fantasy *points* (already in `match_player_stats`) feed the leaderboards.
5. **Offline scorer download** (`/scorer`, `scorer.html`) stays in its own increment; this
   increment only *ingests* CSVs.
6. Match **finances** (ticket revenue per match) belong to the finances/Vault increment; the
   registry here is what that increment will hang revenue entries on.

## 7. Tests — `tests/test_matches.py`

1. Registry CRUD + walkover entry → synthetic team rows, winner credited.
2. CSV import: small fixture → team/player rows exact (runs, balls, extras, wickets, overs),
   fantasy points match the ported formula; missing column → ValueError; unknown match → error;
   overwrite requires confirm; walkover CSV → rejected.
3. Undo import removes all three row kinds.
4. League table: points/2-1-0; NRR ordering; **H2H tie-break**; **boundaries tie-break**.
5. Leaderboards: top run-scorer, top wicket-taker, fantasy leader; qualifier cutoffs.
6. Match summary shape (batting/bowling sections, extras, total string).
7. S1 import: counts (registry 13, match 13, team 26, player 93) + Naan CC NRR ≈ old aggregate
   (cross-check) + `teams.global_team_id` backfilled (4).
8. Route smoke tests (public pages 200; admin scorer requires login).

## 8. Build order

1. Schema (5 tables + `teams.global_team_id` + bootstrap migration).
2. `ScorerService` core: registry CRUD + walkover → CSV parse/normalize/derive/persist/undo.
3. Queries: league table (+ H2H/boundaries) → leaderboards → match summary → profiles.
4. Phase-2 import (`--phase stats`) + `global_team_id` backfill + cross-check vs old aggregates.
5. Routes + templates + nav; admin `/admin/scorer` page.
6. Tests (§7) + E2E: import real S1 CSVs into a copy, check the table matches prod numbers,
   pages render; `pytest` full suite stays green.
