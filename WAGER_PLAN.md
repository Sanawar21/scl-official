# Wager Platform — Increment Plan

Goal: the SCL prediction market per `wager.txt` (SCL Wager & Risk Management Protocol): pooled
Yes/No AMM markets with admin calibration, solvency veto, peer betting, house injection/guarantee,
and resolution or voided refunds. Wager stakes draw from each player's **liquid cash** in the
central bank (already built in Increment 1).

Status: **IMPLEMENTED (Increment 5)** — 34/34 tests pass, E2E verified. Deviations from this plan,
none: payout model confirmed with the user as proportional + House guarantee. See `MEMORY.md` for
the build summary. Reference docs: `wager.txt`, `MEMORY.md`, `PLAN.md`.
Reference implementation to port mechanics from: `../SCL/app/services/economy_service.py` +
`../SCL/app/routes/economy.py` (conceptual port — see §7).

---

## 1. Rules from the protocol (`wager.txt`)

Lifecycle: **Initiation → Calibration → Financial Veto → Peer Phase → House Injection → Resolution**

1. **Initiation** — a manager proposes a condition and places the first stake.
2. **Calibration** — admins assign an objective probability to lock the pool ratios.
3. **Financial Veto** — mandatory check that the market can't risk club solvency.
4. **Peer Phase** — other players/managers enter Yes or No pools.
5. **House Injection** — admins balance pools with House funds to ensure fair payouts.
6. **Resolution** — the winning side **splits the total pot proportionally**.
7. **House Guarantee** — if peer interest is insufficient to meet the risk-adjusted payout
   (e.g. a 25% underdog needs 3:1), the House injects the missing funds so winners are fully paid.
8. **Bankruptcy veto** — admins may cap/cancel any market threatening a club's long-term
   participation (leaving a club unable to pay for the upcoming draft).
9. **Voided markets** — ambiguous/impossible conditions or suspected fixing → 100% refunds.
10. **Dynamic risk** — mid-wager news → pools **frozen**; a Phase-2 market may open with updated odds.
11. **Consensus** — if admins disagree on the probability, the **mathematical average of blind
    estimates** is used.

## 2. Payout model (key design decision)

**Proportional split of the pot, with a House guarantee floor** (doc-faithful; differs from the old
app's fixed-odds model — see §7):

- Calibration sets `p_b` = house probability of Side B (0–100 exclusive); `p_a = 100 − p_b`.
- Fair multiplier for a side: `fair(side) = 100 / p(side)` (e.g. 25% underdog → 4.0x).
- **Pot** = Σ yes stakes + Σ no stakes + house injections.
- At resolution, winning bets are credited:
  - `guaranteed(side) = Σ winning stake × fair(side)` — the House guarantee.
  - If `pot ≥ guaranteed` → each winner gets `stake × pot / Σ winning stakes`
    (proportional split; can exceed fair odds — that's the upside of a fat pot).
  - If `pot < guaranteed` → the House tops up by `guaranteed − pot`, and each winner gets
    exactly `stake × fair(side)` (fully rewarded underdog).
- **Solvency guard**: before a resolution that needs a top-up, the required injection is checked
  against the House account balance; insufficient funds block resolution with a clear error.
  Admins then either inject first or veto/void the market.

## 3. Schema additions (`app/schema.py`)

```sql
CREATE TABLE IF NOT EXISTS wagers (
  id TEXT PRIMARY KEY,
  season_id TEXT REFERENCES seasons(id),        -- optional scope (e.g. match markets)
  title TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  side_a TEXT NOT NULL DEFAULT 'Yes',
  side_b TEXT NOT NULL DEFAULT 'No',
  status TEXT NOT NULL DEFAULT 'proposed',       -- proposed|calibrating|vetted|frozen|resolved|voided
  accepting_bets INTEGER NOT NULL DEFAULT 0,
  initiator_user_id TEXT,                        -- who proposed (users.id)
  initiator_name TEXT NOT NULL,                  -- display snapshot
  house_probability REAL,                        -- p(Side B), locked at calibration end
  calibration_estimates TEXT NOT NULL DEFAULT '[]',  -- [{admin, estimate}] for consensus
  house_injected INTEGER NOT NULL DEFAULT 0,     -- Σ house funds added to the pot
  winning_side TEXT,
  veto_reason TEXT,
  void_reason TEXT,
  history TEXT NOT NULL DEFAULT '[]',            -- [{at, action, actor, note}] audit trail
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  resolved_at TEXT
);

CREATE TABLE IF NOT EXISTS wager_bets (
  id TEXT PRIMARY KEY,
  wager_id TEXT NOT NULL REFERENCES wagers(id),
  user_id TEXT NOT NULL,                         -- users.id
  username TEXT NOT NULL,                        -- display snapshot
  side TEXT NOT NULL,                            -- side_a or side_b label
  amount INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'open',           -- open|settled|refunded
  payout INTEGER,                                -- credited at settle/refund
  stake_tx_id TEXT,                              -- bank_transactions.id (deduction)
  created_at TEXT NOT NULL,
  settled_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_wagers_status ON wagers(status);
CREATE INDEX IF NOT EXISTS idx_wager_bets_wager ON wager_bets(wager_id);
CREATE INDEX IF NOT EXISTS idx_wager_bets_user ON wager_bets(user_id);
```

**Money rails already exist — no bank schema change:**
- Player stakes/payouts/refunds → `bank_accounts` (`owner_type='player'`, `owner_id=global_player_id`)
  via `bank_service.adjust(account_id, ±amount, comment, tx_type=...)`.
- House account → a plain `bank_accounts` row `owner_type='house'`, `owner_id='house'`, topped up by
  the existing admin bank-adjust form (already supports `owner_type:owner_id` refs — `house:house`).
- New `bank_transactions.type` values (no column change): `wager_stake`, `wager_payout`,
  `wager_refund`, `house_inject`.

## 4. Wager state machine

```
proposed ──(admin calibrate)──▶ calibrating ──(finalize)──▶ vetted ──▶ (betting open)
   │                                │                          │
   └──(veto)──▶ voided              │                          ├──(freeze)──▶ frozen ──(unfreeze)──▶ vetted
                                    │                          │
                                    └────────(veto)──▶ voided   ├──(resolve)──▶ resolved
                                                                └──(void)────▶ voided
```

- `proposed`: initiator created the market and placed the first stake (money already deducted).
- `calibrating`: admins submit blind estimates; consensus (mean) updates `house_probability`
  live; admin **Finalize** locks odds and opens betting (`vetted`, `accepting_bets=1`).
- `vetoed` (bankruptcy veto): all open stakes refunded 100%; `veto_reason` recorded. Market dead.
- `frozen`: `accepting_bets=0` (mid-wager news); unfreeze reopens, or resolve/void from frozen.
  A "Phase 2 market" is simply a **new wager** created with updated odds (UI hint on frozen cards).
- `resolved`: payouts per §2, bets settled.
- `voided`: ambiguous/impossible/fixing → 100% refunds, `void_reason` recorded.

Guards: betting only when `status='vetted'` and `accepting_bets=1`; resolve/void only from
`vetted`/`frozen`; veto only from `proposed`/`calibrating`; a market can only be resolved once.

## 5. Service — `app/services/wager_service.py` (`WagerService(db)`)

Port the *mechanics* of `../SCL/app/services/economy_service.py` onto the central bank:

- `create_wager(season_id, title, description, side_a, side_b, user, side, amount)` —
  validates linked player + liquid cash; creates wager (status `proposed`) + first bet;
  deducts stake via bank (`tx_type='wager_stake'`). Mirrors "manager proposes + first stake".
- `calibrate(wager_id, admin_user, estimate)` — appends a blind estimate, recomputes consensus
  (mean of all estimates) into `house_probability`; status → `calibrating`. Consensus rule per §1.11.
- `finalize_calibration(wager_id, admin_user)` — locks odds, status → `vetted`,
  `accepting_bets=1`. (Also the **financial veto gate**: admin can veto here instead.)
- `veto(wager_id, reason, actor)` — status → `voided` (bankruptcy veto); refunds all open stakes.
- `place_bet(wager_id, user, side, amount)` — validates status/accepting, side, positive amount,
  liquid cash ≥ amount; deducts stake, inserts bet.
- `inject_house(wager_id, amount, actor)` — house account → pot (`house_injected += amount`;
  `tx_type='house_inject'`). Solvency check on the house account balance.
- `freeze(wager_id, actor)` / `unfreeze(wager_id, actor)` — toggle `accepting_bets`/status.
- `resolve(wager_id, winning_side, actor)` — implements §2: compute pot, guaranteed payouts,
  required top-up; **block with error if top-up > house balance**; credit winners
  (`tx_type='wager_payout'`), settle bets, status → `resolved`.
- `void(wager_id, reason, actor)` — refund all open stakes (`tx_type='wager_refund'`),
  status → `voided`.
- Queries: `list_wagers(season_id=None)`, `get_wager(id)` (with pooled totals + bets),
  `my_bets(user_id)`, `house_account()`.
- Every mutation appends a `history` entry (actor/action/note) — the audit trail. All money ops go
  through `bank_service.adjust` so `bank_transactions` is the single ledger.

## 6. Routes & screens

**`app/routes/wagers.py`** (`Blueprint("wagers", url_prefix="/wagers")`), registered in
`create_app`; nav link "Wagers" in `base.html` for logged-in (non-admin) users:

- `GET /wagers` — market board (all statuses; pool totals, odds, pot per market).
- `POST /wagers` — create market + first stake (`@login_required`, user must be linked).
- `POST /wagers/<id>/bet` — place stake (`@login_required`, linked).
- `GET /wagers/<id>` — detail: description, calibration status, pool bars (Yes/No + pot),
  house-injected amount, bet list, current user's bets.
- Admin (all `@login_required(role='admin')`, one admin panel page):
  - `GET /wagers/admin` — control room: markets needing calibration, veto queue, resolve/void.
  - `POST /wagers/admin/<id>/calibrate` · `/finalize` · `/veto` · `/freeze` · `/unfreeze`
    · `/inject` · `/resolve` · `/void`.

**Templates** (`app/templates/wagers/`): `board.html`, `detail.html`, `admin.html` —
mobile-first, reuse existing `.card`, `.table-wrap`, `.chip`, `.tag` styles; pool bars as simple
two-tone progress rows; JSON-free form posts + redirects (matches admin/manager conventions).

**Authorization**: betting requires a **linked** account (`global_player_id` set) — the bank
account is per player; managers and players are both eligible (a player one season is a manager
the next). Unlinked users see the board read-only. Admins don't bet.

## 7. Port notes vs. the old app

- Old `economy_service.py` kept a **separate per-username balance** (starting 100) — the rebuild
  instead draws from the **central bank** (`liquid_cash`); port the mechanics (calibration odds,
  veto/refund shapes, resolve loop), not the accounts.
- Old resolution paid fixed odds `100 / house_probability` per winning bet. The new model (§2)
  is **proportional pot split + House guarantee floor**, which matches the protocol text
  ("winning side splits the total pot proportionally", "House injects to guarantee the winner is
  fully rewarded"). Flagged as the one deliberate deviation — confirm in review.
- Old app had no explicit calibration/veto/freeze steps; those come from `wager.txt` and are new.

## 8. Tests — `tests/test_wager.py`

Reuse `tests/conftest.py` (`_setup` + a linked manager/player account with known liquid cash):

1. Create wager with first stake → status `proposed`, stake deducted, `wager_stake` tx logged.
2. Betting blocked before finalize (not `vetted`).
3. Calibration: two blind admin estimates → `house_probability` = their average (consensus).
4. Finalize → `vetted`, accepting; bet placed → pool totals update; insufficient balance rejected.
5. Veto → all stakes refunded (`wager_refund`), status `voided`, no further bets.
6. Freeze → betting blocked; unfreeze → allowed again.
7. House injection → `house_injected` increases; house account debited.
8. Resolve with balanced pot → winners split pot pro-rata; losers get nothing.
9. **House guarantee**: lopsided pools (underdog side) → resolution auto-top-up; winners get
   `stake × fair(side)` exactly; blocked if house balance insufficient.
10. Void (ambiguous condition) → 100% refunds; status `voided`.
11. Lifecycle guard rails: resolve twice / bet after resolve / void after resolve all rejected.
12. Ledger integrity: total deductions = Σ stakes; total payouts + refunds reconcile.

Run: `./.venv/Scripts/python.exe -m pytest tests/ -q` (existing 19 must stay green).

## 9. Build order

1. Schema: `wagers`, `wager_bets` (+ indexes) in `app/schema.py`.
2. `WagerService` + payout/guarantee math (pure helper first, unit-testable).
3. Routes + templates (board/detail/admin) + nav link; house account bootstrap (create on demand).
4. Tests (§8); then `pytest` + boot server, manual E2E: create → calibrate → bet → inject →
   resolve; and a void path.
5. Polish: pool bars, "my bets" summary, Phase-2-market hint on frozen cards.

## 10. Out of scope (later)

- Wager **undo** via the auction action log (refunds are the revert path today; a wager action log
  can be added if admin undo for wagers is wanted).
- Live socket updates for the board (polling pattern from `app.js` can be reused when the matches
  increment brings the live scoreboard).
- Auto-resolution from match results (comes with the scorer/matches increment).
