# Offline Scorer — Plan (rev 2)

Status: **BUILT 2026-08-15** — 7 new tests (79 total green), E2E verified.
All three decisions confirmed (public access, delivery_log, reportlab) and implemented; the
call-up batting-order fix (batter_order) was added at the user's request.

Goal: port the standalone offline scorer (`../SCL/scorer.html`) so scorers can score a
match on the ground on a phone (no internet), export ball-by-ball CSV, and have the admin
import it — **and** make the scorecard PDF a first-class, DB-driven artifact instead of a
standalone script.

## Why this integrates better than the reference

The reference app has three disconnected pieces:

1. `scorer.html` — a mobile scoring UI (Jinja-rendered, exports a 32-col CSV).
2. Admin CSV import (`scorer_service.import_match_csv`) — ingests that CSV.
3. `scoreCard.py` — a **standalone script** that re-parses a CSV file on disk and builds a
   PDF with reportlab. It hardcodes S1 fantasy maps (`PLAYER_ROLES`, `PLAYER_TIERS`) and S1
   revenue (`REVENUE`), and re-derives batting/bowling/FOW from the raw CSV again.

The new app already collapses 1→2: `import_match_csv` accepts the scorer's 32 columns (28
required), normalizes local→global ids, computes fantasy with DB tiers, and fires the
finance hook. What's missing is serving the scorer page and the PDF.

So this increment's integration wins:

- **The scorer page is served by the app** from live DB rosters (`/scorer`, `/scorer/download`),
  like the reference — no manual file copying.
- **The PDF is generated from the DB** (the imported match is the single source of truth),
  not by re-parsing a CSV. Batting/bowling/FOW/fantasy all come from `match_summary` +
  `match_player_stats` — no duplicated derivation, no drift.
- **The PDF's revenue section is real** — it lists the actual `season_finance_entries`
  (match rewards / adjustments / transfers) for that match from the finance ledger, instead
  of scoreCard.py's hardcoded S1 numbers.
- **Fall of Wickets needs ball-by-ball**, which the DB doesn't keep (only row counts). Fix:
  store the parsed delivery log as JSON on `match_stats.delivery_log` at import time. Small,
  enables FOW + a future ball-by-ball view. Pre-existing S1 matches just omit the FOW line.

## Build steps

### 1. Port scorer.html → `app/templates/scorer/scorer.html`
- Copy `../SCL/scorer.html` verbatim (it is already a Jinja template). It expects:
  `scorer_config` {title, version}, `scorer_payload` {title, version, max_overs,
  season{slug,name}, teams[{id, name, manager_id, players[{id, name}]}]},
  `scorer_download_url`, `scorer_download_filename`.
- Keep it dependency-free (vanilla JS, no CDN) so the downloaded HTML works fully offline.

### 2. `ScorerService.build_scorer_context()` + `download_filename()`
- `build_scorer_context()`:
  - config = `load_config()` (title/version/max_overs/season_slug from `scorer_config.json`).
  - Resolve the season: config.season_slug → fallback latest season that has teams.
  - Teams payload from `teams` table of that season: `{id: <local team id>, name,
    manager_id: manager_player_id, players: [{id: <local player id>, name}]}`. **Local ids** —
    the CSV's ids round-trip through the existing `_identity_maps` (local→global, then name
    fallback), exactly like the reference's TinyDB doc ids.
  - Payload also includes the season's `match_registry` (match_id, between, team ids) so the
    setup screen can pre-fill match id/teams (nice-to-have, small JS).
- `download_filename(config)` → `scorer-v{version}.html` (sanitized).

### 3. Routes (on `matches_bp`, public — see Q1)
- `GET /scorer` → renders the template string with the context (live, usable in browser).
- `GET /scorer/download` → same HTML, `Content-Disposition: attachment`.
- Nav: "Scorer" link in `base.html` (public nav, next to Matches) + a "Download scorer HTML"
  button on `/admin/scorer`.

### 4. Schema: `match_stats.delivery_log` + `match_player_stats.batter_order`
- `match_stats.delivery_log`: TEXT (JSON) column, added to `schema.py` + a `db.bootstrap()`
  migration (`_MIGRATIONS`, pattern: `teams.global_team_id` / `vault_positions.unlocked`).
  `import_match_csv` writes the parsed rows (normalized view of the ball-by-ball) into it;
  walkover writes `[]`. Backward compatible: absent → FOW omitted.
- `match_player_stats.batter_order`: INTEGER column (same migration pattern). **Batting
  order fix** — the previous version (reference `scoreCard.py` AND current `match_summary`)
  listed batsmen sorted by runs, not by call-up order. The scorer's CSV already exports a
  `Batter Order` column (striker's call-up order per ball) that was never read:
  - Importer reads the optional `Batter Order` column → per player, min order across the
    match (each player bats in exactly one innings, so min across the match = their team's
    call-up order). Players without the column (old CSVs) fall back to first-appearance
    order from the delivery rows — still correct in practice.
  - Edge: the innings-start non-striker who never faces a ball never appears as striker →
    derive order 2 from the first delivery row of each innings.
  - `match_summary` sorts each innings' batting list by `batter_order` (None → end, in
    appearance order). This fixes the public summary page **and** the PDF (which is fed by
    `match_summary`).
  - S1's 13 imported matches have no ball-by-ball data (aggregated JSON) → their
    scorecards keep the old ordering; the fix applies to everything scored/imported going
    forward (Season 2).

### 5. `app/services/scorecard_service.py` (PDF from DB)
- Port scoreCard.py's PDF builders (header bar, BATTING/BOWLING subheaders, tables, FOW,
  fantasy leaderboard, page banner/footer, reportlab styles) — **fed by data, not CSV**:
  - `match_summary(season, match)` → team sections (batting rows incl. status/SR, bowling
    rows incl. overs/econ, extras, totals) + fantasy leaderboard (already computed at import).
  - `delivery_log` → Fall of Wickets `prog-1 (name, ov.ball)` lines per innings.
  - `finance_service.list_finance_entries(season)` filtered to the match → revenue table
    (replaces hardcoded REVENUE); omitted when empty.
- `build_scorecard_pdf(season_id, match_id) -> bytes` (BytesIO), walkover → small "won by
  walkover" card.
- Add `reportlab` to `requirements.txt` + install into `.venv` (Q3).

### 6. PDF route + links
- `GET /matches/<season>/<match>/scorecard` → `send_file(BytesIO, mimetype='application/pdf')`.
- "Scorecard PDF" link on the match summary page and on `/admin/scorer` recent imports.

### 7. Tests (`tests/test_scorer_offline.py`)
- Context build: payload shape, teams/players with local ids, season fallback, download filename.
- Routes: `/scorer` + `/scorer/download` render (test client); download has attachment header.
- **Round trip**: build a scorer-format CSV (as scorer.html exports) → `import_match_csv` →
  `match_summary` has correct teams/players/runs; `delivery_log` persisted.
- **Batting order**: craft a CSV where call-up order differs from runs order (first batter
  scores 0, fourth batter scores 50) → assert `match_summary` batting lists them in call-up
  order; also assert the no-column fallback and the innings-start non-striker edge.
- Scorecard: PDF bytes non-empty, starts with `%PDF`, sections present for a 2-innings match;
  FOW line present; revenue section reflects a posted match reward; walkover PDF renders.
- Full suite stays green.

### 8. Docs + commit
- New `OFFLINE_SCORER_PLAN.md` status; update MEMORY.md (build notes + gotchas:
  delivery_log, local-id round trip, PDF-from-DB), RESUME.md, PLAN.md. Commit per milestone.

## Decisions (confirmed 2026-08-15)

- **Q1 — Public access**: `/scorer`, `/scorer/download`, and the scorecard PDF are public
  (matches the reference; the scorer works at the ground without an account, and PDF data is
  already public on the match summary).
- **Q2 — Add `delivery_log`**: `match_stats.delivery_log` JSON column (migration in
  `_MIGRATIONS`); FOW lines in the PDF; pre-existing S1 matches omit the FOW line.
- **Q3 — Use reportlab**: add `reportlab` to `requirements.txt` + install into `.venv`;
  faithful port of scoreCard.py's PDF builders.

## Build order

1. Port scorer.html → `app/templates/scorer/scorer.html` + `build_scorer_context()` /
   `download_filename()` in ScorerService.
2. Routes `/scorer` + `/scorer/download` (public) + nav + admin link.
3. Schema: `match_stats.delivery_log` + `match_player_stats.batter_order` (migrations +
   write at import, `[]`/None for walkover).
4. `match_summary` batting sort → call-up order (fixes summary page + PDF).
5. `app/services/scorecard_service.py` (reportlab, DB-fed) + `/matches/<season>/<match>/scorecard`.
6. Tests (`tests/test_scorer_offline.py`) + full suite green.
7. Docs (MEMORY/RESUME/PLAN + this file) + commits.

## Out of scope (this increment)

- Ball-by-ball web view of a match (delivery_log enables it; separate increment).
- Auto-resolve wager markets from match results (wager-polish increment).
- Admin dashboard consolidation (separate increment).
