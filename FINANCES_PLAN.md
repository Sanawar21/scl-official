# Finances + Vault Wiring — Increment Plan (rev 3)

Goal: finish the central-bank story. The account/vault model and `apply_match_yield` already
exist but nothing triggers match finance or season settlement. User decisions (2026-08-14):

- **The manager's account IS his team's account** — one wallet from team creation. No
  settlement/transfer step: the auction budget lives in the manager's player bank account from day
  one, so the manager can spend (wagers, vault, etc.) before any match is played.
- **Yield auto-applies** when a match result is finalized — the admin can't forget it (plus a
  manual catch-up button).
- **No ticket-price math.** Every finalized match rewards both playing teams a **fixed amount**
  (per-season ruleset constant, S2 default 200).
- **No separate credit-refund feature.** Admin adds balance to the manager's account from the
  existing dashboard bank-adjust and comments "this is for the credit saved".

Status: **planned**. Docs: `MEMORY.md`, `RESUME.md`, `PLAN.md`, `PROD_IMPORT_PLAN.md` (Phase 3
deferred here). Old-app finance code: `../SCL/app/routes/admin.py` (`finances_adjust` /
`finances_transfer`, ~735–860), `../SCL/app/routes/landing.py` (`/finances` read-only board).

---

## 1. What's already in place (no rework)

- `bank_accounts` (owner_type player/team, liquid_cash, locked_capital), `vault_positions`
  (per season: principal, locked_capital, reinvest, last_yield_match), `bank_transactions` —
  schema + `app/services/bank_service.py`.
- `BankService.apply_match_yield(season_id, match_number)` — 7%/match, compound/manual, **no route,
  no unlock**.
- Player vault UI on `/account` + admin bank adjust (`/admin/bank/adjust`, accepts
  `player:<global_player_id>` refs) — the existing bank adjust *is* the credit-refund mechanism.
- Match registry per season (`ScorerService.list_match_registry`); registry upsert stores
  team_a/team_b (local team ids, global fallback); admin scorer import + walkover routes exist
  (`app/routes/matches.py`) — the natural hook points.
- Auction money moves all live in `app/services/auction_service.py`: `create_team` (purse),
  `gift_team`, `close_current` (deduct sold price), `admin_transfer` (price between teams),
  `_execute_trade` (cash moves), `complete_draft` (zero purse of incomplete teams), plus undo
  handlers for each — **the sites that must also touch the manager wallet**.
- S1 44 `finance_transactions` in `prod-data/season_dbs/season-1.json` (adjust add/remove +
  transfer, each with team_id, comment, before/after purse).

## 2. Design (user-decided + recommendations)

1. **One wallet per manager, funded at team creation.** `create_team` opens
   `get_or_create_account('player', manager_player_id)` and credits it with the tier purse
   (same `db.write`, conn passthrough). `teams.purse_remaining` remains the auction-facing column
   and is **kept in lockstep with the wallet at every auction money move** — gift, close, transfer,
   trade, complete-draft penalty — via `bank.adjust(..., conn=conn)` inside the same transaction.
   Because both writes share one write-lock/connection they can never drift (MEMORY.md gotcha).
   Undo handlers reverse the bank side too (e.g. undo gift → debit back). `delete_team` (setup)
   zeroes the wallet to remove phantom money. **Consequence (accepted):** the wallet is spendable
   any time — a manager who wagers or vault-locks part of the purse mid-auction shrinks his bidding
   budget; bids/close then fail on `Insufficient liquid cash` just like a purse shortfall.
2. **Auto-finance on match finalization** — `finance_service.on_match_finalized(season, match)`:
   1. **fixed match reward** to both playing teams (ruleset `match_reward_amount`);
   2. **vault yield catch-up** for the season through the current finalized-match count (cap 12).
   No settlement step (rev 2 removed). Hooked into the admin scorer import + walkover routes
   (not the service — `ScorerService` stays decoupled). A **"Process pending"** admin button
   backfills all finalized matches.
3. **Yield loop fix (correctness).** Current `apply_match_yield(season, 5)` applies ONE 7% and
   stamps `last_yield_match=5`, skipping matches 1–4. Docs require per-match compounding
   (2000→2140→2290→2450→2622…). Fix: loop `last+1..match_number`. Existing sequential tests pass.
4. **M12 unlock** — admin-triggered (`unlock_vault`), guarded by ≥12 finalized matches (force
   checkbox). Locked capital → liquid, position flagged, `vault_unlock` transaction. No other
   withdrawal path.
5. **Finance ledger** (`season_finance_entries`) = the season's post-auction money story: match
   rewards, fines/umpire/sub-cash adjusts, transfers. Every finance action writes the ledger row
   *and* the bank transaction atomically (same `db.write`, conn passthrough). Ledger carries
   team_name, before/after wallet, from/to for transfers (S1-compatible shape); `bank_transactions`
   remains the per-account ledger. (Auction-era moves — gifts, closes, trades — stay in
   `auction_action_log` as today; the ledger is finance actions + rewards.)
6. **Manual overrides stay**: `post_adjust` (add/remove on the manager wallet, e.g. −200
   "Playing with 3 players") and `post_transfer` (manager A → manager B, e.g. sub cash).
7. **Undo**: one-step undo of the last non-undone finance entry (reverse wallet delta / transfer),
   marking `undone_at`. No cascade.
8. **S1 import Phase 3 is history + wallet seed.** Insert the 44 ledger rows verbatim; replay the
   chain (S1 setup purse + entries) and cross-check the final purse == `teams.purse_remaining`
   (2285/1145/2885/1365, exit 2 on drift); then **seed each manager wallet** with a single credit
   of the final purse (replaying history through `bank.adjust` would hit S1's negative mid-season
   purses — seed, don't replay). S1 pages then show real wallets that equal the imported purses.

## 3. Schema additions (`app/schema.py` + `db.bootstrap()` migrations)

```sql
-- Season finance ledger (old finance_transactions; post-auction team money story)
CREATE TABLE IF NOT EXISTS season_finance_entries (
  id TEXT PRIMARY KEY,
  season_id TEXT NOT NULL REFERENCES seasons(id),
  team_id TEXT,                 -- local team id (side affected; NULL on transfers)
  team_name TEXT,
  type TEXT NOT NULL,           -- 'match_reward' | 'adjust' | 'transfer'
  operation TEXT,               -- 'add' | 'remove' (adjust only)
  amount INTEGER NOT NULL,
  comment TEXT,
  created_by TEXT,
  from_team_id TEXT,            -- transfer only
  to_team_id TEXT,              -- transfer only
  before_wallet INTEGER,        -- manager wallet before
  after_wallet INTEGER,
  undone_at TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_finance_season ON season_finance_entries(season_id);
```

- `vault_positions`: `unlocked INTEGER NOT NULL DEFAULT 0`, `unlocked_at TEXT` (migration).
- `rulesets`: **`match_reward_amount INTEGER NOT NULL DEFAULT 200`** — the fixed per-match reward
  (the only finance constant; S2 default 200, editable per season in the ruleset editor).

## 4. Service work

### `app/services/bank_service.py`
- Fix `apply_match_yield` per-match loop (§2.3); keep the guard as a fast path.
- `unlock_vault(season_id, force=False)` (§2.4).

### `app/services/auction_service.py` (wallet sync)
- `create_team`: after inserting the team, `get_or_create_account('player', manager_player_id,
  conn=conn)` + `adjust(account, purse, "Team purse (tier)", tx_type='purse', conn=conn)`.
- `gift_team` / `close_current` (deduct sold price) / `admin_transfer` / `_execute_trade` /
  `complete_draft` (penalty zero): adjust the manager wallet(s) with the same delta as
  `purse_remaining`, same conn.
- `delete_team`: zero the manager wallet (setup only).
- Undo handlers (`_undo_gift`, `_undo_close_sold`, `_undo_transfer`, `_undo_trade_accept`,
  `_undo_complete_draft`, …): apply the inverse wallet delta.
- Add `_team_manager_account(conn, team)` helper: resolve account id via
  `get_or_create_account('player', team['manager_player_id'], conn=conn)`.

### `app/services/finance_service.py` (NEW — `FinanceService(db, bank_service, auction_service)`)
- `list_season_finances(season_id)` — Budget Board: team name + manager wallet (liquid_cash) +
  credits_remaining + players/bench counts (S1 works because wallets are seeded).
- `list_finance_entries(season_id, limit)` — ledger newest-first with display labels.
- `on_match_finalized(season_id, match_id, actor='system')` — idempotent: reward both teams in
  the match + yield catch-up. Resolve match teams via `teams WHERE id = :ref OR global_team_id =
  :ref` (registry stores local ids with global fallback); unresolvable team → skip + comment.
- `process_pending(season_id, actor='admin')` — run `on_match_finalized` for every finalized
  match not yet rewarded (backfill button).
- `post_adjust(season_id, team_id, operation, amount, comment, actor)` — bank.adjust on the
  manager wallet (add/remove) + ledger row; `operation ∈ {add, remove}`, amount > 0; overdraft
  raises (live seasons — unlike S1 history, no negative wallets).
- `post_transfer(season_id, from_team_id, to_team_id, amount, comment, actor)` — two bank.adjusts
  + ledger row with both wallets' before/after.
- `undo_last_finance_entry(season_id, actor)` — reverse delta(s); raises if the wallet would
  overdraft; no-op when already undone.
- `credit_refund_hint(season_id)` — display-only: per team `credits_remaining ×
  ruleset.credit_refund_rate` so the admin knows what to type into bank adjust (no code path).

### Import Phase 3 — `scripts/import_prod.py --phase finance`
1. Insert the 44 ledger rows verbatim (local team ids already match `teams.id`).
2. Purse-chain cross-check → exit 2 on drift.
3. Seed wallets: per team, `get_or_create_account('player', manager_player_id)` + credit
   `purse_remaining` (tx `purse`, comment "Season 1 final purse").
`--phase all` = core + stats + finance. Idempotency same as other phases (refuses over existing
data without `--force`).

## 5. Routes & screens

### Hook (no UI)
- `app/routes/matches.py`: after a successful CSV import / walkover upsert, call
  `finance_service.on_match_finalized(season_id, match_id)`. Match *undo* does not reverse finance
  (forward-only; admin adjusts manually — documented limitation).

### Admin — `/admin/finances` (new page; nav next to Scorer)
- Season picker + match picker (from registry).
- **Match finance**: adjust form (team, add/remove, amount, comment) + transfer form (from/to,
  amount, comment) — for fines, umpire duty, sub cash.
- **Process pending** button (backfill rewards+yield for all finalized matches).
- **Vault**: "Apply yield through Match N" (default = next unapplied) and "Unlock vault (M12)"
  with force checkbox.
- **Credit refund hint**: per-team `credits_remaining × rate` reference table (actions still go
  through the existing `/admin/bank/adjust`).
- **Ledger** + "Undo last entry".
- Endpoints: `POST /admin/finances/adjust`, `/transfer`, `/process-pending`, `/yield`,
  `/unlock`, `/undo` (admin only).

### Public — `/finances[/<season_id>]` (read-only, port the old page)
- Budget Board (team + wallet) + ledger. On `matches_bp`; nav link "Finances".

### Player — `/account`
- Vault section: per-season match count, lock-until-M12 note, **Unlocked** badge
  (`vault_unlock` shows in transactions). Manager sees his team purse == liquid cash directly.

## 6. Tests — `tests/test_finance.py` (new file)

1. **Yield catch-up**: position at `last_yield_match=0`; `apply_match_yield(season, 4)` →
   2000→2140→2290→2450→2622 (doc table; proves the loop fix).
2. **Unlock**: moves locked→liquid, flags position, logs `vault_unlock`; refuses <12 finalized
   matches; `force` bypasses.
3. **Wallet == purse from creation**: `create_team` funds the manager's player account with the
   tier purse; `gift_team`/`close_current`/`admin_transfer`/trade move the wallet identically;
   **undo of a gift/close/transfer reverses the wallet**; `delete_team` zeroes it.
4. **`on_match_finalized`**: both playing teams get `match_reward_amount`; yield applied through
   match count; **idempotent on re-run** (no double reward).
5. **`process_pending`**: backfills rewards+yield for all finalized matches.
6. **Adjust/transfer**: wallet debit/credit + ledger rows with before/after; validation errors;
   overdraft raises.
7. **Undo**: reverses adjust / transfer / reward; second undo no-op; overdraft guard.
8. **S1 import (`--phase finance`)**: 44 rows; chain replay == final purses (2285/1145/2885/1365);
   wallets seeded with final purses.
9. **Route smoke**: `/admin/finances` login-gated; `/finances` + `/finances/season-1` 200 with
   Budget Board/ledger; `/account` shows unlocked badge.

## 7. Build order

1. Schema + migrations (ledger, vault unlocked, `rulesets.match_reward_amount`) + Ruleset model.
2. `bank_service`: yield loop fix + `unlock_vault`.
3. `auction_service`: wallet sync at all money moves + undo handlers (tests 3).
4. `finance_service.py` + hook in scorer routes.
5. Import Phase 3 (+ cross-check + wallet seed).
6. Routes + templates + nav (`/admin/finances`, `/finances`, `/account` tweaks).
7. Tests (§6) + E2E against `data/scl.db`: `--phase finance` on a copy, `/finances/season-1`
   matches old deployed numbers, full suite green.
8. Update `PLAN.md`, `MEMORY.md`, `RESUME.md` (gotchas: wallet==purse sync points, yield loop,
   seed-don't-replay, forward-only finance on match undo).

## 8. Out of scope

- Fantasy entries; offline scorer; admin dashboard consolidation — later increments.
- Ring-fencing the auction budget from wagers/vault (user chose one shared wallet).
- Player-side income beyond rewards/vault (tickets are gone by design).
- Reversing finance when a match import is undone (forward-only; manual adjust).
