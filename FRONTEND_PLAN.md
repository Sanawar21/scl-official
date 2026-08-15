# Frontend Transformation — Plan (rev 2)

Scope: rebuild the UX of the whole platform — new information architecture, page
structures, and action flows (not a recolor) — and add a Playwright UI suite that tests
everything end-to-end in a real browser.

## Decisions (locked 2026-08-15)

- **Q1 — Playwright driver: `pytest-playwright`** (Python; same runner, reuses `_setup`).
- **Q2 — Theme: light theme** (structure/flows change regardless; palette only).
- **Q3 — Mobile priority: mobile-first** (bottom action bar + drawer; the scorer use-case
  is phones).

## Data-parity guarantee (hard requirement from user)

**Every data point visible in the current UI must remain visible in the new UI.** The
redesign may change where/how it's shown (structure, grouping, hierarchy) — never remove
it. The audit below maps each current page's data to its new home; each phase's acceptance
criterion is that this table holds with nothing dropped.

| Current page / data | New home in the redesign |
|---|---|
| **Manager dashboard**: team name, takeover banner | Team hub header + takeover banner (kept) |
| Purse (wallet) · credits remaining · spent · phase | Stat row on team hub (wallet = purse note kept) |
| Current lot card (player, tier, current bid) | Hero lot card in bid flow |
| Bid controls (quick/custom/pass) + inline errors | Bid action bar (quick +increments, custom w/ validation, pass) |
| Squad: XI + bench chips (player_labels/bench_labels) | Squad as player cards (XI vs bench) |
| Trades panel: incoming w/ accept/reject, outgoing, request form | Trades panel during break (same actions, cleaner) |
| **Admin overview**: auction phase/teams/sold, registry/finalized/pending finance, wallet total, vault positions, yield x/12, wagers open/resolved, house liquid, unlinked/linked accounts, recent activity | Overview cards keep every number + activity feed |
| **Auction dashboard**: phase, lot, budget board (wallet, credits, roster, takeover), bid feed, lot bids, undo/action log | Auction tab: hero lot + phase stepper, budget as team cards, feeds as tabs, undo log kept |
| **Account**: liquid cash, locked capital, vault positions (principal, current value, yield x/12, unlock, reinvest), transactions ledger w/ running balance, link status | Balance hero + vault flow (position cards + yield progress) + filterable transactions + link banner |
| **Wagers board**: market cards (status, pot, side totals, odds, bet count) | Market cards w/ pool bar + fair odds; propose as collapsible flow |
| **Wager detail**: pool Yes/No, fair odds, stake flow, results/void callouts, bets + history | Same data; live "you'd win X" preview added |
| **Published season**: champion, squads (roster chips, purse/credits), all-players table | Champion hero, squad cards, filterable player table |
| **Match summary**: innings header (team/total/overs), batting w/ call-up order + status, FOW, bowling, extras, result banner, PDF link | Scorecard-style layout — same data, sticky result banner, actions row |
| **Public finances**: budget board (wallet/credits/roster), ledger feed w/ undo markers | Budget board as team cards + ledger icon feed |
| **League table**: standings, points, NRR, For/Against, tie-breaker note | Ranked rows w/ zone highlighting, same numbers |
| **Leaderboards**: BAT/BOWL/FANTASY/SR/ECON/TEAM boards | Tabbed boards w/ top-3 podium, same metrics |
| **Team/player profiles**: header (name/tier/spec/season), stat tiles, by-season tables | Header card + stat tiles + by-season tables (kept) |
| **Live board**: lot, phase, budget table, two feeds | Hero lot + phase stepper + tabbed feeds (same data) |
| **Home**: hero, published seasons, live seasons | Role-aware dashboard (seasons still listed) |
| **Auth**: login/signup flows | Card flows + "awaiting admin link" state (kept) |

## Design principles (apply everywhere)

- **Mobile-first, server-rendered, progressively enhanced** (keeps the stack; JS only adds
  live updates / async actions where it already exists).
- **One consistent design system** in `app.css`: tokens (colors/type/space/radius), a
  primary/secondary button language, status chips with a fixed semantic map
  (phase, wager status, match result), cards, empty states, toasts.
- **Role-aware navigation**: every page knows who's looking (anon / player / manager /
  admin) and shows the right primary actions — a single mental model for the whole app.
- **Every action is a flow, not a buried form**: propose → confirm → feedback (flash
  toast), destructive actions always confirmed, async actions (bid, vault lock) show
  inline success/error with no page jump.
- **All existing routes and POST endpoints stay unchanged** — the redesign is templates +
  CSS + JS + flow markup. Server-rendered content still works without JS.

## Part 1 — Global shell (`base.html`, `app.css`, `app.js`)

**Now:** one top nav row (11 links), flat flash list, plain container.
**New:**
- **Header nav** collapsed into role-aware groups with a mobile menu (hamburger drawer on
  small screens; the desktop row stays). Brand → home.
- **Mobile bottom action bar** for the 3-4 highest-value actions per role
  (e.g. player: Account · Wagers · Home; manager adds My Team).
- **Flash → toast system** (auto-dismiss, icon per category) — replaces the stacked list.
- **Page header component**: title + subtitle + primary action slot + back/breadcrumb on
  detail pages; consistent across every template.
- **Status chip component** with a semantic color map (one source of truth, reused in
  templates): phases, wager lifecycle, match results, vault/yield states.
- Keep the **admin tab shell** (Overview · Auction · Scorer · Finances · Wagers · Link).

## Part 2 — Home (`viewer/home.html`)

**Now:** hero + two list cards.
**New:** a role-aware landing dashboard:
- Hero with the season status + primary CTA (live board / my account / my team).
- **Quick actions grid** per role (anon: live board, matches, table, sign up;
  player/manager: account, wagers, my team).
- **Season picker** + latest match results snippet (finalized matches from the registry).
- Published seasons + live seasons as tappable cards.

## Part 3 — Live auction board (`viewer/live.html` + `app.js`)

**Now:** 2×2 card grid (lot, phase, budget table, two feeds).
**New:**
- **Hero lot card**: big player name/tier, current bid, phase stepper showing where the
  auction is (phase order with the active step highlighted).
- **Budget board as team cards** (wallet + credits + roster count, takeover badge) instead
  of a dense table; toggle to a compact table on wide screens.
- **Bid feed / lot bids** as tabbed panels with timestamps.
- Live polling stays (4s); renderers upgraded to the new markup.

## Part 4 — Published season (`viewer/published.html`)

**New:** champion/result hero, final squads as team cards (roster chips + purse/credits),
all-players table with a client-side filter (tier/team/status).

## Part 5 — Matches (`matches/index.html`, `summary.html`, `admin.html`)

- **Index**: fixtures as result cards (teams, venue, date, result badge, walkover tag)
  instead of a flat table; season switch kept.
- **Summary**: scorecard-style — innings header (team, total, overs), batting list with
  the call-up order + status, FOW line, bowling list, extras; sticky result banner;
  actions row (scorecard PDF, back to fixtures).
- **Admin scorer**: keep the tabs; organize into clear cards (config / add match /
  import CSV with a 3-step feel: choose season → pick match → upload; registry list with
  status badges).

## Part 6 — League table (`matches/table.html`)

**New:** standings as **ranked rows/cards** with position (1-4 highlighted as
championship/qualification zones), points + NRR prominent, expandable "For/Against",
tie-breaker note; keep the season switch.

## Part 7 — Leaderboards (`matches/leaderboard.html`)

**New:** tabbed boards (BAT · BOWL · FANTASY · SR · ECON · TEAM) with top-3 podium
highlighting, rank badges, links into player profiles.

## Part 8 — Team & player profiles (`teams/detail.html`, `players/detail.html`,
`teams/index.html`)

- **Profile header card**: name, tier/speciality badges, season affiliation, back link.
- **Stat tiles** (already good) restyled into a hero stat row; by-season tables kept with
  season switcher chips.
- **Index pages**: cards with name + key stat + link (teams: record; players: role/tier).

## Part 9 — Public finances (`matches/finances.html`)

**New:** Budget Board as team cards with wallet + credits + roster count (progress bars
for wallet vs tier purse where known); ledger as an icon feed (reward/add/remove/transfer)
with undo markers; season switch; link to the finance admin for admins.

## Part 10 — Wagers (`wagers/board.html`, `detail.html`, `admin.html`)

- **Board**: market cards — title, status chip, pot + side totals with a **pool bar**,
  fair odds, bet count; "Propose a market" as a clean collapsible flow with live liquid
  cash shown and the opening-side select syncing with side names.
- **Detail**: pool visual (Yes/No bar), fair odds, **stake flow** with a side toggle +
  amount + live pot preview ("you'd win X"); results/void callouts; bets + history as tabs.
- **Admin**: keep the tab shell; each market as a stepper showing where it is in the
  lifecycle (proposed → calibrating → vetted → frozen → resolved/voided) with the next
  action prominent.

## Part 11 — Banking / account (`banking/account.html`)

**New:**
- **Balance hero**: liquid cash + locked capital as big tiles; manager note ("your wallet
  is your team's purse") as a callout.
- **Vault**: deposit/lock flow (amount + season + mode) with instant feedback; position
  cards showing principal, current locked value, yield progress (match X of 12), unlock
  badge, and the reinvest toggle inline.
- **Transactions**: filterable (type/date) ledger with the current balance in each row.
- **Link status banner** for unlinked accounts ("Ask an admin to link you" + path).

## Part 12 — Manager dashboard (`manager/dashboard.html`)

**New:**
- **My team hub**: wallet/credits/spent stat row; takeover banner kept; squad as player
  cards (XI vs bench).
- **Bid flow**: current lot card + a bid action bar (quick +increment buttons, custom
  amount with validation, pass) with inline error/disabled reasons — cleaner than the
  generated buttons today, same endpoints.
- **Trades**: a dedicated panel during break — incoming requests with accept/reject
  buttons, outgoing with status, request form; clear empty states outside break.

## Part 13 — Admin (`admin/overview.html`, `dashboard.html`, `finances.html`, `link.html`)

- Keep the tab shell and all flows (just built); restyle overview status cards with
  counts + primary links, make every form consistent (labels above fields, row layout),
  keep the action log + undo prominent.

## Part 14 — Auth (`auth/login.html`, `signup.html`)

**New:** centered card flows with role explainers; signup success → "account created,
awaiting admin link" state with a clear next step; login supports `next` redirect (kept).

## Part 15 — Offline scorer (`scorer/scorer.html`)

**Untouched** — it's a deliberately standalone offline app; only the download links on
the site point to it.

## New cross-cutting flows (delivered with the redesign)

1. **Toast feedback** for every server action (replaces stacked flashes).
2. **Confirmation** for destructive admin actions (delete player/team, undo, veto).
3. **Live liquid-cash previews** in wager forms (stake → "you'd win X").
4. **Empty states** on every list (already present; standardized).
5. **Phase/status steppers** (auction phases, wager lifecycle) as a reusable component.

## Playwright UI test plan

- **Driver**: `pytest-playwright` (Python, fits the existing pytest suite and `conftest`
  `_setup` seeding) + Chromium. Server booted per session by a fixture on a random port
  against a **temp DB** (never the real `data/scl.db`). See Q1.
- **Layout**: `tests/e2e/` with a `conftest.py` (server + seeded-db fixtures, `page`,
  `base_url`, role-login helpers, seed helpers mirroring `_setup`).
- **Files** (one per area, ~40-60 cases total):
  1. `test_nav_auth.py` — nav per role (anon/player/manager/admin), mobile drawer,
     signup → login → admin-links account → role changes.
  2. `test_auction_flow.py` — admin creates season/players/teams, sets phase, nominates;
     manager bids/passes/custom-bid; budget board + bid feed update; step-back + undo.
  3. `test_wagers_flow.py` — propose market (stake), admin calibrate/veto, peer stake,
     resolve → payouts; freeze/void path.
  4. `test_banking_vault.py` — deposit, vault lock (compound + manual), reinvest toggle,
     position progress, transactions list.
  5. `test_matches_stats.py` — registry via admin UI, scorer CSV upload (file chooser),
     summary renders (batting order, FOW), table numbers, leaderboards, scorecard PDF
     download asserted via download event.
  6. `test_finances.py` — admin adjust/transfer/undo + ledger; public budget board.
  7. `test_admin_overview.py` — tab shell, overview numbers, link-accounts flow.
  8. `test_live_viewer.py` — live board renders + polls, published season page.
- **Baseline first**: run a Playwright smoke over the *current* UI before redesign to lock
  the flows (catches breakage from the redesign), then the full suite against the new UI.
- **Keeping the pytest suite green**: server-rendered pages mean existing route-smoke tests
  that assert exact markup (e.g. overview `Teams: <strong>4</strong>`) will be updated to
  the new structure as part of each phase.

## Delivery order (phased, committed per phase)

1. ✅ **Phase 0 — Playwright infra + baseline smoke tests** (DONE 2026-08-15): pytest-playwright
   + Chromium installed (`requirements.txt`), `tests/e2e/conftest.py` (session-scoped server on
   a temp DB + `login`/`base_url`/`seed` helpers), `tests/e2e/test_baseline.py` — 13 smoke tests
   over the current UI (auth per role, deposit/vault, admin adjust, wagers, public pages,
   scorer download). **98 tests total green.**
2. ✅ **Phase 1 — Shell + design system** (DONE 2026-08-15): `app.css` light-theme tokens +
   components (buttons, semantic chips/tags, cards, stat tiles, quick actions, empty states,
   toasts); `base.html` role-aware nav (desktop row + mobile drawer + bottom action bar);
   flash→toast JS in `app.js`; Home → role-aware landing (hero + season status chip, quick
   actions grid per role, latest results from the match registry, published + seasons cards);
   Auth → card flows with role explainers + linking steps. 13 new e2e tests (111 total).
3. **Phase 2 — Public surfaces**: live board, published, matches, table, leaderboards,
   team/player profiles, public finances.
4. **Phase 3 — Player/manager surfaces**: banking/vault, wagers board/detail, manager
   dashboard.
5. **Phase 4 — Admin polish** (overview cards, form consistency) + final pass.
6. **Phase 5 — Full Playwright suite green + docs (MEMORY/RESUME/PLAN) + commit.**

## Delivery cadence

One iteration = one phase, committed and verified (Playwright suite for that phase green)
before the next begins. No phase bundles two areas that could ship separately.
