# SCL Rebuild — Memory / Reference (living doc)

Purpose: everything I've learned about this project, so future work doesn't rediscover it.
Update this file whenever you learn something durable. Keep it current as the build progresses.

> New session? Start with `RESUME.md` (handoff/quickstart), then this file, then `PLAN.md`.

## What this is

- **Section-C Cricket League (SCL)** platform rebuild. Small cricket league:
  4+ teams of 4 players each; the **manager owns the team and is one of the 4 players**.
  Manager sits in the auction and buys players. A player one season can be a manager the next.
  Season rules are **fluid** — sometimes a final/relegation match, sometimes table decides.
  League table = **12 matches, double round robin**.
- **Rebuild home**: this repo — `D:\projects\SCL-official` (project root; contains only the 3 PDFs,
  PLAN.md, MEMORY.md, and `.txt` extracts of the PDFs).
- **Reference implementation**: `D:\projects\SCL` (`../SCL` from root) — Flask + TinyDB + SocketIO.
  Its auction logic is solid and should be **ported, not rewritten from scratch**.

## Stack (decided with user)

- Flask + SQLite (stdlib `sqlite3`, WAL, thread-safe connection-per-op with a lock — mirrors the
  existing `LockedTinyDB` pattern) + Flask-SocketIO live updates with polling fallback.
- Server-rendered Jinja templates, mobile-first. No ORM, minimal deps (project's style).
- Reference app deps: Flask, Flask-SocketIO, tinydb, python-socketio, Werkzeug (see `../SCL/requirements.txt`).

## Source documents (in project root; NOT final — user said keep in mind)

- `SCL_Season_2_Official_Rulebook.pdf` → `rulebook.txt`
- `SCL_Season_2_Vault_Guide_Updated (1).pdf` → `vault.txt`
- `SCL_Wager_Risk_Management_Protocol.pdf` → `wager.txt`
- Extract with: `/mingw64/bin/pdftotext -layout file.pdf -` (pdftotext installed on this machine).

## Domain rules (from docs + user)

- Season 2 format: **final abolished**; champion = table topper after 12-match double round robin.
  Points 2 win / 1 draw / 0 loss. Tie-breakers: 1) NRR, 2) Head-to-head, 3) Total boundaries.
- Auction order S2: **Platinum → Gold → 5-min trade break → Silver** → then Phase B.
  ⚠ Conflict: current app does Silver+Gold → break → Platinum. Resolved via **configurable
  per-season ruleset**, default = Platinum-first (user confirmed).
- Credits: **8 per team**; manager tier deducts (Platinum 3 / Gold 2 / Silver 1); each team must buy
  **3 players** (roster = 4 incl. manager).
- S2 economy: purses P 9000 / G 10000 / S 11000; base prices P 3000 / G 2000 / S 1000;
  unspent-credit refund 1000/credit (S1 was 500); Phase B flat 200; bid increment +50;
  tickets: SCL viewer 100, outsider 150.
- Vault: deposit converts **Liquid Cash → Locked Capital**; **7% yield per match**;
  **default = compounding** (auto-reinvest); optional **manual harvest** (7% on initial principal only);
  principal locked until end of Match 12; no emergency withdrawals. 2000 @ 7% compound → 4504 after M12.
- Wager: pooled **Yes/No AMM**; lifecycle = proposal → admin probability calibration → financial
  veto → peer phase → house injection → resolution (winning side splits pot proportionally).
  House guarantees payouts when peer interest is insufficient. Voided/ambiguous → 100% refund.
  Frozen pools on mid-wager news. Bankruptcy veto: admins cap/cancel wagers threatening solvency.
- Discipline (S2): match boost 300 PKR (+40% ticket revenue), umpire quota 3/mgr, field-invasion
  fine 500 PKR, Kela protocol (strip manager authority for recurring disputes).

## User's architecture requirements (round 2 — must be in the plan)

1. Players can be managers. Admin adds/modifies players **before** an auction.
   **Manager tier = his player profile tier** (drives purse + credit deduction). Credits system unchanged.
2. **Post-auction transfer window**: after auction, **admin has full control** over player transfers.
3. **Everything undoable from admin**: full action log; e.g. undo an accidental manager bid.
4. **Central banking system**: every player has an account; can wager or hold in **Vault**
   (constant yield is the default mode).
5. **Self signup + linking**: player signs up with personal username; **admin later links** that
   account to an existing SCL player.
6. **Pre-auction gifts**: admin can gift teams an amount before the auction to balance skill gaps
   (e.g. a Platinum manager saves money not buying a platinum player; a Silver manager needs it).
7. **Admin takeover of fumbling teams** (added round 3): if a manager fumbles in the auction
   (no-show/bad bids/violations), admins take over the team and balance it by buying/selling on
   its behalf. Explicit + logged (reason, admin, time), reversible; manager loses bid rights while
   taken over; ties into the Kela protocol from the rulebook.

## Reference files in ../SCL to port (with their role)

- `app/services/auction_service.py` — bid/purse/credit validation, close lot, step-back undo,
  Phase B gating, completion+penalties, trades with cash. **Core to port.**
- `app/rules.py` — tier/purse/base/credit constants + phases → becomes the **ruleset model**.
- `app/routes/admin.py` — auction control room, finances adjust, scorer import, wagers, publish.
- `app/routes/manager.py` — bid/pass/trade endpoints; `app/routes/viewer.py` — live board + published.
- `app/services/auth_service.py` — session auth (admin/manager) + seed admin (admin/admin123).
- `app/services/economy_service.py` — wager scenarios (yes/no pools, house injection, resolution).
- `app/services/scorer_service.py` — scorer config render, CSV import, match registry, league tables,
  leaderboards, finances, fantasy — for later increments.
- `scorer.html` — standalone offline scorer (Jinja-rendered, mobile-first, exports ball-by-ball CSV).
- `scoreCard.py` — parses scorer CSV, builds PDF scorecard (fantasy engine).
- Data format note: TinyDB JSON files store tables as `{table: {"_default": {doc_id: doc}}}`.

## Environment notes

- Windows, but terminal is **bash (Git Bash)**: use POSIX commands (`mv`, `rm`, forward slashes).
- Python venv exists at `../SCL/.venv` (`./.venv/Scripts/python.exe`).
- Chrome installed (can verify UI in browser when needed).
- Project root has no git repo yet (git context empty). Consider `git init` when we start building.

## Progress

- [x] Context gathered (existing app + 3 PDFs); PLAN.md + MEMORY.md maintained
- [x] **Increment 1 (auction) built and tested** — 19 pytest tests pass; page-render + full manager-flow E2E verified
- [x] **Prod import (Phase 1)** — `scripts/import_prod.py` imports Season 1 (players/teams/managers/bids/snapshot) from `prod-data/` into `data/scl.db`; plan in `PROD_IMPORT_PLAN.md`
- [x] **Increment 5 (wager platform) built and tested** — 15 new tests (34 total); plan in `WAGER_PLAN.md`
- [x] **Matches/stats built and tested** — 17 new tests (51 total); plan in `MATCHES_PLAN.md`; S1 scorer data imported via `--phase stats`
- [x] **Finances + Vault wiring built and tested** — 21 new tests (72 total); plan in `FINANCES_PLAN.md`; S1 finance ledger imported via `--phase finance`
- [x] **Git repo initialized** (`git init` + regular commits), per user request (2026-08-15)

## Increment 1 — what was built (structure)

- `app/` — Flask app factory (`__init__.py`), `config.py` (env-overridable), `db.py` (thread-safe
  SQLite, WAL, JSON helpers), `schema.py` (SQL schema), `rules.py` (S2 defaults), `ruleset.py`
  (per-season Ruleset model + phase flow), `authz.py` (login_required)
- `app/services/` — `auction_service.py` (full auction engine + undo handlers), `auth_service.py`
  (admin seed admin/admin123, signup, linking, manager assignment), `bank_service.py` (accounts,
  ledger, vault 7%/match compound/manual)
- `app/routes/` — `admin.py` (control room: setup, phases, gifts, takeover, transfers, undo, publish,
  bank adjust), `manager.py` (bid/pass/trade JSON), `viewer.py` (home, live, published, /api/state),
  `auth.py` (login/logout/signup/link), `banking.py` (account + vault)
- `app/templates/` + `app/static/` — mobile-first UI; `js/app.js` renders live board from JSON
- `tests/` — `conftest.py` (`_setup` helper), `test_auction.py` (16 tests), `test_bank.py` (4 tests)

## Matches & stats — what was built (Increment: matches)

- 5 tables (`match_registry`, `match_stats`, `match_team_stats`, `match_player_stats` + indexes) and
  `teams.global_team_id` (with a **bootstrap migration** in `db.bootstrap()` — first entry in
  `_MIGRATIONS`, pattern for future columns on existing tables).
- `app/services/scorer_service.py` (`ScorerService(db)`): registry CRUD, walkover synthesis
  (winner/team rows from `between`+`winner`), ball-by-ball CSV import (28 required columns,
  local→global id mapping via `players.global_player_id`/`teams.global_team_id` + name fallback,
  optional **Substitution Log** section, overwrite-confirm via `MatchOverwriteConfirmationRequired`,
  archive to `data/matches/`), undo, on-demand **league table** (points 2/1/0, **NRR computed as
  round(rr_for,2) − round(rr_against,2)** to match the old published S1 numbers, tie-breakers
  points → NRR → **H2H** → **boundaries**), **leaderboards**, **match summary** (innings batting/
  bowling sections, extras, fantasy), **team/player profiles** (slug `name-<id[:8]>`).
- `app/routes/matches.py` (`matches_bp`): public `/matches[/<season>[/<match>]]`, `/table`,
  `/leaderboards`, `/teams[/<slug>]`, `/players/<slug>`; admin `/admin/scorer` (config, registry
  CRUD, CSV import w/ overwrite-confirm + undo, recent imports). Nav links in `base.html`.
- `scripts/import_prod.py --phase stats`: imports registry (13) + match stats (13, incl. M6
  walkover with team rows — no CSV by design) + team rows (26) + player rows (93) verbatim (they
  already use global ids), backfills `teams.global_team_id` (4), and **cross-checks the recomputed
  league table against the old `scorer_team_global_stats`** (exits 2 on mismatches; clean).
- Fantasy: per-player fantasy points from the ported formula feed the leaderboards; fantasy
  *entries* (the S1 fantasy-game teams) remain out of scope (no schema).

## Finances + Vault — what was built (Increment: finances)

- **Wallet == purse from day one** (user decision): `create_team` opens the manager's player
  account and credits it with the tier purse; every auction money move (gift, close, transfer,
  trade cash, complete-draft penalty, step-back refund, delete-team zero) also adjusts the wallet
  in the same write transaction via `bank.adjust(..., conn=conn)` — the two can never drift.
  Undo handlers reverse the bank side too. Consequence (accepted): the wallet is spendable any
  time, so wagers/vault-locks mid-auction shrink the bidding budget; bids/close fail on
  `Insufficient liquid cash`.
- **Auto-finance on match finalization** (`app/services/finance_service.py`): `on_match_finalized`
  pays both playing teams the ruleset `match_reward_amount` (S2 default 200) + catches up vault
  yield through the current finalized-match count (cap 12). Hooked into the admin scorer import +
  walkover routes (not the scorer service). `process_pending` is the backfill button.
- **Yield loop fix (correctness)**: `apply_match_yield` now uses running totals per step so
  compounding builds 2000→2140→2290→2450→2622 (previously it re-read the stale row and applied
  7% of the original principal each step → 2560). The docs' table proved the fix.
- **M12 unlock**: `bank.unlock_vault(season_id, force=False)` moves locked→liquid, flags the
  position, logs `vault_unlock`; guarded by ≥12 finalized matches (force checkbox bypasses).
- **Finance ledger** `season_finance_entries`: match rewards, adjusts (fines/umpire duty),
  transfers (from/to + both wallets), one-step undo (reverses delta, marks `undone_at`,
  no cascade). Written atomically with `bank_transactions`.
- **No credit-refund feature** (user decision): admin uses the existing bank adjust + comment;
  `/admin/finances` shows a per-team `credits_remaining × rate` **hint table**.
- **Routes**: `/admin/finances` (adjust/transfer forms, process-pending, yield, unlock, ledger +
  undo, credit hint); public `/finances[/<season>]` (Budget Board + ledger, nav link); `/account`
  shows per-season match progress + unlocked badge + "your team == wallet" note.
- **Import Phase 3** (`--phase finance`): 44 ledger rows verbatim + purse-chain cross-check
  (exit 2 on drift; final purses 2285/1145/2885/1365) + wallet seed (`purse` tx "Season 1 final
  purse"). `--phase all` = core + stats + finance.

## Wager platform — what was built (Increment 5)

- `wagers` + `wager_bets` tables; `app/services/wager_service.py`; `app/routes/wagers.py`; templates
  `wagers/board|detail|admin.html`; nav link; registered in `create_app`.
- Lifecycle: propose+first stake → calibrate (blind estimates, consensus = average) → finalize
  (opens betting) → peer bets → house inject → resolve | void; veto = pre-open bankruptcy gate.
- Payout (doc-faithful): winners split the pot **proportionally**; if pot < `Σ stake × 100/p(side)`
  the House tops up the difference (solvency-checked against the house account). Void/veto refund 100%.
- House = `bank_accounts` row (`house:house`), topped up via admin bank adjust.
- Stakes/payouts/refunds go through `bank_transactions` (`wager_stake/payout/refund`, `house_inject`).

## Gotchas learned (don't rediscover)

- **Never return via a separate read connection inside `db.write()`** — uncommitted rows are
  invisible to other connections (WAL). All returns must be outside the `with self.db.write()` block.
  (create_season/previous_player/bank adjust/lock had this bug; fixed.)
- **Never open a second write transaction inside `db.write()`** — a nested `BEGIN IMMEDIATE` on a
  fresh connection blocks on the first connection's write lock → deadlock (30s timeout). To move
  money atomically with another table's write, pass the caller's `conn` into
  `bank_service.adjust(..., conn=conn)` / `get_or_create_account(..., conn=conn)` (added for the
  wager service; `conn=None` keeps old behavior).
- **S1 finance purses are history, not a replay target** — the 44 transactions end at
  purse_remaining per team, but mid-season purses go negative; wallets are **seeded** with the
  final purse, never replayed through `bank.adjust` (that would overdraft-raise).
- **Transfer rows carry the purse on both sides** (`from_after_purse`/`to_after_purse`); a
  team's *last* ledger row may be a transfer, so the terminal-value cross-check must track all
  three fields (the original check only looked at `after_purse` on `team_id` rows and wrongly
  flagged Naan CC 2795 vs 1145).
- **Import cross-checks exit 2, not 1** — `main()` returns 2 for data drift (SystemExit from a
  string is 1, which is a script error). When re-verifying, rebuild fresh: `rm data/scl.db &&
  --phase all` (a failed run still commits its writes, so `--force` re-runs duplicate rows).
- **`apply_match_yield` must compound within one call** — running totals per step; re-reading the
  position row between steps (or reading `position["locked_capital"]` from the fetched row)
  silently breaks compounding.
- **`process_pending` returns only matches where something happened** (reward or yield applied) so
  the backfill button shows meaningful counts and re-runs report nothing.
- `app.config.from_object(dict)` does NOT work — dicts must go through `app.config.update()`.
- Flask-SocketIO 5.3.6 does NOT serve a client bundle at `/socket.io/socket.io.js` (400). Live
  updates therefore use **4s polling** (`app.js` `startLive`/`startManager`); server-side
  `socketio.emit('state_update')` is kept for a future CDN client.
- SQLite + Python: use `PRAGMA foreign_keys=ON` per connection; JSON fields stored as text.
- Windows bash: use POSIX commands; `rm -f data/*.db*` to clear WAL+SHM too.

## Run / verify

- `./.venv/Scripts/python.exe run.py` → http://127.0.0.1:10001 (admin/admin123)
- Tests: `./.venv/Scripts/python.exe -m pytest tests/ -q`
- DB: `data/scl.db` by default; override with `SCL_DB_PATH` (also `SCL_SECRET_KEY`, `SCL_ADMIN_USERNAME/PASSWORD`)
- Note: `python run.py` runs with debug/reloader (reference-app style); `emit_state` needs the
  app context of the running socketio server to broadcast (routes call it after mutations).

## Balance reset (2026-08-15)

- User reset team account balances to 0 to bring in the new economic system ("every player gets
  an amount later; managers may get more for the auction"). Applied to `data/scl.db`.
- Tool: `scripts/reset_balances.py` (dry-run by default, `--yes` writes). Zeroes wallet + purse in
  lockstep per team, keeps all history, appends a `balance_reset` bank_transaction per account.
- ⚠ When granting auction money later, the **purse must move in lockstep with the wallet**
  (wallet-only bank adjust leaves `purse_remaining` at 0 → bids fail on "Not enough purse").

## Still to build (later increments)

- Offline scorer (downloadable HTML → CSV → admin import; port `scorer.html`/`scoreCard.py`)
- Wager polish: socket updates, auto-resolve from match results; fantasy entries (S1 data exists, no schema)
- Matches polish: `between` is a SQL keyword — quoted in schema/service/import; keep that in mind if adding columns
