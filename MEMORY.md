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
- [x] **Offline scorer + scorecard PDF built and tested** — 7 new tests (79 total); plan in `OFFLINE_SCORER_PLAN.md`; `/scorer`, call-up batting order, DB-fed PDF (reportlab)
- [x] **Admin dashboard consolidation built and tested** — 6 new tests (85 total); plan in `ADMIN_DASHBOARD_PLAN.md`; `/admin` = overview + shared tab shell
- [x] **Git repo initialized** (`git init` + regular commits), per user request (2026-08-15)
- [x] **Frontend transformation Phase 0 built** — Playwright e2e infra + 13 baseline smoke tests
  (98 total) locking the CURRENT flows before the redesign; plan in `FRONTEND_PLAN.md`
  (decisions: pytest-playwright, light theme, mobile-first, data-parity guaranteed)
- [x] **Frontend transformation Phase 1 built** — shell + design system (light theme), role-aware
  nav + mobile drawer + bottom bar, flash→toast, Home landing dashboard (latest results from the
  match registry), auth card flows — 13 new e2e tests (111 total)
- [x] **Frontend transformation Phase 2 built** — public surfaces restyled: live board (phase
  stepper + budget cards + table toggle), published season (squads cards + filterable player
  table), matches index (result cards) + scorecard summary (FOW line, result banner, PDF action),
  league table (zone highlighting, expandable for/against), tabbed leaderboards with podium,
  team/player profiles (stat tiles), public finances (budget cards + ledger icon feed) —
  12 new e2e tests (123 total)
- [x] **Frontend transformation Phase 3 built** — player/manager surfaces: account page (balance
  hero tiles, link-status banner, vault position cards, filterable transactions), wagers board
  (market cards with pool bars + fair odds, collapsible propose flow) + detail (pool visual with
  percents, live "you'd win X" stake preview), manager dashboard (team hub stat row, squad XI/
  bench, bid action bar) — 11 new e2e tests (134 total)
- [x] **Frontend transformation Phase 4 built** — admin polish: overview status cards as stat
  tiles with primary link buttons, labeled bank-adjust + phase forms, wager lifecycle steppers
  on the wagers admin, link-page empty state — 7 new e2e tests (141 total)
- [x] **Frontend transformation Phase 5 — COMPLETE** — final suite green (141: 85 unit + 56 e2e),
  mobile sweep of every page per role passed; plan `FRONTEND_PLAN.md` marked done. The redesign
  touched every template except the offline scorer.
- [x] **S2 economy restructure (5 increments)** — `global_teams` persistence, no tier purse,
  idempotent 10k universal funding (button + `scripts/fund_players.py`), 250/match credit to
  every wallet + `auto_vault` toggle on `/account`, squad-cost levy at draft end + three-section
  balance board; plan `ECONOMY_PLAN.md`, commits `6841556`..`efc1336`
- [x] **Auction lifecycle e2e suite** — `tests/e2e/test_auction_lifecycle.py`: 8 tests on an
  ISOLATED per-test server (own app + temp DB), driving the full draft through the browser
  (phase set → nominate → manager bid/pass → close → complete/undo → wallet assertions).
  Found + fixed: the e2e seed was overwriting `global_team_id` with random ids (silently
  duplicating teams on the board). Suite now 191 (116 unit + 75 e2e).
- [x] **Deposit removed + automatic house guarantee** (2026-08-16): players have NO deposit
  form — admin bank adjust is the only way to add balance (positive grants now go through
  `credit()`, so auto accounts route them to the vault; fines stay liquid). Wagers show the
  **live house coverage per side** ("House covers: Yes win → N · No win → M") on board,
  detail + admin pages, computed by `wager_service.house_coverage()` and polled every 4s
  via new `/wagers/live` + `/wagers/<id>/live` JSON endpoints. Suite now 193.
- [x] **Participant documents drafted (2026-08-16)** — `docs/SCL_BRAND_PROMPT.md` (paste-into-
  nano-banana branding prompt), `docs/SCL_RULEBOOK.md`, `docs/SCL_VAULT_GUIDE.md`,
  `docs/SCL_WAGERS_GUIDE.md`, `docs/SCL_ECONOMY_GUIDE.md`. New rules added per user: umpiring
  quota 3 matches else 1,500 fine at season end; Substitution Release Clause = 50% of the
  player's auction price; 500 fine to the manager whose player invades the field; 200 for
  sponsored match announcements (promote/trash talk). MDs for review → PDFs later.
- [x] **Admin can remove single bets** (2026-08-16) — `wager_service.remove_bet(wager_id,
  bet_id, actor)`: admin-only, open bets only, pre-resolution only (proposed/calibrating/
  vetted/frozen); stake refunded 100% to the bettor's liquid cash, bet marked `refunded`
  (kept in history), pools/pot/house coverage recompute live. Admin UI: bets table with
  `Remove bet` buttons on `/wagers/admin` (open count header). Route:
  `POST /wagers/admin/<id>/bets/<bet_id>/remove`. Suite now 197 (120 unit + 77 e2e).
- [x] **Participant PDFs + website docs + changelog** (2026-08-16) — `scripts/generate_docs.py`
  builds the four docs into PDFs at `app/static/docs/*.pdf` via new
  `app/services/doc_service.py` (markdown → HTML for the site + reportlab PDF renderer;
  `md_to_html`/`md_to_pdf`/DOCS registry). Website: public `/docs` index, `/docs/<slug>`
  rendered view, `/docs/<slug>/pdf` download; `/changelog` public audit trail page +
  `/admin/changelog` (add/delete entries, markdown bodies) backed by new `changelog` table
  + `ChangelogService`. Nav links Docs + Changelog; admin tab Changelog; demo + e2e seeds
  include sample entries. HARDENED the flaky vault-lock e2e (wait for the reloaded
  transaction row instead of a pre-reload marker). Suite now 206 (126 unit + 80 e2e).

## Participant docs — source of truth mapping
- Rulebook/vault/wager sources are `rulebook.txt`, `vault.txt`, `wager.txt` in the repo root
  (S2 rulebook PDFs too). Platform numbers that override them: no tier purse (universal 10k),
  match reward 250 to EVERY player, credit refund 1000, bid increment 50, vault 7%/match cap
  12, roster = manager + 3, squad-cost levy on non-spenders at draft end.

## E2E gotchas (auction driving)
- The manager dashboard has NO `#bid-feed` (that's the live board) — bid feedback appears in
  `#current-lot` ("Current bid: N") after the 4s state poll.
- The custom "Bid" button is the `+` adjacent sibling of the custom input; `~` also matches Pass.
- Pass is never disabled (only the Bid buttons are) — don't assert all buttons disabled.
- The admin helpers must log in as admin first (`/admin/auction` bounces unauthenticated).
- Nomination order is `rowid` within tier — Alpha/Beta/Gamma/Delta/Epsilon/Zeta for the seed lots.
- Manager players are marked `sold` to their own team in the seed so lots stay nominatable.

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

## Offline scorer + scorecard PDF — what was built (Increment: offline scorer)

- **Offline scorer page** (`app/templates/scorer/scorer.html`, port of `../SCL/scorer.html`):
  public `/scorer` (in-browser scoring) + `/scorer/download` (standalone `scorer-v<version>.html`,
  fully offline). Payload from `ScorerService.build_scorer_context()`: **local** team/player ids
  (round-trip through the import's local→global maps), manager added to every roster (S1
  managers play — they're NOT in `teams.players` JSON), plus `matches` from the match registry
  so the setup screen pre-fills. Season = config.season_slug, else latest season with teams.
- **Call-up batting order fix** (user bug report: "batsmen weren't in call-up order"): the
  scorer's CSV exports a `Batter Order` column that was never read. Now imported into
  `match_player_stats.batter_order` (min per player; innings-start non-striker who never faces
  → 2; opener striker w/o data → 1; no-column fallback = first-appearance order).
  `match_summary` sorts batting by it → summary page AND PDF both fixed. S1's 13 matches have
  no ball-by-ball → keep old ordering (go-forward fix for Season 2).
- **`match_stats.delivery_log`** (JSON TEXT column, migration in `_MIGRATIONS`): the parsed
  ball-by-ball rows, written at import (walkover = `[]`). Powers the PDF's Fall of Wickets and
  future ball-by-ball views.
- **Scorecard PDF from the DB** (`app/services/scorecard_service.py`, reportlab 5.0.0): fed by
  `match_summary` + `delivery_log` (FOW) + `season_finance_entries` for the match (revenue
  section = real rewards/adjustments, not scoreCard.py's hardcoded S1 REVENUE). Route
  `/matches/<season>/<match>/scorecard` (public, per user decision), linked from match summary
  + admin scorer. reportlab added to `requirements.txt` + installed.

## Admin dashboard — what was built (Increment: admin consolidation)

- **`/admin` is now the Overview** (status cards: Auction/Matches/Finances/Wagers/Accounts +
  recent activity feed); the auction control room moved to **`/admin/auction`**.
- **Shared tab shell** (`app/templates/admin/_tabs.html`, `{% from ... import tabs %}`):
  Overview · Auction · Scorer · Finances · Wagers · Link, rendered on all six admin pages
  with the active tab highlighted (`active` param passed literally per template — no route
  context changes needed). Tabs link to the existing URLs, so every form POST → redirect
  keeps working (auction actions redirect to `/admin/auction` now).
- Overview context (`_overview_context()` in admin.py): reuses services (auction state,
  scorer registry/imports, finance board/entries, wager list/house, auth unlinked) + a few
  direct queries (`finalized_count`, `pending_finance` = finalized matches without a
  `match_reward` entry, `vault_positions`, `yield_progress` capped at 12).
- base.html admin nav shrunk to a single **Admin** link (→ overview); the tabs expose the rest.

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

- **Stats rows may reference either a team's per-season id or its global id**
  (imported S1 stats use the global id; live scorer rows use the per-season id).
  `league_table` maps names by BOTH (`team_names.setdefault(gid, ...)` + `t["id"]`);
  `finance._resolve_match_teams` matches `id = ? OR global_team_id = ?`. When
  touching team-id lookups, cover both (2026-08-16, Inc 1 global teams).
- **Playwright `inner_text` excludes input/textarea values** — assert on
  `page.input_value(...)` for form fields (2026-08-16).
- **The DB-path env var is `SCL_DB_PATH`** (app/config.py), NOT `SCL_DB` — setting `SCL_DB`
  silently falls back to `data/scl.db` and a scratch script can **write into the real DB**.
  (2026-08-15: an e2e-seed scratch run polluted `data/scl.db` with a Test Season + users +
  wager + M1; cleaned manually — row ids backed up in `data/scl.db.pre-cleanup.bak` before
  the DELETEs. For scratch scripts always pass `DB_PATH` via `create_app({"DB_PATH": ...})`
  like tests/e2e/conftest does.)
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
- **Offline scorer payload uses LOCAL ids** — that's what makes the CSV round-trip work: the
  import's `_identity_maps` resolve local→global (then name fallback). The manager is not in
  `teams.players` JSON (only the 3 bought players) but DOES play — rosters must add the manager
  via `players.global_player_id = teams.manager_player_id`.
- **`batter_order` is nullable** — S1 rows are NULL (aggregated import, no ball-by-ball). Any
  sort/display must handle None (match_summary puts them last; template shows '—').
- **`match_stats.delivery_log` is nullable too** — `json_loads(row.get("delivery_log"), [])`
  and old matches just omit the FOW line.
- **PDF byte check is `pdf[:4] == b"%PDF"`**, not `[:5]` — reportlab writes `%PDF-1.4`.
- reportlab 5.0.0 is a new major; platypus Table/Paragraph APIs used here are stable.
- When resolving a team's "global" reference for the scorer prefill, map BOTH
  `global_team_id → local id` AND `local id → local id` — teams without a global id are
  referenced by their local id in the registry.
- **The real `data/scl.db` admin password is NOT `admin123`** (set at the last bootstrap's
  env, not the default) — admin-flow E2E against the real DB needs the real password; tests
  use temp DBs where admin/admin123 is seeded fresh. Verify admin contexts against real data
  via `_overview_context()` under `test_request_context()` instead of HTTP login.
- `pending_finance` on the overview = finalized matches WITHOUT a `match_reward` entry. For
  S1 that's all 13 (its 44 ledger rows were imported as history, not through
  `on_match_finalized`) — clicking "Process pending" on season-1 WOULD post 200×2 rewards
  per match (the admin's call).
- `yield_progress` in the overview is capped at 12 to match `MAX_YIELD_MATCH`.
- The tab shell links to `url_for('admin.auction', season=...)` etc. — passing an Undefined
  season_id into url_for breaks, so templates without a season pass `tabs('wagers')` with no
  season arg (the macro defaults to '').
- **Playwright e2e gotchas** (Phase 0):
  - CSS uppercases headings (`text-transform: uppercase` in app.css) — assert on
    `body.inner_text().lower()` or you'll chase phantom misses like `Vault positions` vs
    `VAULT POSITIONS`.
  - Legacy `page.fill('input[name=...]')` targets the FIRST match (non-strict) — scope
    selectors (`section#bank input[name='amount']`) when a page has several same-named fields
    (team-gift forms + bank adjust all use `name="amount"`).
  - JSON endpoints return compact bodies — check `'"ok":true'`, not `'"ok": true'`.
  - `page.goto` on a download URL raises "Download is starting" — use
    `page.request.get(...)` and assert content-disposition/type instead.
  - e2e server: `socketio.run(..., allow_unsafe_werkzeug=True)` is REQUIRED (Flask-SocketIO
    5.3.6 raises without it); boot in a daemon thread on a free port, poll `/` for readiness.
  - The e2e seed reuses service calls, not HTTP: `auction.create_team` → `auth.assign_manager`
    requires the user's player to BE the team's manager — pick manager users accordingly.
  - The open mobile drawer **covers the right side of the viewport** — clicking the backdrop's
    center hits the drawer (pointer-events). Click at the visible left edge:
    `page.locator('#drawer-backdrop').click(position={'x': 10, 'y': 400})`.
- **Phase 1 design system**: light theme tokens live in `app.css` `:root`; every legacy class
  still works (cards/tags/chips/feeds/team-box etc. were restyled, not removed). Flash messages
  now render as auto-dismissing `.toast` elements (`app.js` `initToasts`, 5s); legacy `.flash`
  styles kept for safety. Home route (`viewer.home`) now builds `latest_results` by calling
  `scorer.match_summary` per finalized match (capped at 4, newest first) — cheap at S1 scale.
- `url_for` must be imported in `viewer.py` (added when Home gained result links).
- **e2e seed now includes a finalized match (M1) + published snapshot** — `_seed_match` imports a
  real CSV through `scorer.import_match_csv` (needs local team/player ids in the CSV and
  `Valid Ball?` = "Yes", plus teams given `global_team_id` since `create_team` leaves it NULL).
  `auction.publish()` creates the snapshot for `/season/<slug>`.
- **`match_summary` now derives Fall of Wickets** from `delivery_log` (same logic as the PDF
  service); S1 matches have no delivery_log so they just omit the line.
- Leaderboard tabs are pure-CSS radio inputs; tests switch tabs by clicking `label[for='lb-…']`.
- The `.stat-label`/`.card h3`/`th` text-transform: uppercase applies in templates too — e2e
  assertions on those labels must lowercase the body (e.g. "PLAYED" not "Played").
- **Manager dashboard was broken for real data** (pre-existing): `manager.dashboard` passed the raw
  `_get_team` result, which has no `player_labels`/`bench_labels` — fixed in Phase 3 by resolving
  the ENRICHED team from `state.teams` instead. The template's "Current Lot" heading is
  lowercase "Current lot".
- **`app.js` bid-affordability was broken by the purse removal**: `renderManagerControls` read
  `team.purse_remaining` (gone) — `undefined < minBid` is always false, so unaffordable bids were
  never disabled. Fixed to use `team.wallet`. If you ever touch bid UI again, grep for
  `purse_remaining` in app.js.
- The e2e seed's wager is **calibrated + finalized to `vetted`** (house p(No)=60% → Yes fair 2.5x,
  No fair 1.67x) so the stake flow and "you'd win" preview are testable. Alice's seed balance is
  4500 (5000 − 500 opening stake); the test asserting 4500 depends on that.
- **Admin overview markup (Phase 4 restyle)**: cards are now stat tiles — unit tests assert
  `<div class="stat-label">Teams</div><div class="stat-value">2</div>` style markup (no more
  `Teams: <strong>2</strong>`). The House tile on the wagers card only renders when a house
  account exists (`{% if house %}`).
- The wagers admin uses a **lifecycle stepper** (reuses `.stepper` CSS): proposed → calibrating →
  vetted → frozen → resolved (+ a voided terminal step). Current step = `li.current`.
- Phase 1-4 are all committed (0d9db4d → Phase 3; Phase 4 = 141 tests). The redesign touched
  EVERY template except the offline scorer (deliberately standalone).
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
- **`.env` auto-loads at startup** (`app/config.py` `_load_dotenv`, zero deps): comments, quotes,
  `export` prefix supported; real env vars always win over .env. `.env` gitignored,
  `.env.example` the template. Maintenance scripts use explicit `--db` (never `.env`'s path).
- Note: `python run.py` runs with debug/reloader (reference-app style); `emit_state` needs the
  app context of the running socketio server to broadcast (routes call it after mutations).
- **Stale-server gotcha (2026-08-16)**: "broken dashboard" = an OLD python process still
  listening on :10001 (started before schema changes) serving stale code → 500s
  (`KeyError: 'purse_remaining'` in its log) + broken navbar/auth. `run.py` then fails to
  bind (port taken) so the browser keeps hitting the zombie. Fix: find it with
  `netstat -ano | grep :10001`, `taskkill //PID <pid> //F //T` (**use //T — debug/reloader
  spawns child processes that inherit the socket, so killing the parent isn't enough**),
  then start fresh. Also: plain `python` resolves to **Anaconda**
  (`/c/Users/sanaw/anaconda3/python`) — always use `./.venv/Scripts/python.exe`.
- **`.env` gotcha (2026-08-16, 2nd report)**: "changing the env doesn't change the DB"
  was again the stale-server problem — a leftover process kept serving the OLD db because
  the new one couldn't bind :10001. `.env` IS read correctly by a fresh process
  (`Config.DB_PATH`). Now `run.py` pre-checks the port and prints the exact PID/kill
  command + the active DB path at startup, so a port conflict is loud, not silent.
- **Reloader gotcha (2026-08-16, 3rd)**: `run.py` now runs `socketio.run(..., debug=True,
  use_reloader=False)`. The debug reloader spawns child processes that re-run the
  port pre-check, causing a bind race + self-stop on restart (and historically the
  zombie trees). One process, one bind, no restart.
- **Startup**: `./.venv/Scripts/python.exe run.py` prints the active DB path and stays
  up. If it prints "Cannot bind port 10001", kill the old listener first:
  `netstat -ano | grep :10001` → `taskkill //PID <pid> //F //T`.

## Balance reset + purse removal (2026-08-15)

- User reset team account balances to 0 for the new economic system ("every player gets an
  amount later; managers may get more for the auction"). Applied to `data/scl.db`.
- Tool: `scripts/reset_balances.py` (dry-run by default, `--yes` writes). Zeroes each team's
  wallet, keeps all history, appends a `balance_reset` bank_transaction per account.
- **`teams.purse_remaining` dropped** (user: "no need for a purse field; the team purse is what
  the manager has in his wallet"). The **wallet is now the single source of truth** for all team
  money — auctions, transfers, finance board, dashboard all read/write `bank_accounts` directly.
  Granting money = one `bank.adjust`; nothing to keep in lockstep anymore.
- Drop migration lives in `db.bootstrap()` (`_MIGRATIONS`); the import script reads expected
  purses from the **source JSON** (no purse column left in the teams table); `public_budget_board`
  keeps the `purse_remaining` *key* in its payload for snapshot compat, value = wallet balance.

## Still to build (later increments)

- Wager polish: socket updates, auto-resolve from match results; fantasy entries (S1 data exists, no schema)
- Matches polish: `between` is a SQL keyword — quoted in schema/service/import; keep that in mind if adding columns

## Qualification scenarios + NRR predictor — DONE (2026-08-15)

Ported the standalone `scl-nrr-calc` tool (user's Downloads folder) into
`app/services/scenario_service.py` — pure functions fed from `match_team_stats` +
`match_registry` (no pasted JSON). Three surfaces: `/table` "Qualification scenarios" card
(status chip + plain-English requirement per team), the required-margin calculator on
`/table` (JSON endpoint `/table/scenarios/calc`; batting-first min margin + chase max-balls,
direct-clash vs 3rd-party-rival cases), and a per-match "What's at stake" panel on match
summaries. Plan + status: `SCENARIOS_PLAN.md`.

Key mechanics:
- **Top-N is season-aware, no schema change**: `qualify_count` = 2 if the season's registry
  has more entries than the double round-robin count (`teams × (teams-1)`) — S1 has 13 (12 RR
  + the M13 final) → top-2; otherwise 1 (S2: champion = table topper).
- **Remaining fixtures** = unplayed registry entries, capped at 2 per team pair (a 3rd meeting
  like S1's final is a knockout, not a qualification fixture).
- **Walkover** (S1 M6): counts as a win (2 pts) with 0 balls → no NRR change; the
  division-by-zero guards mirror `league_table`'s.
- Margin math uses **exact** run/ball totals (not the 2dp-rounded `nrr_display`); NRR for
  comparisons is computed from raw aggregates.

## Reference docs + demo env — DONE (2026-08-15)

- `docs/ADMIN_REFERENCE.md`, `docs/PLAYER_MANAGER_REFERENCE.md`, `docs/UI_TEST_COVERAGE.md` —
  full role guides + what the 65-test e2e suite covers.
- `scripts/seed_demo.py` — self-serve demo (fresh `data/demo.db`, real DB never touched):
  season, 4 teams, 6 users, a partial auction with a live lot + real bids, wagers across
  the lifecycle, vault positions, one finalized match, published snapshot. Logins all
  `demo123`. Run then `SCL_DB_PATH=data/demo.db python run.py`.
- Demo gotchas: manager players must be marked `sold` to their own team before the auction
  (else `nominate_next` picks them); phases are `phase_a_<tier>`, not `platinum`; team
  credits are per-tier (a platinum manager team starts with 5 credits — 3 spent on a
  platinum buy leaves 2, so later platinum bids fail).

## Admin login stale-password fix — DONE (2026-08-16, +2 unit → 242 tests)

- **Gotcha**: `seed_admin_if_missing` only created the admin if missing, so an
  EXISTING admin row kept its old password while `.env` promised a new one —
  admin login silently failed ("can't login as admin"). `data/scl.db`'s admin
  had the old default `admin` while `.env` said `demo123`.
- **Fix**: the seed now syncs username + password of an existing admin to the
  configured values on every boot — `.env` is authoritative. Also: always use
  `./.venv/Scripts/python.exe` (Anaconda `python` was serving again).

## PDF letterhead + home banner — DONE (2026-08-16, +2 unit +1 e2e → 240 tests)

- **PDF letterhead**: every generated PDF (doc_service docs + scorecard) draws the
  **16:9 SCL logo mark** (`logo-mark-16-9.JPG`, aspect preserved, ~80-85mm wide)
  as a flowable at the top of the FIRST page only; every page gets a slim navy
  running header (title + volt underline). Wide banner was warped as a strip, so
  it was dropped from PDFs. `scripts/generate_docs.py` re-run.
- **Home page `/`**: brand-band hero leads with the SCL wide banner image, then
  the logo mark + title + actions below.

## Branding gaps closed + docs show brand — DONE (2026-08-16, +5 unit +2 e2e → 237 tests)

- **Banners now render everywhere**: manager dashboard and team detail page
  always render the `<img class="team-banner">` (previously guarded by
  `{% if ... banner %}` so the SCL **wide-banner fallback was dead**). Since
  `team_banner()` always resolves (SCL fallback when a team has none), the
  SCL wide banner now shows by default on every team page/dashboard.
- **Docs show the brand**: `doc_service` markdown parser now supports
  `![alt](/branding/...)` image blocks → HTML `<figure class="doc-figure">`
  (site) and reportlab `Image` scaled to fit (PDF, resolves `/branding/`
  to `data/brandings/`; missing images skipped gracefully). Added `_italic_`
  support to `_inline` (rulebook uses it). Rulebook §3.1 now embeds the SCL
  wide banner + logo mark with captions; `scripts/generate_docs.py` re-run.

## Season setup wizard — DONE (2026-08-16, +10 unit → 252 tests)

- **`/admin/season/<id>/setup`** (new **Setup** tab): pick managers + auction
  players from the GLOBAL pool (all players/teams from previous seasons).
  Creating a season now redirects here instead of the auction room.
- **Service** (`auction_service`): `season_setup_context` (every global player
  with team ownership + in_auction/is_manager flags, every global team with
  in_season), `sync_season_setup` (add/remove auction players by global id;
  managers keep their existing team automatically or get a new one; deselected
  managers' teams leave the season), `reassign_team_manager` (setup only; a
  player can't manage two teams).
- **Gotcha**: `_setup` test helper's team dict uses `global_team_id` (the
  per-season `teams.id` ≠ global id) — tests must pass the global id to
  `get_global_team`.

## SCL branding + team branding assets — DONE (2026-08-16, +11 unit +9 e2e → 226 tests)

- **BrandingService** (`app/services/branding_service.py`): SCL asset registry
  (`data/brandings/scl/*`), `team_logo/team_banner` resolution with **SCL
  fallback** when a team has no asset, and upload/remove helpers that store
  files under `data/brandings/teams/<team_id>/`. Stored value is a relative key
  (`teams/<id>/logo.png`) or an external URL; `_resolve_value` turns both into
  servable `/branding/...` URLs.
- **Serving**: `GET /branding/<path>` (viewer blueprint) serves read-only from
  `data/brandings/`, path-traversal safe (404 on `..`/absolute).
- **Schema**: `global_teams.banner` added (migration in `db.py`);
  `update_team_profile` now takes `banner` and only overwrites fields passed
  (None keeps stored value — a name/about edit never wipes uploaded assets).
- **Upload flows**: manager `/account/team/branding` + `/account/team/branding/remove`
  (own team only, 403 otherwise); admin `/admin/teams/<gid>/branding` +
  `.../remove`. Allowed JPG/PNG/WEBP/GIF ≤5MB.
- **Admin Teams panel** (`/admin/teams`, tab **Teams**): list every persistent
  team w/ manager, wallet, seasons + brand assets; create team (name + manager
  player), edit name/about, upload/remove logo+banner, delete team (removes
  profile + season rows + unassigns users; **wallet untouched**).
- **Palette**: CSS `:root` now the SCL brand — navy `#0B1E38` primary,
  `#131822` dark, volt `#A3FF00` accent, `#F7F7F2` bg, slate `#8C939E` muted.
  Navbar is navy with the SCL logo mark; home hero + docs/changelog use a
  `.brand-band`; `.btn-volt`, `.brand-band`, `.team-logo(-sm)`, `.team-banner`
  classes added. PDF accents (doc_service + scorecard_service) → navy + volt
  underline; `scripts/generate_docs.py` re-run.
- **Surfaces with logos**: navbar, auth cards, teams index/detail, league table,
  public finances board, live budget board (JS `public_budget_board.logo_url`),
  manager dashboard, admin auction team boxes, admin overview, admin teams panel.
- **E2E gotcha**: Playwright `set_input_files` needs a dict `{name, mimeType,
  buffer}`, NOT a BytesIO.
- Backlog: nothing branding-related outstanding.

## S2 economy — COMPLETE (2026-08-16, 5 increments, 183 tests)

All five increments shipped: persistent teams, no purse + 10k funding, universal
250/match credit + auto mode, squad levy + three-section board, admin flows +
docs. Reference docs (ADMIN/PLAYER_MANAGER/CLI) updated for the new economy.
Backlog: wager polish (auto-resolve), fantasy entries.

## S2 economy — Increment 4 (squad levy + balance board) DONE (2026-08-16)

- **Squad-cost levy**: `finance.apply_squad_levy(season_id)` — avg = Σ teams.spent
  / n teams; deducts from player wallets that didn't spend in the auction (teams
  with spent > 0 are exempt). Liquid first, then `bank.seize()` takes from the
  season's vault position for auto accounts. Idempotent via one
  `season_finance_entries` type='squad_levy' marker. Triggered automatically in
  the admin complete-draft route; manual button on `/admin/finances`.
- **Budget board**: `list_season_finances` rows now carry `section`
  (playing/non_playing/players), `name`, `wallet` (liquid), `locked` (vault).
  Public `/finances` + admin finances render grouped sections. Overview
  'Team wallets' sums only kind=='team' rows.
- **Public finances URL is `/finances`** (the matches blueprint has no
  url_prefix; routes include /matches explicitly). `/matches/finances` hits the
  `<season_id>` route and 302s.

## Funding fix + ghost-account cleanup — DONE (2026-08-16)

- **`fund_all_players` now funds LIQUID for everyone** — the old version forced
  `auto_vault=1` on newly created wallets, so a player's 10k landed LOCKED in
  the vault and the auction 'Purse' (liquid only) showed 0. Auto mode is
  strictly opt-in via `/account`, never forced by funding. (+1 unit, commit
  `176100f`; also restored the affected managers' 10k back to liquid in
  `data/scl.db` via a ledger-logged unlock.)
- **Phantom vault positions**: the old forced-auto funding left `vault_positions`
  rows (principal 10000, locked 0, last_yield 0) on manual accounts the user
  never opened — the /account page showed a vault the player didn't create.
  Deleted positions where `locked_capital=0 AND last_yield_match=0 AND
  account.auto_vault=0` (2 rows in scl.db: Ahmad + Sanawar).
- **Ghost login accounts**: `import_prod.py` step 5 used to copy old prod
  manager accounts (with a SHARED default password hash — a security hole) into
  the rebuild, creating logins for players who never signed up. Removed the
  step entirely: players self-signup and the admin links them. New test
  `test_s1_import_creates_no_login_accounts` locks it in. Also deleted the 4
  ghost users (Hassan/Hashir/Osama/Owais) from `data/scl.db`.
- **Stale finance tests updated**: two `test_finance` tests still asserted the
  old auto-vault funding behavior (they predated `176100f` and were never
  re-run). Now assert liquid funding, auto never forced.

## Auto mode default ON + post-auction vault lock — DONE (2026-08-16)

- **New accounts default to auto mode**: `get_or_create_account` inserts
  `auto_vault=1` (schema default changed too). The /account page shows
  'Switch to manual' for fresh accounts; the copy explains auto is the default.
- **Universal funding stays liquid through the auction**: `credit()` gained
  `force_liquid=True` (bypasses the auto-vault routing); `fund_all_players`
  passes it, so the 10k is spendable for bidding/staking even on auto accounts.
- **Money locks AFTER the auction**: new `bank.lock_auto_after_auction(season_id)`
  locks every auto account's leftover liquid into the season's vault position
  (compounding). Wired into the admin complete-draft route, after the squad
  levy. Manual accounts keep liquid control.
- **Gotchas**: wagers debit liquid via `adjust` (unaffected by auto mode).
  `_setup` (test conftest) opts managers into manual so match-reward tests
  stay faithful; the e2e auto-mode test now asserts ON by default.

## S2 economy — Increment 3 (universal credit + auto mode) DONE (2026-08-16)

- **Universal match credit**: `_apply_match_reward` credits EVERY player wallet
  (`match_reward_amount`, default 250) via `bank.credit()`. One marker ledger
  row (team_id NULL, team_name 'all players') guards the batch — the admin
  'pending fin.' count still works. Undo reverses all wallets (auto accounts
  give it back from the vault via `bank.unlock_amount`); legacy per-team
  entries (S1) still undo individually.
- **Auto mode**: `bank_accounts.auto_vault` (default 0). `bank.credit()` =
  liquid for manual, straight-to-  vault (compounding, via `_lock_internal`) for
  auto. Toggle: `POST /account/auto`, card on `/account`. `fund_all_players`
  funds LIQUID for everyone and never touches auto mode (auto is opt-in; see
  the 'Funding fix' note above — the old force-auto behavior was removed).
- **Gotcha**: the auto card's 'Switch to manual' text is a substring of the
  vault card's 'Switch to manual harvest' — e2e asserts must use the
  unambiguous 'Turn on auto mode'.

## S2 economy — Increment 2 (economy rules) DONE (2026-08-16)

- **No tier purse**: `DEFAULT_TIER_PURSES` = zeros; purse inputs removed from the
  ruleset UI (S1 ruleset rows keep their stored values). `create_team` never funds.
- **Universal funding**: `bank.fund_all_players(amount=10000)` — idempotent via a
  `season_funding` bank-transaction marker (manual `funding`/grants never exempt
  anyone; re-runs skip only `season_funding` recipients). Wallets are auto-created
  for players who never signed up. Admin button on `/admin/finances`; script
  `scripts/fund_players.py --db ... --yes`.
- Demo seed + test harness fund through `fund_all_players` (12/12 wallets in demo).

## S2 economy — Increment 1 (persistent teams) DONE (2026-08-16)

Plan: `ECONOMY_PLAN.md` (locked decisions D1-D4). Inc 1 shipped:

- **`global_teams` table** = persistent team identity (name, logo, about,
  manager_player_id). Per-season `teams` rows link via `global_team_id`
  (backfill in `db._backfill_global_teams` is idempotent, runs at bootstrap).
- **`create_team` no longer funds a purse** (no tier purse at all); it creates
  or reuses the global team (by id or exact name) + registers the season row
  while the season is in setup. Outside setup it creates the profile only
  (`registered: False`). `delete_team` never touches the wallet.
- **Team accounts**: `auction.create_team_account(gp, name)` + `update_team_profile`;
  a player creates a team from their account page (`/account` "Start a team"
  card, then "My team" edit form for logo/about). Public `/teams` + `team_profile`
  read global identity; global-only teams (no season) appear too.
- Tests updated to the new economy: `tests/conftest._setup` funds each manager
  10k (`tx_type="funding"`) instead of relying on tier purses; e2e seed funds
  the manager too; `seed_demo.py` funds 10k per user.

## Ball-by-ball match view — DONE (2026-08-15)

`/matches/<season>/<match>/balls` — play-by-play from the stored `delivery_log`: innings tabs
(pure-CSS radio tabs, same pattern as leaderboards), over-by-over ball chip grid
(`.ball`/`.ball-wkt`/`.ball-four`/`.ball-six`/`.ball-extra`, `<details>` for per-ball detail),
Fall of Wickets + Partnerships callouts. Backend: `ScorerService.ball_by_ball()` in
`scorer_service.py` (groups deliveries → innings → overs → balls; FOW uses the CSV's
`Progressive Runs`/`Over Number`/`Ball Number`; partnerships = runs since last wicket, plus an
unbroken `current` stand when the innings ends). Summary page links through via
`matches.match_balls`. S1's 13 matches have no `delivery_log` → the page shows a
"ball-by-ball not available" state (go-forward for S2).
