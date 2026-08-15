# Prod Data Import Plan

Goal: bring the **Season 1** data from the deployed previous version (`prod-data/`, TinyDB JSON
exports of the old Flask app) into the new rebuild's SQLite DB (`data/scl.db`, schema in
`app/schema.py`). After import, the rebuilt app should show the real S1 auction results, rosters,
and (once the matches increment lands) league stats — no manual re-entry.

Reference docs: `MEMORY.md`, `RESUME.md`, `PLAN.md`. Old app lives at `../SCL`.

---

## 1. Source data inventory (`prod-data/`)

| File | What it is | Used for |
|---|---|---|
| `auction_live_db.json` | Live auction DB: `users` (5), `meta` (phase=complete), `players` (13 sold), `teams` (4), `bids` (66) | Core auction data (Phase 1) |
| `auction_snapshots/season-1-setup.json` | Pre-auction state: 13 unsold players, 4 empty teams | Ruleset reconstruction (purses/credits/base prices) |
| `auction_snapshots/season-1-final-draft.json` | Same content as `auction_live_db.json` | Cross-check / backup |
| `season_dbs/season-1.json` | **Published season**: 17 players (13 auction + 4 managers), 4 teams w/ final purses, users, `season_meta` (published/frozen), 17 fantasy entries, 44 finance transactions | Primary source for Phase 1 (richest file) |
| `global_auth_db.json` | users only (admin, different hash than auction DB) | Not needed — new app seeds its own admin |
| `global_league_db.json` | 17 global players, season↔global links, rosters, scorer match/team/player stats + registry | Global players + links (Phase 1); all scorer stats (Phase 2) |
| `matches/*.csv` | 13 ball-by-ball scorer CSVs (M1–M5, M7–M13, `test1`) | Asset copy now; ingest in offline-scorer increment |
| `scorer_config.json` | Scorer page config (max_overs 3, v1.3.0) | Asset copy now; used by scorer increment |

Notes:
- **M6 has no CSV** — it was a walkover (finance tx #16: "Walkover in Match 6"). Correct, not a gap.
- `global_auth_db.json` admin hash differs from the auction DB's admin hash; neither is
  importable/useful — the rebuild keeps its own seeded `admin`/`admin123`.

## 2. Target schema (new app, `app/schema.py`)

Tables to populate: `global_players`, `seasons`, `rulesets`, `players`, `teams`, `users`, `bids`,
`auction_meta`, `season_snapshots` (+ `auction_action_log` optionally, see §6).

Not yet in schema (deferred to their increments): scorer tables, finance table, fantasy.
`bank_accounts`/`vault_positions`/`bank_transactions` — S1 predates the central bank; **no data to
import**, and the services create accounts lazily, so nothing to pre-seed.

## 3. ID strategy

**Preserve all legacy IDs verbatim.** Old local IDs (players/teams/bids) are hex strings already
cross-referenced inside the source files; keeping them makes every FK relation drop in unchanged
and matches how the new app generates IDs (TEXT PKs, hex). No remapping step needed.

## 4. Mapping (old → new)

### 4.1 global_players (17)
`global_league_db.global_players` → direct copy: `id, name, tier, speciality, created_at`.

### 4.2 seasons
`season_dbs/season-1.json` → `seasons`:
- `id` = `"season-1"`, `name` = `"Season 1"`, `status` = `"completed"`,
  `created_at` = `season_meta.created_at`.

### 4.3 rulesets — **S1 values, not S2 defaults**
Reconstructed from the setup snapshot + season-1 data (S1 ≠ S2 economy):

| Field | S1 value | Evidence |
|---|---|---|
| `tier_purses` | `{platinum: 4000, gold: 4800, silver: 5500}` | setup teams' `purse_remaining` |
| `tier_base_prices` | `{platinum: 1500, gold: 800, silver: 400}` | setup players' `base_price` |
| `tier_credits` | `{platinum: 3, gold: 2, silver: 1}` | setup `credits_remaining` (5/6/7) |
| `total_credits` | `8` | |
| `bid_increment` | `50` | bid log deltas |
| `phase_b_price` | `0` | Anas/Hassin sold at 0 in Phase B |
| `credit_refund_rate` | `500` | S1 rate (S2 = 1000) |
| `required_players` / `roster_size` | `3` / `4` | |
| `break_minutes` | `5` | default |
| `phase_order` | `["silver", "gold", "break", "platinum", "phase_b"]` | ⚠ approx — see §8 |

### 4.4 players (17)
`season_dbs/season-1.json` `players` → `players`. Direct: `id, season_id, global_player_id, name,
tier, speciality, base_price, credits, status, sold_to_team_id, sold_price, phase_sold,
nominated_phase_a`. `current_bid`/`current_bidder_team_id` → 0/NULL. `nomination_order` → NULL.
The 4 manager players come in as regular players with `sold_to_team_id` = own team, `sold_price` = 0
(that's exactly how the old data represents them).

### 4.5 teams (4)
`season_dbs/season-1.json` `teams` → `teams`:
- `manager_player_id` = old `manager_global_player_id` (references global_players)
- `purse_remaining` = **final** values (2285 / 1145 / 2885 / 1365) — these already include all 44
  season finance transactions, so no finance replay is needed for correct balances
- `players` / `bench` = old JSON lists (local player ids), verbatim
- `is_active` = 1, `control_status` = `"manager_controlled"`

### 4.6 users (4 managers)
`season_dbs/season-1.json` `users` (skip admin) → `users`:
- `username`, `password_hash` (old Werkzeug `scrypt:` hashes — **compatible**, logins keep working),
  `role` = `"manager"`, `display_name`, `team_id` (old value), `global_player_id` = the manager
  player's global id (via `season_player_links` or `players[].global_player_id`).

### 4.7 bids (66)
`auction_live_db.json` `bids` → `bids`: `id, season_id, ts, team_id, player_id, amount, phase, kind`
verbatim (legacy phase strings like `phase_a_silver_gold` kept in the bids, see §8).

### 4.8 auction_meta
`phase` = `"complete"`, `current_player_id` = NULL, `nomination_history` = `[]`.

### 4.9 season_snapshots (published page)
The `/season/season-1` page renders `season_snapshots.payload` shaped like `get_state()` output
(`teams[].manager_name/player_labels/bench_labels`, `players[].sold_to_team_name`, `phase`, …).
Old `season-1.json` is **not** that shape. So: after loading core data, **reconstruct** the payload
in-app via `AuctionService.get_state("season-1")` and insert the snapshot row with the old
`published_at` (2026-04-11T12:22:35.656116) and name `"Season 1"`. This guarantees the published
page renders with the real final squads.

## 5. Import order (dependency order)

1. `global_players`
2. `seasons` + `rulesets` (+ `auction_meta`)
3. `players` (FK → global_players, seasons)
4. `teams` (FK → seasons, global_players)
5. `users` (managers; FK → global_players, team_id)
6. `bids` (FK → teams, players)
7. `season_snapshots` (reconstructed via `get_state`)

Core rows in one `db.write()` transaction (rollback on any error); the snapshot is inserted
in a **second** write after commit, because it is rebuilt from live state via `get_state()` and
WAL makes uncommitted rows invisible to a fresh read connection (MEMORY.md gotcha).

## 6. Script design — `scripts/import_prod.py`

- Python stdlib + app modules only (uses `app.db.Database`, `app.services.auction_service`, and
  `app.rules` / `app.ruleset` for the ruleset row). Run:
  `./.venv/Scripts/python.exe scripts/import_prod.py [--data prod-data] [--db data/scl.db]`
- **Idempotency**: refuses to run if any target table already has rows (beyond the seeded admin)
  unless `--force`. Fresh import is the happy path (dev DB is empty today).
- `--phase core|all`: `core` = §4.1–4.9 (Phase 1). `all` also imports scorer stats + finance +
  fantasy **once their schema tables exist** (guarded by table-existence check, so it's safe to run
  now and again later).
- Prints a summary (counts per table) and exits non-zero on mismatch vs. expected counts
  (17/17/4/4/5/66/1).
- Optional `--log-actions`: synthesize an `auction_action_log` entry (`import_prod`) so the admin
  undo stack behaves sensibly (a single "imported prod data" entry that is not undoable), or skip —
  empty log is also fine.

## 7. Deferred phases (need schema first)

| Phase | Data | Home | When |
|---|---|---|---|
| 2 — Matches & stats | `scorer_match_registry` (13), `scorer_match_stats` (13), `scorer_team_match_stats` (26), `scorer_player_match_stats` (93), `scorer_team_global_stats` (4), `scorer_player_global_stats` (17) | new scorer tables in `schema.py` | Matches/stats increment (PLAN next-steps #1) |
| 3 — Finance | 44 `finance_transactions` (revenue, umpire duties, fines, sub cash) | new finance/ledger table | Finances increment (PLAN #2) |
| 4 — Fantasy | 17 `fantasy_entries` | new fantasy table (or drop — S1 fantasy is closed) | optional |
| Assets | `matches/*.csv` + `scorer_config.json` | copy to `data/matches/` + `data/scorer_config.json` now; ingest later | offline scorer increment (PLAN #4) |

Match CSVs + scorer config should be copied into `data/` during Phase 1 so the rebuild carries the
raw source, even though ingestion happens later.

## 8. Decisions & gotchas

- **S1 ruleset ≠ S2 defaults** — hardcode the S1 table from §4.3 in the importer (documented, not
  guessed from code).
- **Phase vocabulary mismatch**: the old app used `phase_a_silver_gold` (combined phase); the new
  model only has per-tier phases (`phase_a_gold`, `phase_a_silver`). The **ruleset's** `phase_order`
  is an approximation (`["silver", "gold", "break", "platinum", "phase_b"]`, matching S1's actual
  order — silver/gold first, then platinum after a ~17-min gap); the **bids** keep their original
  phase strings verbatim, so the true history survives in the log.
- **S1 Phase B price was 0**, not the S2 200 — set `phase_b_price = 0` in the S1 ruleset.
- **Password hashes are portable** — old hashes are Werkzeug `scrypt:32768:8:1$…`, exactly what
  `werkzeug.security.check_password_hash` verifies today. Managers log in with their existing
  passwords; admin stays `admin`/`admin123`.
- **Don't import old admin users** (two different hashes across the old DBs; ours is seeded fresh).
- **Don't replay the 44 finance transactions** — final team purses already include them.
- **Published payload must be rebuilt, not copied** — old published JSON has a different shape
  than `published.html` expects.
- **WAL/read-connection gotcha** (from MEMORY.md): do all writes inside one `db.write()`;
  reconstruct the snapshot from the same DB after commit.
- `data/scl.db` is currently empty (admin only) — import is a clean first fill; tests use a temp
  DB and are unaffected.

## 9. Verification

1. Run import script → counts match §6 (17 players, 4 teams, 4 managers + admin, 66 bids, 1 snapshot).
2. `./.venv/Scripts/python.exe -m pytest tests/ -q` — all green (import must not change app behavior).
3. Boot server; check:
   - `/` lists "Season 1" + published card; `/season/season-1` shows the 4 final squads + all players.
   - `/admin` season page shows completed state; action log intact (or import entry).
   - Manager login (Hassan/Hashir/Osama/Owais with old passwords) lands on `/manager` with their team.
4. Spot-check: Naan CC roster = Yousuf, Qambar, Azen (+ manager Hashir); sold prices match the bid log.
