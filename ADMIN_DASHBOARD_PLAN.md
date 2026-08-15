# Admin Dashboard Consolidation — Plan (rev 2)

Status: **BUILT 2026-08-15** — 6 new tests (85 total green), E2E verified.
Decisions confirmed: tab shell (not one-page tabs), `/admin` is the Overview (auction moved
to `/admin/auction`), single Admin nav link. The individual player/manager dashboard is the
user's next increment (out of scope here).

Goal: give the admin a single overview of the whole app and a consistent shell to move
between the six admin surfaces, without touching any existing POST/redirect flow.

## Current state (the problem)

Six separate admin pages, no overview, inconsistent navigation:

| Page | URL | Blueprint | Content |
|---|---|---|---|
| Auction control room | `/admin` | admin | ruleset, players CRUD, teams/gifts/takeover, phase, transfers, manager assign, action log+undo, bank adjust |
| Scorer | `/admin/scorer` | matches | config, match registry CRUD, CSV import + undo |
| Finances | `/admin/finances` | admin | budget board, adjust/transfer, process-pending, yield, M12 unlock, ledger + undo |
| Wagers | `/wagers/admin` | wagers | calibration, veto, finalize, freeze, house inject, resolve/void |
| Link accounts | `/auth/admin/link` | auth | unlinked signups → player linking |

## Key design decision: tab *shell*, not one-page tabs

The reference app used a single page with `?tab=` hidden panels. The new app is
server-rendered with forms that POST → **redirect back to their own page**. A true
one-page tabbed UI would require editing ~35 redirects across 4 blueprints to keep the
active tab. Instead:

- **One shared tab bar** (Overview · Auction · Scorer · Finances · Wagers · Link) rendered
  at the top of every admin page. Each tab links to its existing URL; the active tab is
  highlighted. Clicking around is one tap, and every existing form/redirect keeps working
  unchanged (you land back on the page you were on, tab still highlighted).
- **A new Overview page** (`/admin/overview`) is the missing single overview + entry point.

## Build steps

### 1. Tab shell (`app/templates/admin/_tabs.html`)
- Jinja include taking `active_admin_tab`; renders the six tab links with `tag tag-active`
  on the active one (existing CSS classes).
- `_overview_context()`/`_admin_context()` etc. pass `active_admin_tab` from each route
  (tiny addition): `admin.dashboard` → `"auction"`, `admin.overview` → `"overview"`,
  `admin.finances` → `"finances"`, `matches.admin_scorer` → `"scorer"`,
  `wagers.admin` → `"wagers"`, `auth.link_page` → `"link"`.
- Insert `{% include "admin/_tabs.html" %}` under the page-head of: `admin/dashboard.html`,
  `admin/finances.html`, `admin/overview.html`, `matches/admin.html`, `wagers/admin.html`,
  `admin/link.html`.

### 2. Overview route + context (`admin.py`)
New `GET /admin/overview` (login_required admin). `_overview_context()` assembles status
cards by reusing existing services + a few direct queries (same style as `_finance_context`):
- **Auction**: current season, phase, teams count, players sold/unsold, current lot,
  phase-B readiness, published flag, last action-log entry.
- **Matches**: registry count, finalized (`match_stats`) count, unfinished diff, latest
  import + link, pending-finance count (finalized matches without a `match_reward` entry).
- **Finances**: Σ team wallets (board), house liquid, vault positions count, yield progress
  `max finalized match / 12`, credit-refund hint total.
- **Wagers**: wagers by status (proposed/calibrating/active/frozen/resolved/voided), Σ pots,
  house balance.
- **Accounts**: unlinked signups count, linked player count, total users.
- **Recent activity**: last ~8 auction actions + last ~8 scorer imports + last ~8 finance
  entries, newest first, in one feed.

### 3. Overview template (`app/templates/admin/overview.html`)
- Tab shell + status-card grid (`.grid`/`.card`), each card a small heading + key numbers +
  links into the section. Recent-activity feed at the bottom. Empty states when no season.

### 4. Nav simplification (optional, flagged)
- `base.html` admin links currently: Admin · Finances Admin · Scorer. With tabs everywhere,
  propose shrinking to a single **Admin → `/admin/overview`** link (the shell exposes the
  rest). Safe fallback: keep all three, tabs are additive either way.

### 5. Tests (`tests/test_admin_dashboard.py`)
- All six admin pages render 200 with the tab shell; the correct tab is active on each.
- `/admin/overview` shows the right numbers against a seeded season (team/player counts,
  registry vs finalized, wallet Σ, wager statuses, unlinked count) — seeded via `_setup` +
  a registered/finalized match + a wager + an unlinked user.
- Public pages still render (no tab shell leakage).

### 6. Docs + commit
- `ADMIN_DASHBOARD_PLAN.md` status, MEMORY/RESUME/PLAN updates, commit per milestone.

## Out of scope (per user)

- **Individual player / manager dashboard** (wagers, funds, other actions) — the user will
  add that as a separate increment. This plan only unifies the *admin* surface; the
  manager's page stays as-is (`/manager`, `/account`).
- Wager polish, fantasy entries, ball-by-ball view (later increments).

## Decisions to confirm

- **Q1 — tab shell vs reference-style one-page tabs.** Recommend the shell (zero redirect
  churn, same UX). One-page tabs are possible but invasive (~35 redirect edits).
- **Q2 — where Overview lives.** Recommend adding `/admin/overview` and keeping `/admin` as
  the auction page (no redirect churn). Alternative: swap — `/admin` becomes the overview
  and auction moves to `/admin/auction` (cleaner URL, ~25 redirect edits).
- **Q3 — nav simplification.** Shrink base.html admin links to a single "Admin" → overview,
  or keep the current three links alongside the tabs.
