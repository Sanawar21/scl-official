# UI Test Coverage — What Has Been Tested in the Browser

All UI behavior is verified with **Playwright (pytest-playwright + Chromium)**
driving a real browser against the real Flask-SocketIO app. Each session boots
the app on a random port against a **fresh temp DB** (the real `data/scl.db` is
never touched) seeded with one season, 4 teams, users (admin / player / manager),
a finalized match (imported through the real CSV path), a published snapshot, and
a vetted wager.

- Suite: `tests/e2e/` — **65 browser tests** across 7 files.
- Full run (unit + e2e): `./.venv/Scripts/python.exe -m pytest tests/ -q` → **164**
- E2E only: `./.venv/Scripts/python.exe -m pytest tests/e2e/ -q`

The seed data is deterministic, so the tests assert real numbers (scores, NRR,
odds, wallet balances).

---

## 1. Baseline flows — `test_baseline.py` (13 tests)

The "can't regress the core" suite, originally written against the pre-redesign
UI and kept green through the redesign:

- Home + every public page renders (`/`, `/live`, `/matches`, `/table`,
  `/leaderboards`, `/teams`, `/players`, `/finances`, `/wagers`)
- Offline scorer downloads (`/scorer/download` headers)
- **Login redirects per role**: admin → overview, manager → team, player → account
- Signup creates an **unlinked** account
- Logout
- **Deposit flow** (adds to liquid cash)
- **Vault lock flow** (moves liquid → locked; "Locked until M12")
- **Admin bank adjust** (labeled form works end-to-end)
- Wagers board shows the seeded market; wager detail renders pools

## 2. Shell, auth, home — `test_shell_auth_home.py` (13 tests)

The design-system shell:

- **Role-aware nav** on desktop (different links per role)
- **Mobile drawer** opens/closes + backdrop click; **bottom action bar** shows the
  role's top actions; drawer links navigate
- **Flashes render as toasts** (error + signup success, auto-dismiss)
- Home page: anon hero CTAs; per-role quick-actions grids (player / manager /
  admin); empty-seasons state
- Auth: login page role explainer; signup page 3-step linking explainer

## 3. Public surfaces — `test_public_surfaces.py` (15 tests)

- **Live auction board**: phase stepper (incl. "Trade break"), budget board cards
  + card/table toggle, empty lot state
- **Matches**: result cards; **scorecard** (totals, call-up batting order, Fall of
  Wickets, result banner, PDF link)
- **Ball-by-ball**: over grid with ball chips, wicket styling, expandable
  delivery detail, FOW + partnerships, link from summary, "not available" state
- **League table**: champion + qualification zones, NRR column
- **Leaderboards**: tabbed panels (Runs → Wickets switch), top-3 podium
- **Profiles**: teams index → detail (record/played), player profile stats
- **Finances**: budget board cards + credits
- **Published season**: "Season complete" hero, final squads, **live player
  filter** by name

## 4. Player & manager surfaces — `test_player_manager_surfaces.py` (11 tests)

- **Account**: balance hero (liquid/locked), manager-only callout, unlinked
  banner, vault position card + reinvest toggle, transaction filter
- **Wagers**: board market card (pool bar, odds, status), detail pools + fair
  odds, **stake flow with live "You'd win X" preview**
- **Manager**: team hub stat row (wallet/credits/spent), squad XI vs Bench with
  empty state, bid controls render

## 5. Admin polish — `test_admin_polish.py` (7 tests)

- Overview: stat cards per area, recent-activity list
- **Bank adjust labeled form**
- **Auction action log + undo**
- **Wager lifecycle stepper** on the admin page
- Link page: empty state, and the **full signup → admin links → success** flow

## 6. Qualification scenarios + NRR predictor — `test_scenarios.py` (6 tests)

- **Scenarios card** on `/table`: per-team status chips + "what they need",
  "Top 1 qualify" + remaining-fixture count
- **Margin calculator**: direct-clash verdicts (batting-first margin + chase
  tables render), score change recalculation through the JSON endpoint
- **"What's at stake"** panel on the match summary: both teams, statuses, and the
  head-to-head margin hint

---

## What the e2e suite does NOT cover (deliberately)

- **Unit-level math** — covered by `tests/` (scorer import, NRR, qualification
  engine, vault yield, wager payouts, auth, etc.). E2E asserts visible outcomes;
  unit tests assert the numbers.
- **Real external services** — no payment gateway, email, or external APIs.
- **Production data** — every run uses a throwaway DB; nothing writes to
  `data/scl.db`.
- **The offline scorer app's gameplay** — it's a standalone HTML app; the tests
  cover its download + the admin import of its CSV output.

## How to add coverage

Follow the pattern in `tests/e2e/`: add a test to the relevant file (or a new
`test_*.py` with `pytestmark = pytest.mark.e2e`), use the `page`, `base_url`,
`seed`, and `login` fixtures from `tests/e2e/conftest.py`, and run
`pytest tests/e2e/<file> -q` to iterate.
