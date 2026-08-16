# S2 Economy Restructure — Plan

Ports the S2 economic model onto the platform: persistent teams, universal
player funding, auto-mode vaults, per-match credits, and a squad-cost levy.
Everything below is grounded in the current code (verified against the real DB).

---

## 1. Current state (what we're changing)

| Area | Today | Target |
|---|---|---|
| Teams | Per-season rows (`teams.season_id NOT NULL`); identity is just the `global_team_id` string (public pages already dedupe by it) | **Persistent team identity** (`global_teams`) + per-season participation rows |
| Team money | Manager's player wallet (`bank_accounts` owner_type='player', owner_id = manager's `global_player_id`) | **Unchanged** — this is already the right model |
| Team creation | `create_team` (setup only) funds manager wallet with **tier purse** (9k/10k/11k) | **No per-tier purse** — teams start with the manager's own 10k funding |
| Delete team | Setup only; **claws the purse back** out of the wallet | Delete removes the team, **wallet untouched** |
| Per-match reward | `match_reward_amount` (200) paid to the **2 playing teams** only | **250 to EVERY player wallet** per finalized match (idempotent) |
| Vault | 7%/match yield, compounds unless reinvest off, unlocks at M12 | **Unchanged** + **auto mode** flag routes incoming money straight to the vault |
| Balance board | `/matches/finances` + admin: **playing teams only** | Playing teams → **non-playing teams** → **individual players** |
| Funding | Tier purses at team creation | **Every player gets 10,000** before the S2 auction (idempotent admin action) |
| Transfers | Player self-deposit only; admin `post_transfer` / `gift_team` | **Unchanged** — transfers stay admin-only (already true) |
| Squad cost | — | **Average squad cost** of the current season deducted from wallets that didn't spend in the auction |

---

## 2. Target model

### 2.1 Persistent teams (`global_teams`)
Mirrors the existing `global_players` → `players` pattern:
- **New table `global_teams`**: `id`, `name`, `logo`, `about`, `manager_player_id`, `created_at`.
- `teams` stays per-season (squad, spent, credits, control) and links via the **existing `global_team_id` column**.
- **Backfill**: S1's 4 teams → 4 `global_teams` rows; set `teams.global_team_id`.
- Match stats need no changes — they're keyed by season + team id/name/global id (`match_team_stats`, `match_registry.team_*_global_id`), so S1 stats remain untouched.
- `team_profile()` and the teams index already aggregate by `global_team_id` — they just start reading name/logo/about from `global_teams`.

### 2.2 Team accounts (anytime, any season)
- A linked player can **create a team account** from their account page: creates a `global_team` with themselves as manager — **even if the team won't play this season**.
- Admin can **always** create a team and link a player as manager (relax the setup-only gate for the global entity; per-season participation rows still require setup).
- Non-playing teams: no squad, but the manager edits **logo, about, name** from their account page ("My team" card). Playing-team managers get the same card.
- `delete_team` deletes the participation row / team entity but **never touches the wallet**.

### 2.3 Funding (10k universal, no tier purse)
- **Every player** (all 17 `global_players`) gets **10,000 liquid** before the S2 auction — an idempotent admin action ("Fund all players") + script, so re-runs are safe. Demo seed does the same.
- `create_team` **stops funding a purse**; the S2 ruleset's `tier_purses` are zeroed and the purse inputs leave the ruleset UI (schema keeps the column for S1 rows).
- Admin can **grant extra funds** to a weak manager's team before the auction (existing `gift_team`, kept).

### 2.4 Auto mode
- `bank_accounts.auto_vault` flag (default off). When on, **incoming money routes straight to the vault** (auto-reinvest) so the account compounds the fixed yield every match without the owner lifting a finger.
- Toggle on the account page. (Scope of "incoming money" — decision D2.)

### 2.5 Per-match credit (250 universal)
- `match_reward_amount` → **250**, paid to **every player wallet** on every finalized match (idempotent, same hook as today: `finance_service.on_match_finalized`). Playing-team managers and non-playing managers are players too, so they're included.
- Admin finance ledger + "pending" counts updated to reflect per-account credits.

### 2.6 Squad-cost levy
- After the auction completes: **average squad cost = Σ(playing teams' auction spend) ÷ # playing teams**.
- That amount is **deducted from wallets with no auction spend** (non-playing teams, players who sat out), capping at available liquid (no negative balances). (Timing/handling — decision D3.)

### 2.7 Balance board
- `/matches/finances` (public) + admin finances: **three sections** —
  1. Teams **in the current season** (wallet = manager's liquid + vault)
  2. Teams **not in this season** (same)
  3. **Individual players** (all linked/unlinked players with accounts)

---

## 3. Delivery — 5 committed increments

1. ✅ **Team identity — DONE (2026-08-16)** — `global_teams` schema + backfill; `create_team` (no purse) / `delete_team` (no clawback); "My team" card on the account page (create team, edit logo/about); `team_profile` reads global data. 169 tests green.
2. ✅ **S2 economy rules — DONE (2026-08-16)** — tier purse gone from ruleset UI + defaults (zeros); idempotent **10k funding** (`bank.fund_all_players`, admin "Fund all players" button on Finances, `scripts/fund_players.py`); admin grants kept. 172 tests green.
3. ✅ **Universal credit + auto mode — DONE (2026-08-16)** — 250/match to EVERY player wallet (one marker ledger entry; undo reverses all); `auto_vault` flag (new wallets default auto, owners toggle on the account dashboard); `bank.credit()` routes deposits/grants/funding/match credits to the vault when auto. 177 tests green.
4. ✅ **Squad-cost levy + balance board — DONE (2026-08-16)** — `finance.apply_squad_levy()` runs automatically on draft complete (idempotent; liquid first, then vault for auto accounts; spenders exempt; manual fallback button on admin finances); `list_season_finances` returns three sections (playing → non-playing → players) rendered on `/finances` + admin. 183 tests green.
5. ✅ **Admin flows + docs — DONE (2026-08-16)** — fund-all button (Inc 2), create/link team anytime (Inc 1), finance ledger shows universal credits + squad levy; reference docs (ADMIN/PLAYER_MANAGER/CLI) updated for the S2 economy. **All 5 increments complete — 183 tests green.**

---

## 4. Locked decisions

- **D1 — Team persistence**: YES — new `global_teams` table (identity: name, logo, about, manager). Per-season `teams` rows link via `global_team_id`.
- **D2 — Auto-mode scope**: EVERYTHING incoming goes to the vault when auto is on (grants, deposits, 250 match credits) — with the funding order: **10k funding lands → squad-cost levy is deducted from it (the avg squad price for that season's auction) → the remainder is vaulted**. Players who never create an account are **auto by default**; owners can switch off auto once they have an account.
- **D3 — Squad-cost levy**: applied **automatically when the auction completes**; wallets are debited down to 0 max (no negative balances). Auto accounts with no liquid: the levy is taken from the vault position (it comes out of the 10k sitting there).
- **D4 — 250 credit scope**: EVERY player wallet gets 250 per finalized match (idempotent) — and a **wallet is auto-created for players who never created an account** (created at funding time, running on auto). No wallet = no credit only for players who never got funded.

### Funding flow (per player, before the S2 auction)
1. Wallet auto-created if missing (default `auto_vault` = ON).
2. **+10,000** credited (idempotent — re-running is safe).
3. When the auction completes, the **avg squad cost** is deducted (liquid first; auto accounts fall back to the vault position).
4. Auto accounts: the remainder plus every later inflow (grants, deposits, 250/match credits) goes straight to the vault (compounding 7%/match).
