# Player & Manager Reference — SCL Platform

Everything a player or a manager can do on the platform, where it lives, and how
the money works. Two roles:

- **Player** — an account (wallet + vault), wagers (propose markets, stake),
  and (optionally) the manager of a team.
- **Manager** — a player who owns a team: sits in the auction and bids, runs the
  squad, proposes trades. **The team's money IS the manager's wallet** — there is
  no separate team account; the manager's bank account is the team's money. In S2
  there is **no tier purse**: every player is funded with the universal 10k before
  the auction, and teams start from their manager's wallet.

Quick demo of everything: `scripts/seed_demo.py` (separate `data/demo.db`; the
real `data/scl.db` is never touched). Demo logins: `ayaan`/`bilal` (managers),
`cyrus`/`dania`/`farah`/`gul` (players), all password `demo123`.

---

## 1. Sign up, log in, linking

- **Sign up** (`/auth/signup`): username + password + display name. You start
  **unlinked** — an admin must link you to your global player before you can use
  the player features. The signup page explains the 3-step linking flow.
- **Log in** (`/auth/login`): normal session login. Role-specific nav appears in
  the top bar once you're in.
- Until linked, the account page shows: *"Ask an admin to link you…"*.

---

## 2. Your account — `/account`

### 2.1 Balance hero
- **Liquid cash** — spendable money (wager payouts, vault unlocks, match
  credits).
- **Locked capital** — money locked in the vault for a season.
- If you're a **manager**, a callout reminds you the wallet is the team's money.
- **You can't add money yourself** — only the admin can (grants via the admin
  dashboard, with a comment explaining it, e.g. "credit saved").

### 2.2 Auto mode (the hands-free option)
- A toggle card on `/account`: **Auto mode ON** means everything that comes in
  (admin grants, the universal 10k funding, the 250-per-match credit) goes
  **straight into your vault** and compounds at 7% per match — you never have to
  manage your liquid cash. Wallets created for players who never signed up are
  auto by default; **the moment the admin links your account to your player,
  auto is switched OFF** — linked accounts are manual, so you control your
  liquid cash (needed to bid or stake). Turn auto back on anytime from the
  toggle card. Admin grants to an auto account land in the vault, not liquid.

### 2.3 Start a team / My team
- **Start a team** (`/account` card): create a **persistent team account** — your
  team exists even if it's not playing this season. Its money is your wallet.
- **My team**: edit the team's name, logo, and about section; see which seasons
  it played. The admin registers it for a season when it's time to play.
- **Upload branding** (`/account` card): upload your **logo** and **wide banner**
  (JPG/PNG/WEBP/GIF, ≤5 MB). They appear across the platform — team page, league
  table, budget board, live board, your manager dashboard. If you haven't
  uploaded an asset, the **SCL brand is used by default** (you can remove your
  logo/banner any time to fall back to it).

### 2.4 Vault
- **Lock** (`/account/vault/lock`): move liquid cash → locked capital for a
  chosen season. Position options:
  - **Compounding (default)**: yield is added to locked capital each match, so it
    compounds. **Manual**: yield is tracked but not reinvested (locked stays flat).
- **Vault positions**: one per season — principal, locked value, mode, yield
  progress `X/12`, and an unlock chip.
- **Reinvest toggle** (`/account/vault/<id>/reinvest`): switch a position between
  compounding and manual at any time.
- **Yield**: **7% per match** applied when matches finalize, capped at Match 12.
  Positions unlock automatically once the season reaches 12 finalized matches
  (admin can force-unlock earlier).

### 2.6 Transactions
- Filterable table of every movement: vault locks, wager stakes + payouts,
  the 250 match credits, admin grants/fines.

---

## 3. Wagers — `/wagers`

### 3.1 Board
- **Market cards** per wager: question, sides, **pool bar** (Yes/No share of the
  pot), fair odds, pot total, number of bets, and a status chip
  (proposed / calibrating / vetted / frozen / resolved / voided).
- **House guarantee chip**: for calibrated markets, shows **how much the House
  covers if either side wins** (e.g. "House covers: Yes win → 750 · No win → 0").
  It's computed live from the pools and **auto-adjusts every 4s** as new stakes
  land on either side — no manual injection needed.
- **Propose a market** (collapsible flow): title, description, sides, your side,
  opening stake (must have liquid cash). Proposing opens a `proposed` market and
  places your opening bet.

### 3.2 Market detail
- **Pool visual**: Yes/No split with percentages + fair odds.
- **House guarantee banner**: the House automatically tops up so winners get
  fair odds — shows the live amount it covers if `side_a` wins vs if `side_b`
  wins, **refreshing every 4s** as stakes land.
- **Stake flow** (`/wagers/<id>/bet`): pick side + amount. A live **"You'd win
  X (stake Y at Zx)"** preview updates as you type/switch.
- **Bets** feed (everyone's bets) + **history** (lifecycle events).
- Status banners: awaiting calibration, results, voided.

### 3.3 Lifecycle (from the player's view)
1. **Proposed** — you opened it; the admin calibrates.
2. **Calibrating** — admin estimates being collected; you can't stake yet.
3. **Vetted** — staking open.
4. **Frozen** — admin stopped new bets before resolving.
5. **Resolved** — winner paid from the pot (your payout lands in liquid cash).
6. **Voided** — cancelled; stakes returned.

Staking requires an **admin-linked** account (players only — managers can also
stake with their wallet, same account).

---

## 4. Manager dashboard — `/manager`

### 4.1 Team hub
- **Wallet (purse) / Credits left / Spent** stat row — the live budget.
- **Squad**: **XI** (bought players) vs **Bench** cards, with proper empty states.
- Manager's own player is the roster's 4th slot (not bought in the auction).

### 4.2 Bidding (during auction phases)
The dashboard renders the current lot + a **bid action bar** (JS-driven, live via
`/manager/state`):
- **Quick bid**: one click at the next allowed amount (min = max(base,
  current + increment), +50 steps).
- **Custom bid**: enter any valid amount.
- **Pass** (`/manager/pass`): sit out the current lot.
- Bids are disabled when your wallet can't cover the amount, the team is under
  admin takeover, or the phase is wrong.
- The public **live board** (`/live`) shows the same auction in real time.

### 4.3 Trades (during the break phase)
- **Propose trade** (`/manager/trade`): pick the target team, the player you
  offer, the player you want (optional), and optional cash from either side.
- **Respond** (`/manager/trade/respond`): accept or decline incoming requests.
- Accepted trades swap players + move cash; credits recalculate automatically.

---

## 5. What a manager sees publicly

- **`/manager`** — the hub above.
- **`/live`** — the auction board (phase stepper, current lot, budget board).
- **`/matches/<season>/<id>`** — scorecard + ball-by-ball + **"What's at stake"**
  panel (your team's qualification status + the NRR margin you need this match).
- **`/table`** — league table + **Qualification scenarios** (status per team:
  Qualified / Safe / In contention / Eliminated + what each team needs) + the
  **Required margin calculator** (win-by-X / chase-in-Y to overtake a rival).
- **`/finances`** — public budget board + ledger.

---

## 6. Money model (read this once)

- One bank account per **player** (`player` owner). For managers that account is
  also the **team's money** — there is no purse; the manager's funding is the
  team's starting bank.
- **S2 funding**: every player gets the **universal 10k** before the auction
  (auto-created wallets default to auto mode → vaulted). No tier purse.
- **Liquid cash** moves: wager stake (−) / payout (+), match credit (+250 to
  every player per finalized match), admin grants/fines (+/−), auction close (−
  for bought players), trades, vault lock (−) / unlock (+). Only the admin can
  add balance (grants with a comment).
- **Squad-cost levy**: when the draft completes, the average squad cost is
  deducted from wallets that didn't spend in the auction (liquid first, then the
  vault for auto accounts).
- **Credits** are the draft budget (per-tier credits, total 8) — they only matter
  during the auction, not as money.
- Vault: liquid → locked for a season; **7%/match compounding yield**, unlock at
  12 finalized matches. Auto mode routes everything incoming straight to it.

---

## 7. Typical player/manager workflows

**Get set up**
1. Sign up → ask an admin to link you → log in.
2. (Manager) admin creates your team → the universal 10k funding is your wallet (or ask the admin to grant extra before the auction).

**Play the auction (manager)**
1. `/manager` — watch the lot.
2. Bid quick/custom, or pass; the admin closes lots and advances phases.
3. Build your XI; during the break, propose trades.

**Make money**
1. `/account` → the admin adds balance (grants/credit saved); lock some in the vault to earn 7%/match yield.
2. `/wagers` → stake on vetted markets (use the "you'd win" preview).
3. (Manager) match rewards are auto-paid to your wallet each finalized match.

**Know what's at stake**
1. `/table` → Qualification scenarios → Required margin calculator.
2. `/matches/<season>/<id>` → "What's at stake" panel before the match.
