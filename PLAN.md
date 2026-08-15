# SCL Rebuild — Implementation Plan

Status: **auction + prod import + wager platform + matches/stats + finances/vault + offline
scorer + admin dashboard built** — 85 tests pass (19 auction + 4 bank + 15 wager + 17
matches/stats + 21 finance + 7 scorer/PDF + 6 admin dashboard), E2E verified.
Plans: `PROD_IMPORT_PLAN.md`, `WAGER_PLAN.md`, `MATCHES_PLAN.md`, `FINANCES_PLAN.md`,
`OFFLINE_SCORER_PLAN.md`, `ADMIN_DASHBOARD_PLAN.md`.
See MEMORY.md for build notes and gotchas. Docs not final; rules fluid per season.
Stack (decided): Flask + SQLite + Flask-SocketIO, server-rendered Jinja, mobile-first
Rebuild home: this repo (`SCL-official`). Reference implementation: `../SCL` (Flask + TinyDB).
See `MEMORY.md` for the living context/decisions log.

## Platform features (all increments)

1. Player auction dashboard — **Increment 1 (current focus)**
2. Admin dashboard — complete control over the application
3. Wager application (yes/no pools, house injection)
4. Matches, seasons, players, teams stats viewing
5. Teams & players finances tracking + Vault
6. Offline scorer (downloadable HTML → CSV → admin import)

## Domain model

- **Global players** persist across seasons (a player one season can be a manager the next).
- Season = 12-match double round robin; champion = table topper (S2 abolishes the final).
- **Manager = a player who owns a team**; manager tier = his player profile tier (drives
  starting purse and the credit deduction). Credits system unchanged (8/team, P3/G2/S1, buy 3).
- **Central bank**: every player has an account (liquid cash + vault/locked capital).
- Season rules are fluid → **per-season ruleset** drives the auction engine and league format.

## Round-2 requirements (folded in)

1. **Player CRUD pre-auction**: admin adds/modifies players before an auction (tier, speciality, base).
2. **Manager tier from player profile**: no separate manager tier — purse/credits derive from the
   manager-player's tier.
3. **Post-auction transfer window**: after the auction, admin has full control over player transfers
   (player ↔ team moves with purse/credit reconciliation + transaction log).
4. **Admin undo for every action**: append-only `auction_action_log`; admin can undo the latest
   action(s) (e.g. an accidental manager bid, a mistaken close/transfer) — each action records
   enough before-state to reverse it cleanly.
5. **Self signup + admin linking**: anyone signs up with a personal username; admin later links the
   account to an existing global player (or creates the player from the signup).
6. **Pre-auction gifts**: admin can gift/deduct a team's starting purse before the auction to
   balance skill gaps (logged transaction with comment).
7. **Admin takeover of fumbling teams**: if a manager fumbles in the auction (no-show, bad bids,
   rule violations), admins **take over the team** and balance it by buying/selling on its behalf.
   Takeover is explicit + logged (reason, admin, time) and reversible; while taken over, the
   manager loses bid rights and admin acts as the team (bid/close/transfer).

## Key design decisions

- **Team control status**: each team is `manager_controlled` or `admin_takeover` (with reason/admin/
  timestamp, restorable). Takeover is itself an action-logged event, so it's undoable like everything
  else. Admin control room gets a "bid as team" action so admins can buy/sell on a taken-over
  team's behalf during any phase (live lots and the transfer window).

- **Per-season ruleset** replaces hardcoded `rules.py` constants: phase order (default S2:
  Platinum → Gold → 5-min break → Silver → Phase B), tier purses (S2: 9k/10k/11k), tier base prices
  (S2: 3k/2k/1k), tier credits (3/2/1), total credits (8), bid increment (50), Phase B price (200),
  unspent-credit refund (1000/credit), required players (3), roster size (4), break duration (5 min).
  Admin edits at setup; S2 defaults preloaded.
- **SQLite** (stdlib `sqlite3`, WAL, connection-per-operation with a lock) — no ORM, minimal deps.
- **Live updates** via SocketIO emit-on-change with polling fallback (proven in existing app).
- **Port, don't rewrite**: `auction_service.py` logic → SQLite-backed service, ruleset-driven.
- **Undo via action log**: every mutating auction/bank action is logged with reversal data;
  admin UI exposes undo (stack-style, with safety guards like the existing step-back rule).

## SQLite schema (auction + banking domain; others extend later)

- `global_players` — persistent identity: id, name, tier, speciality
- `users` — id, username, password_hash, role (admin/player/manager), global_player_id (admin-linked,
  nullable), team_id (nullable), created_at
- `seasons` — id (slug), name, status (setup / live / completed / transfers_open), ruleset_id, created_at
- `rulesets` — id, season_id, phase_order (JSON), tier_purses (JSON), tier_base_prices (JSON),
  tier_credits (JSON), total_credits, bid_increment, phase_b_price, credit_refund_rate,
  required_players, roster_size, break_minutes
- `players` — id, season_id, global_player_id, tier, speciality, base_price, credits,
  status (unsold/sold), sold_to_team_id, sold_price, phase_sold, current_bid, current_bidder_team_id,
  nominated flags, nomination order
- `teams` — id, season_id, name, manager_player_id, manager_tier (from profile), spent,
  credits_remaining, players (JSON ids), bench (JSON ids), is_active,
  control_status (manager_controlled / admin_takeover), takeover_reason, takeover_by, takeover_at
  (⚠ `purse_remaining` was **dropped 2026-08-15** — the manager's wallet is the team purse, the
  single source of truth; every money move goes through `bank_accounts`)
- `bids` — id, ts, team_id, player_id, amount, phase, kind (bid/pass)
- `trade_requests` — id, status, created_at, from/to team, offered/requested player, cash amounts
- `transfers` — post-auction admin transfers: id, season_id, team_from, team_to, player_id, price,
  credits, created_by, created_at
- `auction_action_log` — id, season_id, action_type, actor, target refs, before/after (JSON),
  created_at, undone_at, undo_of (for generalized admin undo)
- `auction_meta` — season_id, phase, current_player_id, nomination_history (JSON), break_started_at
- `season_snapshots` — published read-only auction results per season
- `bank_accounts` — id, owner_type (player/team), owner_id, liquid_cash, locked_capital
- `vault_positions` — id, account_id, season_id, principal, reinvest (bool, default true),
  last_yield_match, created_at (7%/match compounding or manual harvest, unlock at M12)
- `bank_transactions` — id, account_id, type (deposit/withdraw/gift/wager/vault_lock/vault_yield/
  vault_harvest/transfer_fee…), amount, balance_after, comment, created_at
- `wager_scenarios`, `wager_bets` — yes/no pools (later increment, schema reserved)

## Auction state machine (ruleset-driven)

setup → phase_a_<tier>… (configured order, optional break) → break → phase_b → complete → transfers_open

- Admin: create season + ruleset; player CRUD; team/manager setup (tier from profile);
  pre-auction gifts; set phase; nominate player; close lot (sold / no-bid unsold); step back;
  start/end break (timer); enter Phase B (enough unsold players); complete draft + penalties
  (auto-assign unsold, zero purse, credit refund at ruleset rate); **post-auction transfer window**
  (admin-driven transfers); publish snapshot; **undo any action** from the action log;
  **takeover/restore teams** (admin bids/sells on behalf of a fumbling manager's team).
- Manager: bid (min = max(base, current + increment)), pass, live state, trades during break
  (bidding disabled while admin has taken over his team).
- Viewer: public live board + published season page (no credentials).

## Banking & accounts (core to auction increment)

- Accounts exist for every player; teams draw on the manager's purse for auction spend.
- Pre-auction gifts hit the team purse with a logged `bank_transactions` entry.
- Vault: player moves liquid → locked (vault) per season; 7%/match yield, default compounding,
  manual harvest toggle; principal locked until end of Match 12 (matches docs; no withdrawals).
- Wager participation draws from liquid cash (wager app is a later increment, but the
  account model + transactions are laid now).

## Signup & linking flow

- Public signup (username/password) → `users` role=player, unlinked.
- Admin: "Link accounts" panel — pick an unlinked user → link to existing global player
  (or create global player from the signup).
- Managers sign in with their personal account; admin assigns team + manager role per season.

## Screens (mobile-first Jinja)

- Admin auction control room (setup, ruleset editor, player CRUD, gifts, control, action log + undo,
  transfer window, publish)
- Admin link-accounts panel (signup linking)
- Manager dashboard (my purse/credits, quick-bid buttons, roster, trades)
- Player banking page (liquid cash, vault with compound/harvest, transaction history) — minimal in
  increment 1, full wagers later
- Viewer live board + published season page
- Landing page linking all areas (placeholders for later increments)

## Increment 1 build order

1. ✅ Scaffold: app factory, config, SQLite bootstrap/migrations, session auth (admin + self-signup)
2. ✅ Schema + ruleset model + S2 defaults; global players; bank accounts + transactions
3. ✅ Auction service (ported, ruleset-driven) + **action log/undo** + **admin transfers** + gifts
4. ✅ Routes: admin control, manager actions, viewer board, publish/snapshot, signup/link, banking
5. ✅ Templates + live updates (4s polling; socket emit kept server-side — see MEMORY.md)
6. ✅ Tests: pytest + temp SQLite covering full lifecycle incl. undo and transfers (19 pass)
7. ✅ Run + verify end-to-end (server boots; pages render; manager bid flow verified)

## Later increments (after auction ships)

2. ✅ Admin dashboard consolidation (all domains) — **built**; plan in
   `ADMIN_DASHBOARD_PLAN.md` (`/admin` = Overview + shared tab shell; auction control moved
   to `/admin/auction`; single Admin nav link)
3. ✅ Matches/seasons/players/teams stats (league table, NRR/H2H/boundaries, leaderboards) — **built**;
   plan in `MATCHES_PLAN.md` (match registry + scorer CSV import + on-demand aggregates + S1
   scorer data imported as `--phase stats`; S2 tie-breakers: NRR → H2H → boundaries)
4. ✅ Finances + Vault full UI (7%/match, harvest, M12 unlock, auto rewards on match
   finalization, ledger + undo) — **built**; plan in `FINANCES_PLAN.md` (wallet == team purse
   from creation, purse column dropped; `/admin/finances` + public `/finances[/<season>]`;
   S1 `--phase finance` import; balances reset to 0 for the new economy)
5. ✅ Wager app (yes/no pools, calibration, house injection, veto, voided refunds) — **built**;
   plan + payout model in `WAGER_PLAN.md` (pooled Yes/No AMM on the central bank: propose →
   blind-estimate calibration → solvency veto → peer phase → house injection/guarantee →
   proportional resolution; voided = 100% refunds)
6. ✅ Offline scorer (downloadable HTML → CSV → admin import; port existing scorer) — **built**;
   plan in `OFFLINE_SCORER_PLAN.md` (public `/scorer` + `/scorer/download`, call-up batting
   order via `batter_order`, DB-fed scorecard PDF via reportlab)

## Frontend transformation (in progress)

Full UX redesign + Playwright UI suite — plan in `FRONTEND_PLAN.md`. Phased (each committed +
verified before the next):

- [x] **Phase 0 — Playwright infra + baseline smoke** (13 e2e tests locking current flows;
  server booted per session on a temp DB)
- [ ] Phase 1 — Shell + design system (base.html, CSS tokens, toast JS, mobile nav) + Home + Auth
- [ ] Phase 2 — Public surfaces (live board, published, matches, table, leaderboards, profiles, finances)
- [ ] Phase 3 — Player/manager surfaces (banking/vault, wagers, manager dashboard)
- [ ] Phase 4 — Admin polish + final pass
- [ ] Phase 5 — Full suite green + docs

⚠ Data-parity rule (user requirement): every data point visible today stays visible in the
redesign — audit table in FRONTEND_PLAN.md.
