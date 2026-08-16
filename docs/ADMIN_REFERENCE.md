# Admin Reference — SCL Platform

Everything an admin can do, where it lives, and what each action does. Covers the
auction, the offline scorer / match registry, finances + vault, wagers, account
linking, and the overview dashboard. The admin logs in at `/auth/login` with the
username/password configured at boot (default `admin` / `admin123`, overridable via
`SCL_ADMIN_USERNAME` / `SCL_ADMIN_PASSWORD`).

Quick demo of everything: see `scripts/seed_demo.py` (runs against a separate
`data/demo.db` — the real `data/scl.db` is never touched).

---

## 1. Overview — `/admin`

Landing dashboard with one card per area, each showing live counts and a primary
button into the area:

| Card | Counts shown | Primary link |
|---|---|---|
| **Auction** | Phase, Teams, Sold/Total players | `Open control` → `/admin/auction?season=…` |
| **Matches** | Registry, Finalized, Pending finance | `Scorer admin` → `/admin/scorer?season=…` |
| **Finances** | Team wallets, Vault positions, Yield x/12 | `Finance admin` → `/admin/finances?season=…` |
| **Wagers** | Open, Resolved | `Wager admin` → `/wagers/admin` |
| **Accounts** | Unlinked, Linked | `Link accounts` → `/auth/admin/link` |

Also: "Create your first season" form when no season exists, and a season switcher
top-right to pick which season the counts refer to (`?season=<id>`).

---

## 2. Auction control — `/admin/auction?season=<id>`

The auction is the draft: teams buy players tier by tier with credits and Phase B.
**S2 economy: there is NO tier purse** — every player gets the universal 10k
funding and the **team's money IS the manager's wallet** (one bank account per
manager). Season setup order: **create season → configure ruleset → fund players →
add players → create teams → run phases → complete draft (auto squad levy) → publish**.

### 2.1 Season + ruleset
- **Create season** (`/admin/season/create`): name → creates a season in `setup`
  status with the default ruleset.
- **Ruleset** (`/admin/season/<id>/ruleset`): the per-season economy. Fields:
  - `phase_order` — comma-separated `platinum, gold, break, silver, phase_b`
  - Per tier (platinum/gold/silver): `base price`, `credits` (no purse in S2)
  - `total_credits`, `bid increment`, `phase_b price`, `credit refund rate`
  - `required_players` (bought players beyond the manager), `roster_size`
  - `break_minutes`, `match_reward_amount` (fixed credit to EVERY player wallet
    per finalized match — default 250)

### 2.2 Players
- **Add player** (`/admin/season/<id>/player/add`): name, tier, speciality.
- **Edit / delete** per player (`…/player/<pid>/update`, `…/player/<pid>/delete`).
- Manager players are roster slots, not auction lots (they're the team's 4th slot).

### 2.3 Teams
- **Create team** (`/admin/season/<id>/team/create`): name + manager player →
  creates/reuses the **persistent team profile** (`global_teams`) and registers it
  for the season. No purse is funded — the team starts with the manager's own
  wallet. Admin can create a team any time (outside setup it's a profile only,
  not a season registration).
- **Delete team** (setup only): removes the season row; **the manager's wallet is
  never touched**.
- **Gift / take** (`…/team/<tid>/gift`): amount + `Gift +`/`Take −` + comment —
  this is the **grants/fines/bonuses tool** (e.g. "credit saved" adjustments).
- **Teams panel** (`/admin/teams`, tab **Teams**): the full control room for
  **persistent team identities** — create a team (name + manager player), edit
  the name/about, **upload or remove logo + banner** (JPG/PNG/WEBP/GIF, ≤5 MB),
  and delete a team. Deleting removes the profile + season registrations but
  **never touches the manager's wallet**. Teams missing an asset automatically
  fall back to the **SCL brand** (logo mark / wide banner) across the platform.
- Managers can also upload their own logo/banner from `/account` ("Upload
  branding" card) — admin uploads override/replace them.
- **Takeover / restore** (`…/team/<tid>/takeover` + `restore`): admin takes control
  of a fumbling manager's team (blocks their bids) and gives it back.

### 2.4 Auction Control panel
- **Set phase** (`/admin/season/<id>/phase`): jump to any phase in the flow
  (`phase_a_<tier>`, `break`, `phase_b`) or `complete` / `transfers_open`.
- **Nominate next** (`/admin/season/<id>/nominate`): put the next unsold player of
  the current tier up for bidding.
- **Step back** (`/admin/season/<id>/previous`): undo the last nomination.
- **Close lot** (`/admin/season/<id>/close`): sell to the current high bidder
  (charges wallet + credits) or mark unsold if no bid.
- **Complete draft + penalties** (`/admin/season/<id>/complete`): end the auction;
  applies Phase B/credit-refund penalties for incomplete teams, then **auto-applies
  the squad-cost levy** (average squad cost charged to wallets that didn't spend).
- **Publish snapshot** (`/admin/season/<id>/publish`): snapshots final squads +
  wallets to the public `/season/<slug>` page.
- **Undo last action** (`/admin/season/<id>/undo`): rolls back the last
  nomination/bid/close/etc. (auction_action_log).

### 2.5 Transfers (post-auction)
Visible once phase is `complete`/`transfers_open`:
- **Transfer** (`/admin/season/<id>/transfer`): move a sold player between teams
  with optional cash and/or credits. The action log + undo cover these too.

### 2.6 Bank adjust — `/admin/bank/adjust`
The catch-all money tool (used from the auction page's team boxes) — **the only
way to add balance** (players have no deposit form):
- **Account** (team/manager), **amount**, **reason**, direction (+/−).
- Positive amounts go through `credit()`: auto-mode accounts get the money
  routed **straight to their vault**; manual accounts get liquid cash. Negative
  amounts (fines) always come from liquid.
- This is how the admin grants credits, applies fines, and adds the
  "credit saved" deposits (just add balance + comment explaining it).

---

## 3. Scorer admin — `/admin/scorer?season=<id>` (matches blueprint)

### 3.1 Scorer config
- **Scorer settings** (`/admin/scorer/config`): `max_overs`, season slug, etc.
  saved to `config/scorer_config.json` and shipped to the offline scorer app.

### 3.2 Match registry
- **Register a match** (`/admin/scorer/registry`): match id, match number, title,
  `between` (teams), venue, date, walkover flag + winner.
- **Delete a registry entry** (`/admin/scorer/registry/delete`).
- The registry is the fixture schedule — unplayed registry matches drive the
  "remaining fixtures" in qualification scenarios and the matches index.

### 3.3 Import a scored match
- **Import** (`/admin/scorer/import`): upload the offline scorer's CSV. Validates
  the 28 required columns, maps local→global player/team ids (name fallback),
  aggregates `match_team_stats` / `match_player_stats`, stores the `delivery_log`.
  An overwrite of an existing match requires confirmation.
- **Undo import** (`/admin/scorer/undo`): rolls back the last import.

### 3.4 What finalizing a match does automatically
On import, `finance_service.on_match_finalized` runs **idempotently**:
- Credits the **match reward** (`match_reward_amount`, default 250) to **EVERY
  player wallet** (auto-vault accounts get it routed straight to the vault). One
  marker ledger entry guards the whole batch; undo reverses all wallets.
- **Catches up vault yield** (7% per match step, capped at Match 12) for all vault
  positions in the season.

### 3.5 Downloads
- The offline scorer is a standalone HTML app the scorer uses on their phone:
  `/scorer` (page) and `/scorer/download` (downloadable HTML). It exports the
  ball-by-ball CSV that this page imports.

---

## 4. Finance admin — `/admin/finances?season=<id>`

### 4.1 Budget board
Three sections (S2): **teams in this season → teams not in this season →
individual players**, each row showing wallet (liquid) + locked (vault). Plus
season totals (team wallets, vault, yield progress x/12) and the ledger feed
(rewards, adds/removes, transfers, vault locks, squad levy) with undo chips.

### 4.2 Actions
- **Fund all players** (`/admin/finances/fund-all`): credits every player's wallet
  with the universal funding (default 10k) — **idempotent** (players who never
  signed up get a wallet too; newly created wallets run on auto). This is the
  "everyone gets 10k before the S2 auction" step.
- **Squad levy** (`/admin/finances/levy`): manual fallback for the squad-cost levy
  (auto-runs on draft complete) — idempotent.
- **Adjust** (`/admin/finances/adjust`): add/remove funds for a team + comment.
- **Transfer** (`/admin/finances/transfer`): move funds team-to-team + comment.
- **Process pending** (`/admin/finances/process-pending`): backfills rewards +
  yield for finalized matches that were finalized before auto-finance existed.
- **Yield** (`/admin/finances/yield`): manually apply the vault yield catch-up.
- **Unlock vault** (`/admin/finances/unlock`): force-unlock a season's vault
  positions early (guarded by ≥12 finalized matches; checkbox bypasses).
- **Undo last entry** (`/admin/finances/undo`): roll back the newest ledger entry.

### 4.3 Vault mechanics (for reference)
- A player locks **liquid cash → locked capital** for a season
  (`/account/vault/lock` on their side).
- Yield: **7% per match**, compounding (yield added to locked capital) unless the
  position is in manual mode (`reinvest=false`), capped at Match 12.
- Positions unlock automatically once the season has 12 finalized matches (or
  admin force-unlock).

---

## 5. Wager admin — `/wagers/admin`

### 5.1 Market lifecycle
Every market moves through states; the admin page shows a **lifecycle stepper**
per market with the current stage highlighted and the next action inline:

| State | Meaning | Admin actions |
|---|---|---|
| `proposed` | A player proposed it with an opening stake | **Calibrate** (enter your probability estimate) |
| `calibrating` | Admin estimates collected; consensus = average | **Finalize calibration** (→ vetted) or **Veto** |
| `vetted` | Open for staking | **Freeze** (stop new bets before resolution) |
| `frozen` | Staking closed | **Unfreeze** or **Resolve** (pick winner) |
| `resolved` | Winner paid out from the pot | — |
| `voided` | Cancelled (stakes returned) | (from proposed/calibrating: **Veto**) |

### 5.2 House guarantee (automatic)
The House **automatically** tops up so winners get fair odds when the pot is
thin — no manual step required. The admin page shows the **live guarantee**
per calibrated market: "House covers: {side_a} win → N · {side_b} win → M".
It is recomputed on every view and the public board/detail poll it every 4s, so
it adjusts as stakes land on either side.

### 5.3 Other admin actions
- **Inject house** (`/wagers/admin/<id>/inject`): optionally pre-fund the pot
  with house funds (lowers the automatic guarantee); the guarantee itself is
  automatic either way.
- **Void** (`/wagers/admin/<id>/void`): cancel a market entirely (stakes returned).

---

## 6. Account linking — `/auth/admin/link`

New signups are **unlinked** until an admin links them to a global player:
- **Link**: pick an unlinked user → pick the global player (which grants the
  player role and a bank account).
- **Unlink**: revoke the link.
- Managers are linked the same way, then **assigned as manager of a team** (via
  `auth.assign_manager` — the manager role + team_id). The link page lists
  unlinked signups with a proper empty state when there are none.

---

## 7. Where the public/player-facing pages live (for context)

- Public: `/` (home), `/live` (auction board), `/matches`, `/matches/<season>/<id>`
  (scorecard + ball-by-ball), `/table` (+ qualification scenarios + NRR margin
  calculator), `/leaderboards`, `/teams`, `/players`, `/finances`, `/wagers`,
  `/season/<slug>` (published snapshot).
- Manager: `/manager` (dashboard: bid/pass, trades).
- Player: `/account` (vault, auto mode), `/wagers` (board + detail, propose/stake).

---

## 8. Typical admin workflows

**Run an auction**
1. `/admin` → create season → configure ruleset.
2. Add players, create teams (funds wallets).
3. Link + assign manager accounts.
4. `/admin/auction` → Set phase `phase_a_platinum` → Nominate next → managers bid
   on `/manager` → Close lot → advance phases → Complete draft → Publish.

**Score a match**
1. `/admin/scorer` → register the fixture in the registry.
2. Scorer plays the match in the offline app (`/scorer/download` on their phone).
3. Import the CSV → verify totals → the match reward + vault yield apply
   automatically.

**Grant a credit / fine**
1. `/admin/finances` (or the auction page) → **Adjust** → pick team, amount,
   direction, comment (e.g. "credit saved") — or use **Team gift** on the auction
   page. No separate feature needed.

**Run a wager**
1. A player proposes a market on `/wagers`.
2. Admin: Calibrate → Finalize → (optionally) Freeze.
3. Players stake; admin Freezes then Resolves (or Voids) → payouts are automatic.

**Onboard a new player**
1. They sign up on `/auth/signup` (role pending).
2. `/auth/admin/link` → link them to a global player → they can log in as a
   player, get an account, stake, use the vault.
3. If they manage a team: assign manager + team (auction setup).
