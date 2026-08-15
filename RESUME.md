# SCL Rebuild — Session Resume Handoff

Read this first in a new session, then `MEMORY.md` (living context + gotchas) and `PLAN.md`
(feature plan). Last updated 2026-08-15 at the end of the session that shipped the frontend
transformation Phase 1 (shell + design system + Home + Auth; light theme, mobile-first).

## Quickstart

```bash
./.venv/Scripts/python.exe run.py          # start server (debug, port 10001)
# or without reloader:
./.venv/Scripts/python.exe -c "from app import create_app, socketio; app = create_app(); socketio.run(app, host='0.0.0.0', port=10001, debug=False, use_reloader=False)"
./.venv/Scripts/python.exe -m pytest tests/ -q   # 111 tests (85 unit + 26 e2e)
```

E2E tests (Playwright, Chromium installed via `python -m playwright install chromium`):
`tests/e2e/` — boots the real app on a random port against a temp DB (`data/scl.db` is never
touched), seeds one season + users (admin / alice / cara / dave) + a wager, drives the flows
in a real browser. Run them with the rest of the suite (`pytest tests/`) or alone:
`pytest tests/e2e/ -q`.

- App: http://127.0.0.1:10001 — admin login `admin` / `admin123` (env-overridable:
  `SCL_ADMIN_USERNAME`, `SCL_ADMIN_PASSWORD`, `SCL_SECRET_KEY`, `SCL_DB_PATH`).
- DB: `data/scl.db` (SQLite, WAL) — holds the **real Season 1**: imported players/teams/managers/
  bids, published snapshot, and full scorer stats (13 matches, 93 player-match rows).
- Python 3.11 (`python`), venv at `.venv`, deps pinned in `requirements.txt`.
- ⚠ A stale server may be holding port 10001 from an earlier session, running **old code**
  (it 404s on `/table`, `/matches`, etc.). Restart it before verifying new work:
  `curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:10001/table` → if not 200, kill the
  old process and start fresh.

## What is this project

Rebuild of the Section-C Cricket League (SCL) website. Reference implementation (old app) lives at
`../SCL` (Flask + TinyDB + SocketIO) — port from it, don't rewrite from scratch. New stack
(decided with user): **Flask + SQLite + Flask-SocketIO, server-rendered Jinja, mobile-first, no ORM.**

Domain: 4+ teams of 4 players each; the manager owns the team and is one of the 4 players; a player
one season can be a manager the next (→ global players persist). Season = 12-match double round
robin; S2 abolishes the final (champion = table topper). Season rules are **fluid** → per-season
ruleset drives everything. Docs (3 PDFs, NOT final) + extracted `.txt` in the project root.

## Built so far (all tested, E2E verified)

### 1. Player Auction
- Per-season **ruleset** (S2 defaults preloaded): phase order, tier purses/base prices/credits,
  bid increment, Phase B price, refund rate — admin-editable. S1 has its own hardcoded economy
  (see import script `S1_RULESET`).
- Bidding (min = max(base, current + increment), +50), close lot, step back, break trades,
  draft completion + penalties, publish snapshot → `/season/<slug>`.
- **Action log + undo** (stack semantics; handlers at the bottom of `auction_service.py`).
- **Admin extras**: player CRUD, pre-auction purse gifts, team takeover/restore, post-auction
  transfer window.
- **Auth**: admin seed, self-signup + admin linking, manager assignment.

### 2. Central bank + Vault
- Per-player accounts (liquid + locked), ledger, **Vault** (7%/match, compounding default,
  manual-harvest toggle). UI on `/account` (vault positions show match progress + unlocked badge).
- **Wallet == purse from day one**: the manager's player account IS the team's account, funded at
  team creation; every auction money move (gift/close/transfer/trade/penalty) adjusts the wallet
  in the same transaction. No settlement step.
- **`teams.purse_remaining` dropped (2026-08-15)**: the wallet is the single source of truth for
  all team money — no lockstep, no dual bookkeeping. Granting money is one `bank.adjust`. The
  drop migration is in `db.bootstrap()`; the import reads expected purses from the source JSON;
  `public_budget_board` keeps the `purse_remaining` key (value = wallet) for snapshot compat.
- **Finances + Vault wiring**: `apply_match_yield` now compounds per-match (2000→2140→2290→2450→2622),
  `unlock_vault` (M12, force-able), `FinanceService` auto-pays both playing teams the ruleset
  `match_reward_amount` on match finalization + catches up yield, `process_pending` backfill,
  manual adjust/transfer with a ledger, one-step undo. Plan: `FINANCES_PLAN.md`.
- **Admin**: `/admin/finances` (adjust/transfer, process-pending, yield, unlock, ledger + undo,
  credit-refund hint table). **Public**: `/finances[/<season>]` Budget Board + ledger (nav link).
- **No credit-refund feature**: admin bank adjust + comment is the mechanism (per user decision).

### 3. Prod import (`scripts/import_prod.py`)
- `--phase core` (default): global players, Season 1 + S1 ruleset, players, teams, manager users
  (old Werkzeug hashes carry over), 66 bids, published snapshot. Refuses to run over existing data
  without `--force`.
- `--phase stats`: match registry (13), match stats (13, incl. M6 walkover), team rows (26),
  player rows (93), `teams.global_team_id` backfill (4), and a **league-table cross-check against
  the old deployed aggregates** (exits 2 on mismatch; currently clean).
- `--phase finance`: the 44 S1 finance_transactions as ledger rows + purse-chain cross-check
  (expected purses read from the **source JSON** — no purse column in the teams table anymore;
  exit 2 on drift) + **manager wallet seed** with the final purse. `--phase all` = core + stats +
  finance. Rebuild fresh (`rm data/scl.db && --phase all`) when re-verifying — a failed run still
  commits, and `--force` re-runs duplicate rows.

### 4. Wager platform
- Full protocol: propose + first stake → blind-estimate **calibration** (consensus = average) →
  **veto** (pre-open bankruptcy gate, refunds all) → peer bets → **house injection** → resolve
  (winners split the pot **proportionally**, House tops up to guarantee fair odds) | **void**
  (100% refunds), plus freeze/unfreeze. Plan: `WAGER_PLAN.md`.
- Money flows through `bank_transactions` (`wager_stake/payout/refund`, `house_inject`); House =
  `bank_accounts` row `house:house`, topped up via admin bank adjust.

### 5. Matches & stats
- Tables: `match_registry`, `match_stats`, `match_team_stats`, `match_player_stats` +
  `teams.global_team_id` (bootstrap migration in `db.bootstrap()` `_MIGRATIONS`).
- `app/services/scorer_service.py` (`ScorerService(db)`): registry CRUD, walkover synthesis,
  ball-by-ball CSV import (28 required columns, local→global id mapping + name fallback,
  Substitution Log, overwrite-confirm `MatchOverwriteConfirmationRequired`, archive to
  `data/matches/`), undo, **league table** (points 2/1/0, NRR = `round(rr_for,2) − round(rr_against,2)`
  — matches old published numbers; tie-breakers points → NRR → **H2H** → **boundaries**),
  leaderboards, match summaries, team/player profiles (slug `name-<id[:8]>`).
- Routes (`app/routes/matches.py`): public `/matches[/<season>[/<match>]]`, `/table`,
  `/leaderboards`, `/teams[/<slug>]`, `/players/<slug>`; admin `/admin/scorer` (config, registry
  CRUD, CSV import + undo). Nav links in `base.html`.

### 7. Admin dashboard consolidation
- **`/admin` is the Overview**: status cards (Auction / Matches / Finances / Wagers / Accounts)
  + recent activity feed; the auction control room moved to **`/admin/auction`**.
- **Shared tab shell** (`app/templates/admin/_tabs.html`): Overview · Auction · Scorer ·
  Finances · Wagers · Link on all six admin pages; active tab highlighted. Tabs link to
  existing URLs so all form POST→redirect flows keep working (auction actions →
  `/admin/auction`). base.html admin nav shrunk to a single **Admin** link.
- Overview context `_overview_context()` in admin.py (reuses services + a few direct
  queries: finalized/pending-finance counts, wallet Σ, vault/yield progress, wager counts,
  unlinked signups). Plan: `ADMIN_DASHBOARD_PLAN.md`.

### 6. Offline scorer + scorecard PDF
- **Offline scorer page** (port of `../SCL/scorer.html`): `app/templates/scorer/scorer.html`,
  served at public `/scorer` (use in-browser at the ground) + `/scorer/download` (standalone
  `scorer-v<version>.html`, works fully offline). Payload built by `build_scorer_context()`
  from **live DB rosters** — local team/player ids (round-trip through the import's local→global
  maps), manager included in every roster (managers play!), plus the season's `match_registry`
  so the setup screen pre-fills match id/teams/venue.
- **Batting order fix**: batsmen now display in **call-up order**, not by runs. The scorer's CSV
  already exports a `Batter Order` column that was never read → imported into new
  `match_player_stats.batter_order` (min per player; innings-start non-striker who never faces
  → 2; opener striker without data → 1; no-column fallback = first-appearance order).
  `match_summary` sorts batting by it, so the summary page AND the PDF are both fixed. S1's
  13 matches have no ball-by-ball → they keep old ordering (go-forward fix).
- **`match_stats.delivery_log`**: ball-by-ball rows stored as JSON at import (enables FOW +
  future ball-by-ball views).
- **Scorecard PDF from the DB** (`app/services/scorecard_service.py`, reportlab): fed by
  `match_summary` + `delivery_log` (Fall of Wickets) + the season finance ledger (revenue
  section = real rewards/adjustments for that match, not hardcoded S1 numbers). Route
  `/matches/<season>/<match>/scorecard`, linked from match summary + admin scorer.
- Plan: `OFFLINE_SCORER_PLAN.md`.

## Architecture map

```
run.py                      entry (socketio.run, port 10001)
app/
  __init__.py               app factory, services, blueprints, socketio, emit_state()
  config.py                 env-overridable settings
  db.py                     Database: thread-safe SQLite (RLock, WAL, BEGIN IMMEDIATE), JSON helpers,
                            _MIGRATIONS (bootstrap column adds, e.g. teams.global_team_id)
  schema.py                 full SQL schema (all tables)
  rules.py / ruleset.py     S2 constants + per-season Ruleset model
  authz.py                  login_required(role) decorator
  services/
    auction_service.py      seasons/rulesets, players/teams, bidding, trades, completion,
                            transfers, takeover, publish, action log, undo handlers
    auth_service.py         seed admin, signup, login, link/unlink, assign_manager
    bank_service.py         accounts, ledger, vault + 7%/match yield (adjust/get_or_create_account
                            accept an optional conn for in-transaction money ops)
    wager_service.py        wager lifecycle + proportional/house-guarantee payout
    scorer_service.py       registry, CSV import, walkover, league table, leaderboards,
                            summaries, profiles, offline-scorer context, batter_order,
                            delivery_log
    finance_service.py      season finance: match rewards, adjust/transfer, ledger, undo,
                            process_pending, credit hint (wallet == team purse)
    scorecard_service.py    reportlab PDF scorecard from match_summary + delivery_log + ledger
  routes/
    auth.py                 /auth/login|logout|signup, /auth/admin/link...
    admin.py                /admin control room + bank adjust + /admin/finances
    manager.py              /manager dashboard + bid|pass|trade JSON
    viewer.py               /, /live, /api/state, /season/<slug>
    banking.py              /account (balances, vault lock, reinvest, deposit)
    wagers.py               /wagers board|detail|admin
    admin.py                /admin (Overview), /admin/auction (control room), /admin/finances,
                            bank adjust, season/player/team/phase/transfer/takeover endpoints
    matches.py              /matches, /table, /leaderboards, /teams, /players, /admin/scorer,
                            /finances[/<season>], /scorer, /scorer/download,
                            /matches/<s>/<m>/scorecard
  templates/                base.html + viewer/, admin/, manager/, auth/, banking/, wagers/, matches/,
                            teams/, players/, scorer/ (offline scorer HTML)
  static/css/app.css        dark mobile-first theme (.card/.table-wrap/.tag/.chip/.feed/.grid…)
  static/js/app.js          live board via 4s polling of /api/state
tests/                      conftest.py (_setup helper) + test_auction (16), test_bank (4),
                            test_wager (15), test_matches (17), test_finance (21),
                            test_scorer_offline (7), test_admin_dashboard (6) — 85 total
```

## Conventions & gotchas (also in MEMORY.md — do not rediscover)

- **Never return via a separate read connection inside `db.write()`** — uncommitted rows are
  invisible to other connections (WAL). Returns go outside the `with` block.
- **Never open a second write transaction inside `db.write()`** — nested `BEGIN IMMEDIATE` on a
  fresh connection deadlocks. Pass the caller's `conn` into `bank_service.adjust(..., conn=conn)` /
  `get_or_create_account(..., conn=conn)` for atomic money ops.
- `app.config.from_object(dict)` doesn't work — use `app.config.update()` (tests pass a dict).
- Flask-SocketIO 5.3.6 doesn't serve a client bundle at `/socket.io/socket.io.js` (400).
  Live updates = 4s polling; server-side `socketio.emit('state_update')` kept for a future CDN client.
- **`between` is a SQLite keyword** — quoted (`"between"`) in schema, scorer service, and import SQL.
- IDs are `secrets.token_hex(8)`; timestamps are UTC ISO (`datetime.now(timezone.utc).isoformat()`).
- JSON fields stored as text via `db.json_dumps/json_loads`.
- Windows Git Bash: POSIX commands only; `rm -f data/*.db*` also removes WAL/SHM.

## Test before shipping changes

```bash
./.venv/Scripts/python.exe -m pytest tests/ -q
```

## Remaining increments (in PLAN.md)

1. ✅ **Finances + Vault wiring — built.**
2. ✅ **Offline scorer — built.**
3. ✅ **Admin dashboard consolidation — built.** `/admin` = Overview + shared tab shell
   (Auction `/admin/auction`, Scorer, Finances, Wagers, Link). Plan: `ADMIN_DASHBOARD_PLAN.md`.
4. **Individual player/manager dashboard** (user's next increment) — wagers, funds control,
   other player actions; `/manager` + `/account` today are minimal.
5. **Wager polish** — socket live updates, auto-resolve markets from match results (registry exists now).
6. **Fantasy entries (optional)** — S1 data for 17 fantasy teams exists but no schema; only
   per-player fantasy points feed leaderboards today.
7. **Ball-by-ball match view** (nice-to-have, now possible: `delivery_log` is stored).

## Handoff state at session end

- **85/85 tests green** (79 + 6 new admin-dashboard tests). E2E verified: all six admin pages
  render with the tab shell and the correct active tab; `/admin` overview shows real S1
  numbers (4 teams, 13 registry, 13 finalized, wallets 0 post-reset); auction POSTs redirect
  to `/admin/auction`; public pages have no tab leakage.
- ⚠ **Real `data/scl.db` admin password is NOT `admin123`** (set at last bootstrap's env) —
  for admin-page browser checks use the real password; automated tests use temp DBs.
- Balances remain reset to 0 (new economy), history intact.
- ⚠ Restart the stale server on port 10001 (old code) before browser-verifying new routes.
- Git: repo at `SCL-official`, one commit per milestone (user request).
- `PLAN.md` + `MEMORY.md` + this file are the source of truth; per-increment plans in
  `PROD_IMPORT_PLAN.md`, `WAGER_PLAN.md`, `MATCHES_PLAN.md`, `FINANCES_PLAN.md`,
  `OFFLINE_SCORER_PLAN.md`, `ADMIN_DASHBOARD_PLAN.md`.
