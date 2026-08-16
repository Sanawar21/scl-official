# CLI Reference — SCL Platform

Every command you need to run, test, and demo the platform. All commands assume
you're in the project root and use the project's virtualenv
(`.venv/Scripts/python.exe` on Windows; `.venv/bin/python` on macOS/Linux — the
`VENV` alias below covers both).

> ⚠️ **Golden rule:** the production DB is `data/scl.db`. Everything except the
> scripts that *explicitly* target it (`import_prod`, `reset_balances`) runs
> against a temp or demo DB. Use `SCL_DB_PATH` (NOT `SCL_DB` — that env var
> does nothing and silently hits the default DB) to point anywhere else.

```bash
# Windows (Git Bash)
VENV="./.venv/Scripts/python.exe"
# macOS / Linux
VENV="./.venv/bin/python"
```

---

## 1. Setup

```bash
# Create the virtualenv + install dependencies (one-time)
python -m venv .venv
./.venv/Scripts/pip install -r requirements.txt

# Install the Playwright Chromium browser (needed for e2e tests)
./.venv/Scripts/python -m playwright install chromium
```

Verify setup: `$VENV -m pytest tests/ -q` — expect **226 passing** (137 unit + 89 e2e).

---

## 2. Demo environment (sample data, safe)

Builds a fresh `data/demo.db` — **never touches the real DB**. Re-run anytime
to reset (the demo DB is deleted and rebuilt).

```bash
# 1. Seed the demo database (prints login credentials at the end)
$VENV scripts/seed_demo.py                      # -> data/demo.db
$VENV scripts/seed_demo.py data/other.db        # custom path

# 2. (Re)generate the participant document PDFs (served from /docs)
$VENV scripts/generate_docs.py                 # all four -> app/static/docs/*.pdf
$VENV scripts/generate_docs.py rulebook       # just one (rulebook|vault|wagers|economy)

# 3. Run the app against the demo DB
SCL_DB_PATH=data/demo.db $VENV run.py
#   -> http://localhost:10001
#   /docs + /docs/<slug> + /docs/<slug>/pdf, /changelog (public), /admin/changelog
#   /admin/teams — the team branding control panel (logo/banner uploads)
#   /branding/... — SCL + team brand assets are served read-only from data/brandings/

# Easier: put your settings in .env (copy .env.example) and just run:
$VENV run.py
#   app/config.py auto-loads .env at startup; real env vars always win over it.
```

### Demo logins (all password `demo123`)

| Role     | Username          | Linked to            |
|----------|-------------------|----------------------|
| Admin    | `admin`           | —                    |
| Manager  | `ayaan`           | Lions (Ayaan)        |
| Manager  | `bilal`           | Tigers (Bilal)       |
| Player   | `cyrus`           | Cyrus (gold)         |
| Player   | `dania`           | Dania (gold)         |
| Player   | `farah`           | Farah (silver — the live lot) |
| Player   | `gul`             | Gul (silver)         |

### What's in the demo
- Season + 4 teams + 12 players
- Partial auction mid-draft: **live silver lot with a real bid** (Farah) — try
  the admin auction control (close lot, nominate next, set phase), then log in
  as a manager and bid/pass
- Wagers in flight, bank accounts + vaults funded, published season snapshot
- **Branding**: Lions + Tigers have uploaded logos/banners (see `/admin/teams`);
  Eagles/Falcons fall back to the SCL brand

### Try these URLs
- **Admin:** `/admin` (overview, auction control, scorer, finances, wager admin,
  link accounts, changelog) and `/admin/teams` (team branding control panel)
- **Manager:** `/manager` (bid/pass on the live lot, propose trades)
- **Player:** `/account` (vault lock/reinvest, auto mode), `/wagers` (propose a market, stake)
- **Public:** `/`, `/live`, `/matches`, `/table`, `/leaderboards`, `/teams`, `/players`, `/finances`, `/wagers`

---

## 3. Running the app

```bash
# Default (production data, data/scl.db)
$VENV run.py                       # -> http://localhost:10001

# Against any other DB
SCL_DB_PATH=path/to.db $VENV run.py

# Other env vars (see app/config.py)
SCL_SECRET_KEY=... SCL_ADMIN_USERNAME=... SCL_ADMIN_PASSWORD=... $VENV run.py

# Or set them once in .env (copy from .env.example) — no CLI passing needed:
#   SCL_SECRET_KEY=<random>
#   SCL_DB_PATH=data/demo.db
#   SCL_ADMIN_USERNAME=admin
#   SCL_ADMIN_PASSWORD=demo123
```

---

## 4. Tests

The Playwright e2e suite boots its own server on a random port against a fresh
temp DB — **you don't need to start the server first**, and `data/scl.db` is
never touched.

```bash
# Full suite (unit + e2e): 226 tests
$VENV -m pytest tests/ -q

# Unit tests only
$VENV -m pytest tests/ -q -m "not e2e"

# E2E browser tests only
$VENV -m pytest tests/e2e/ -q

# A single file
$VENV -m pytest tests/e2e/test_scenarios.py -q

# A single test (by name)
$VENV -m pytest tests/e2e/test_scenarios.py -q -k "calculator"

# Stop on first failure + show full tracebacks (while iterating)
$VENV -m pytest tests/ -q -x --tb=short

# Run a scratch check with the temp DB isolated — always set SCL_DB_PATH first!
SCL_DB_PATH=data/scratch.db $VENV - <<'PY'
from app import create_app
app = create_app({"DB_PATH": "data/scratch.db"})
print("ok")
PY
```

---

## 5. Maintenance scripts

```bash
# Reset all team balances to zero (new economic system).
# Idempotent; appends one balance_reset txn per account. REFUSES to write without --yes.
$VENV scripts/reset_balances.py --db data/scl.db            # dry run (no write)
$VENV scripts/reset_balances.py --db data/scl.db --yes      # actually reset

# Fund every player with the universal 10k before the S2 auction (idempotent;
# auto-creates wallets for players who never signed up). Same as the admin
# "Fund all players" button on /admin/finances. REFUSES to write without --yes.
$VENV scripts/fund_players.py --db data/scl.db                      # dry run (no write)
$VENV scripts/fund_players.py --db data/scl.db --amount 10000 --yes # actually fund

# Import deployed Season 1 data (prod-data/) into a fresh rebuild DB.
# Refuses to run if the target DB already has imported rows (unless --force).
$VENV scripts/import_prod.py [--data prod-data] [--db data/scl.db] [--force]
$VENV scripts/import_prod.py --phase stats [--data prod-data] [--db data/scl.db] [--force]
```

---

## 6. Everyday git (this repo's workflow)

```bash
git status --short          # what changed
git diff                    # review unstaged changes
git add -A && git commit -m "message"
git log --oneline -10       # recent history
```

---

## 7. Useful gotchas (from MEMORY.md)

- **`SCL_DB_PATH`, never `SCL_DB`** — `SCL_DB` is not read; a script setting it
  silently writes to the default `data/scl.db`.
- **`.env` is auto-loaded** (app/config.py, no dependency): `#` comments, quoted
  values, optional `export ` prefix. Real env vars always win. `.env` is
  gitignored; `.env.example` is the documented template. Maintenance scripts
  (`reset_balances`, `fund_players`, `import_prod`) take `--db` explicitly and
  ignore `.env`'s DB path — good, they must never be silently redirected.
- **CSS uppercases headings** — Playwright `inner_text` assertions on headings
  must use the uppercased form (e.g. `CURRENT LOT`).
- **e2e seed details**: match M1 has a `delivery_log` (ball-by-ball page works);
  M2 is registry-only (empty-state page).
- The real DB sanity baseline: 1 season, 5 users, 17 players, 4 teams, 13 matches.
