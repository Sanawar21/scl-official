# Qualification Scenarios + NRR Predictor — Integration Plan

Source: `C:\Users\sanaw\Downloads\scl-nrr-calc (2)\scl-nrr-calc\` — a standalone client-side
tool (index.html + script.js + style.css) that computes NRR, standings, **qualification status**
(what each team needs to top the table / make top-2) and a **required-margin calculator**
(win by X runs batting first, or chase Y within Z balls, to overtake a rival on NRR).

Goal: bring that "before the match, which team needs what" capability into the platform,
feeding it **real data automatically** (no JSON pasting), styled with the current design system.

---

## 1. What the tool does (review)

All logic lives in `script.js` as pure functions — no server, no persistence:

| Function | Purpose |
|---|---|
| `calculate(rawData)` | Builds per-team stats from match JSON: played/won/lost/tied/points, runs scored & faced, runs conceded & bowled → run rates + NRR, sorted standings (pts → NRR → name), and **remaining fixtures** (12-match double round robin minus played) |
| `qualStatus()` / `buildReq()` | Per team: `Qualified ✓` / `Safe ✓` / `In Contention` / `Eliminated` + a plain-English requirement line ("must win all 3 remaining…", "win remaining match → 8pts, qualifies est. #2…") |
| `calcBattingFirst()` | Given opponent's assumed score S: what **win margin M** (batting first, full 18 balls) puts Team A's NRR above the rival's — direct-clash (rival = opponent) or 3rd-party (rival NRR fixed) |
| `calcChasing()` | Given opponent's target S: what's the **max balls** Team A can take chasing S+1 to still beat the rival on NRR |
| PDF | jsPDF report (nice-to-have; low priority — the platform already has a scorecard PDF path) |

Hardcoded assumptions: 4 teams, 3 overs (18 balls), 12 matches (double round robin), **top 2**
qualify, 2 pts win / 1 tie / 0 loss.

## 2. How it maps to the platform (no pasting needed)

Everything the tool asks for already exists in SQLite:

- **Per-match results**: `match_team_stats` — `runs_scored, balls_faced, runs_conceded,
  balls_bowled, wins, losses, ties, no_results, result` per team per match. This is exactly the
  tool's input shape; `league_table()` already aggregates most of it.
- **Fixture schedule / remaining matches**: `match_registry` (13 entries for season-1, incl. the
  walkover M6) — played vs not-yet-played is `match_stats` present or not.
- **Overs per innings**: season ruleset / scorer config (`max_overs`, default 3 → 18 balls).
- **Teams**: `teams` per season (4 for S1; the platform supports 4+).

So the port is: **read from DB instead of pasted JSON**, keep the math identical.

## 3. Architecture

**Server-side Python port** (matches the stack; testable with pytest; e2e-testable):

- New `app/services/scenario_service.py` — pure functions ported from `script.js`:
  - `standings(season_id)` → per-team stats + NRR + sorted positions (can reuse/reconcile with
    `scorer_service.league_table` — keep one source of truth, ideally build on the existing agg).
  - `remaining_fixtures(season_id)` → unplayed registry matches (team pair, match number).
  - `qualification(season_id)` → per team: status + requirement string + max points (port
    `qualStatus`/`buildReq`).
  - `margin_calc(team, opponent, rival, opp_score)` → batting-first min margin + chase max-balls
    verdicts (port `calcBattingFirst`/`calcChasing`; direct vs 3rd-party cases).
- **One JSON endpoint** (`GET /table/scenarios/calc?...`) returning the margin tables, so a small
  JS handler can recompute interactively (same fetch pattern app.js already uses for the auction).
- **Template**: a new section rendered inside the existing `/table` (league table) page — see
  placement below. Styled with existing components (cards, chips, stat-tiles, `.table-wrap`).

**Generalization (small):** read `MAX_TEAMS`/`MAX_BALLS`/`TOP_N` from the season's ruleset +
scorer config instead of constants. S1 = 4 teams / 18 balls / top-2. S2 (champion = table topper,
no final) → top-1. Decision needed: add a `qualify_count` field to the ruleset (default 2) or
derive: `2 if the season has a final else 1`.

## 4. Placement (where it lives in the UX)

Recommendation — three surfaces, one engine:

1. **League table page (`/table`)** — the natural home (that's where NRR + standings already are).
   Add a **"Qualification scenarios"** card above/below the table: one row per team with a status
   chip (`Qualified` / `Safe` / `In contention` / `Eliminated`) + the plain-English requirement
   ("Must win all 2 remaining; NRR likely the decider", "Win M12 by 6+ runs to top the table"…).
   This is the tool's Qualification Summary, fed live.

2. **Required Margin Calculator** — same page, collapsible card under the scenarios table:
   pick Analyzing Team / Opponent / Target Rival + assumed opponent score → shows the two verdicts
   (batting-first min margin table; chase balls table) exactly like the tool. Interactivity via
   the JSON endpoint (small JS, no page reload).

3. **Per-match "What's at stake" callout** — on each match summary page
   (`/matches/<season>/<id>`), a compact panel above the scorecard: for the two teams in that
   fixture, "X tops the table with a win" / "Y must win by 7+ to overtake Z for 2nd" — the
   literal "before the match" use case. Same service, one query.

(Admin/scorer surfaces untouched; PDF report optional later — the scorecard PDF path exists.)

## 5. Edge cases to handle

- **Walkovers** (S1 M6): no delivery data; counts as a win/loss for NRR? Decide: treat walkover as
  played, 2 pts to the winner, **no NRR change** (no balls). Note: the tool has no walkover
  concept — the port must.
- **No-result / abandoned**: same treatment (0 pts each, no NRR change) — S1 has none, but S2 may.
- **Unplayed registry matches with no `match_stats` row** → remaining fixtures.
- **Season complete**: all scenarios collapse to final positions (tool's "Tournament complete").
- **Tie on points**: NRR decider, then name (port the exact comparator).
- **0 balls faced/bowled** → guard against division by zero (platform already does this).

## 6. Test plan

- **Unit** (`tests/test_scenario_service.py`): standings/NRR on S1 real data cross-checked against
  the current `/table` output; qualification statuses for a mid-season snapshot (use the e2e seed:
  M1 played of 12 → in-contention states); margin calc sanity: direct-clash and 3rd-party cases,
  `impossible` / `anyWinSufficient` branches; walkover/no-result handling; complete-season collapse.
- **E2E** (`tests/e2e/test_scenarios.py`): `/table` shows the scenarios card with real teams;
  status chips render; the margin calculator recomputes via the JSON endpoint (fill + click →
  verdict text appears); match summary shows the "What's at stake" panel for the seeded M1.

## 7. Delivery (one commit per increment)

1. **Engine**: `scenario_service.py` (standings + remaining + qualification) + unit tests.
2. **Scenarios card + margin calculator on `/table`**: template section, JSON endpoint, small JS,
   e2e tests.
3. **Per-match "What's at stake"** on match summary + e2e.
4. **Ruleset generalization** (`qualify_count` / top-N for S2) + docs + final suite.

No schema changes beyond the optional `qualify_count` ruleset column (additive, defaulted).

## 8. Decisions (locked with user 2026-08-15)

- **Placement**: table page + per-match callout — scenarios card & margin calculator on `/table`,
  plus a compact "What's at stake" panel on each match summary page.
- **Top-N**: season-aware switch, **no schema change** — seasons with a final use top-2 (S1),
  seasons without (S2, champion = table topper) use top-1. Determined by the season's
  ruleset/phase flow (if the season has a final/relegation step → 2, else 1).
- **PDF report**: deferred — not part of the initial delivery.

## 9. Delivery (revised per decisions)

1. **Engine**: `scenario_service.py` — standings + remaining fixtures + qualification status
   (top-N from the season-aware switch) + margin calc, ported from `script.js` + unit tests.
2. **Scenarios card + margin calculator on `/table`**: template section, JSON endpoint, small
   JS, e2e tests.
3. **Per-match "What's at stake"** on match summary + e2e.
4. **Final pass**: full suite + docs + commit.

---

## Status: ✅ COMPLETE (2026-08-15)

All four increments shipped. Engine in `app/services/scenario_service.py`; `/table` scenarios
card + margin calculator (JSON endpoint `/table/scenarios/calc`); "What's at stake" panel on
match summaries. 11 unit + 6 e2e tests; full suite 164 green. Screenshots:
`data/shot-scenarios-table.png`, `data/shot-scenarios-stakes.png`.

Deferred (per decision): jsPDF report. Natural next step if wanted: richer margin-hint
phrasing using the actual fixture's remaining schedule, or per-team "path to top" steps.
