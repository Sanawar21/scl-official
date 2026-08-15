# SCL Rebuild — Session Resume Handoff

Read this first in a new session, then `MEMORY.md` (living context + gotchas) and `PLAN.md`
(feature plan). Last updated 2026-08-15 at the end of the session that built the finances/vault
increment (and initialized the git repo).

## Quickstart

```bash
./.venv/Scripts/python.exe run.py          # start server (debug, port 10001)
# or without reloader:
./.venv/Scripts/python.exe -c "from app import create_app, socketio; app = create_app(); socketio.run(app, host='0.0.0.0', port=10001, debug=False, use_reloader=False)"
./.venv/Scripts/python.exe -m pytest tests/ -q   # 72 tests
```

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
                            summaries, profiles
    finance_service.py      season finance: match rewards, adjust/transfer, ledger, undo,
                            process_pending, credit hint (wallet == team purse)
  routes/
    auth.py                 /auth/login|logout|signup, /auth/admin/link...
    admin.py                /admin control room + bank adjust + /admin/finances
    manager.py              /manager dashboard + bid|pass|trade JSON
    viewer.py               /, /live, /api/state, /season/<slug>
    banking.py              /account (balances, vault lock, reinvest, deposit)
    wagers.py               /wagers board|detail|admin
    matches.py              /matches, /table, /leaderboards, /teams, /players, /admin/scorer,
                            /finances[/<season>]
  templates/                base.html + viewer/, admin/, manager/, auth/, banking/, wagers/, matches/,
                            teams/, players/
  static/css/app.css        dark mobile-first theme (.card/.table-wrap/.tag/.chip/.feed/.grid…)
  static/js/app.js          live board via 4s polling of /api/state
tests/                      conftest.py (_setup helper) + test_auction (16), test_bank (4),
                            test_wager (15), test_matches (17), test_finance (21) — 72 total
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

1. ✅ **Finances + Vault wiring — built.** Auto match rewards + yield on finalization,
   M12 unlock, ledger + undo, S1 `--phase finance` import, `/admin/finances` + public
   `/finances[/<season>]`. Plan: `FINANCES_PLAN.md`.
2. **Offline scorer** — port `../SCL/scorer.html` (standalone mobile HTML → ball-by-ball CSV →
   admin import) and `scoreCard.py` (CSV → PDF scorecard).
3. **Admin dashboard consolidation** — admin pages split across auction / wagers / scorer / link;
   no single overview.
4. **Wager polish** — socket live updates, auto-resolve markets from match results (registry exists now).
5. **Fantasy entries (optional)** — S1 data for 17 fantasy teams exists but no schema; only
   per-player fantasy points feed leaderboards today.

## Handoff state at session end

- 72/72 tests green; E2E verified: full `--phase all` import clean (league table matches old
  aggregates; finance cross-check clean), manager wallets == final purses
  (2285/1145/2885/1365), `/finances`, `/finances/season-1`, `/admin/finances` all render with
  the 44-row S1 ledger, adjust/transfer/undo flows work against a DB copy.
- **Balances reset to 0** on the real `data/scl.db` (user: new economic system; everyone starts
  at 0, grants come later). History kept (44 ledger rows + `balance_reset` transactions).
  Tool: `scripts/reset_balances.py` (dry-run default, `--yes` writes).
- Git: repo initialized at `SCL-official`, commits made per increment — commit after each
  milestone from now on (user request).
- ⚠ Restart the stale server on port 10001 (old code) before browser-verifying new routes.
- `PLAN.md` + `MEMORY.md` + this file are the source of truth; per-increment plans in
  `PROD_IMPORT_PLAN.md`, `WAGER_PLAN.md`, `MATCHES_PLAN.md`, `FINANCES_PLAN.md`.
