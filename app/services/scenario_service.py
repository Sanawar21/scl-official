"""Qualification scenarios + NRR predictor (ported from scl-nrr-calc).

Tells you, before a match, what each team needs to top the table, reach the
qualification cut, or overtake a rival on NRR. The math is a straight Python
port of the standalone tool's script.js (calculate / qualStatus / buildReq /
calcBattingFirst / calcChasing), but the input comes from the platform's own
tables instead of pasted JSON:

- standings/NRR: aggregated from `match_team_stats` (reuses scorer_service's
  league_table for positions + display, then keeps exact run/ball totals for
  the margin math)
- remaining fixtures: the FULL double round-robin schedule over the season's
  teams (each pair plays twice) MINUS played matches — derived from the teams
  table, not from registered fixtures, so a season whose admin only registered
  played matches still knows its upcoming fixtures. Registered unplayed
  entries are attached when available (for match ids); extra knockout matches
  (a final) are never part of the qualification schedule. A season with extra
  knockout matches has a final → top-2 qualify, otherwise top-1, i.e. S2 where
  the champion is the table topper.
"""

import itertools
from collections import Counter

from ..db import row_to_dict, rows_to_dicts

WIN_POINTS = 2
TIE_POINTS = 1
ROUNDS_PER_PAIR = 2


def _exact_nrr(runs_for, balls_for, runs_against, balls_against):
    rr_for = (runs_for * 6.0 / balls_for) if balls_for else 0.0
    rr_against = (runs_against * 6.0 / balls_against) if balls_against else 0.0
    return rr_for - rr_against


def _format_overs(balls):
    return f"{balls // 6}.{balls % 6}"


def _format_nrr(n):
    if abs(n) < 0.0005:
        return "+0.000"
    return ("+" if n >= 0 else "") + f"{n:.3f}"


def _format_rate(r):
    return f"{r:.3f}"


class ScenarioService:
    def __init__(self, db, scorer_service):
        self.db = db
        self.scorer = scorer_service

    # ------------------------------------------------------------------
    # data
    # ------------------------------------------------------------------
    def _max_balls(self) -> int:
        try:
            ctx = self.scorer.build_scorer_context()
            return max(1, int(ctx["scorer_config"].get("max_overs") or 3)) * 6
        except Exception:
            return 18

    def _registry(self, season_id):
        with self.db.read() as conn:
            rows = conn.execute(
                "SELECT match_key, match_id, match_number, match_title, \"between\", "
                "team_a_global_id, team_b_global_id, walkover "
                "FROM match_registry WHERE season_id = ?", (season_id,)).fetchall()
        return rows_to_dicts(rows)

    def _played_keys(self, season_id):
        with self.db.read() as conn:
            rows = conn.execute(
                "SELECT DISTINCT match_key FROM match_stats WHERE season_id = ?",
                (season_id,)).fetchall()
        return {r["match_key"] for r in rows}

    def qualify_count(self, season_id: str) -> int:
        """Seasons with knockout matches (registry exceeds the double
        round-robin count) have a final → top-2 qualify; otherwise top-1
        (S2: champion = table topper)."""
        registry = self._registry(season_id)
        n_teams = len(self._season_team_ids(season_id))
        rr_matches = n_teams * (n_teams - 1)  # double round robin
        return 2 if len(registry) > rr_matches else 1

    def _season_team_ids(self, season_id: str) -> list:
        """Distinct participating team ids for the qualification schedule, in
        the id space the season's registry uses (global ids when the registry
        references them, otherwise the per-season team id — tests and legacy
        data may use either)."""
        registry_ids = set()
        for e in self._registry(season_id):
            for ref in (e.get("team_a_global_id"), e.get("team_b_global_id")):
                ref = (ref or "").strip()
                if ref:
                    registry_ids.add(ref)
        with self.db.read() as conn:
            rows = conn.execute(
                "SELECT id, global_team_id FROM teams WHERE season_id = ?",
                (season_id,)).fetchall()
        seen, ids = set(), []
        for r in rows:
            sid = (r["id"] or "").strip()
            gid = (r["global_team_id"] or "").strip()
            if gid and gid in registry_ids:
                canonical = gid
            elif sid and sid in registry_ids:
                canonical = sid
            else:
                canonical = gid or sid
            if canonical and canonical not in seen:
                seen.add(canonical)
                ids.append(canonical)
        return ids

    @staticmethod
    def _pair_key(a, b):
        a = (a or "").strip()
        b = (b or "").strip()
        if not a or not b:
            return None
        return tuple(sorted((a, b)))

    def remaining_fixtures(self, season_id: str) -> list:
        """Unplayed fixtures of the qualification schedule.

        The schedule is the full double round-robin over the season's teams
        (every pair plays ROUNDS_PER_PAIR times) MINUS the played matches —
        derived from the teams table rather than the registry, so a season
        whose admin only registered the played fixtures still knows its
        upcoming ones. A registered unplayed entry is attached to a slot when
        one exists (keeps real match ids); otherwise a placeholder pair is
        returned (only the pairing matters for the qualification math). Extra
        knockout matches (e.g. a final) are never part of this schedule."""
        registry = self._registry(season_id)
        played = self._played_keys(season_id)
        team_ids = self._season_team_ids(season_id)

        played_per_pair = Counter()
        reg_by_pair = {}
        for e in registry:
            key = self._pair_key(e.get("team_a_global_id"), e.get("team_b_global_id"))
            if not key:
                continue
            reg_by_pair.setdefault(key, []).append(e)
            if e["match_key"] in played:
                played_per_pair[key] += 1

        remaining = []
        for pair in itertools.combinations(sorted(team_ids), 2):
            played_n = played_per_pair.get(pair, 0)
            for round_no in range(1, ROUNDS_PER_PAIR + 1):
                if played_n >= round_no:
                    continue
                slot = next((e for e in reg_by_pair.get(pair, [])
                             if e["match_key"] not in played), None)
                if slot:
                    reg_by_pair[pair].remove(slot)
                    remaining.append(slot)
                else:
                    remaining.append({
                        "match_key": None, "match_id": None, "match_number": None,
                        "match_title": None, "between": None, "walkover": None,
                        "team_a_global_id": pair[0], "team_b_global_id": pair[1],
                    })
        remaining.sort(key=lambda e: (e.get("match_number") or e.get("match_id") or "").lower())
        return remaining

    def scenarios(self, season_id: str) -> dict:
        """Standings + remaining fixtures + per-team qualification status."""
        standings = self.scorer.league_table(season_id)
        if not standings:
            return {"season_id": season_id, "teams": [], "remaining": [],
                    "qualify_count": 0, "complete": True, "rows": []}
        remaining = self.remaining_fixtures(season_id)
        top_n = self.qualify_count(season_id)
        team_remaining = Counter()
        for e in remaining:
            team_remaining[e.get("team_a_global_id")] += 1
            team_remaining[e.get("team_b_global_id")] += 1

        rows = []
        for s in standings:
            tid = s["team_id"]
            rem = team_remaining.get(tid, 0)
            max_pts = s["points"] + rem * WIN_POINTS
            q = self._qual_status(s, standings, remaining, top_n, rem, max_pts)
            rows.append({
                "team_id": tid, "team_name": s["team_name"], "slug": s.get("slug", ""),
                "position": s["rank"], "played": s["played"], "wins": s["wins"],
                "losses": s["losses"], "draws": s["draws"], "points": s["points"],
                "max_points": max_pts, "nrr": s["nrr"], "nrr_display": s.get("nrr_display", ""),
                "remaining": rem, "status": q["label"], "status_cls": q["cls"],
                "requirement": q["req"],
            })
        return {"season_id": season_id, "teams": [s["team_name"] for s in standings],
                "remaining": remaining, "qualify_count": top_n,
                "complete": not remaining, "rows": rows}

    # ------------------------------------------------------------------
    # qualification status engine (port of qualStatus + buildReq)
    # ------------------------------------------------------------------
    def _qual_status(self, s, standings, remaining, top_n, rem_count, max_pts):
        # tournament complete
        if not remaining:
            if s["rank"] <= top_n:
                return {"label": "Qualified", "cls": "success",
                        "req": "Tournament complete — qualified."}
            return {"label": "Eliminated", "cls": "danger",
                    "req": "Tournament complete — did not qualify."}
        # mathematically eliminated: top_n teams already above max possible
        above = sum(1 for t in standings if t["team_id"] != s["team_id"]
                    and t["points"] > max_pts)
        if above >= top_n:
            return {"label": "Eliminated", "cls": "danger",
                    "req": f"Cannot reach top {top_n} — max possible {max_pts}pts "
                           f"is already beaten by {top_n} teams."}
        # already in the cut and safe (no team below can match, even with all wins)
        if s["rank"] <= top_n:
            overtake = [t for t in standings if t["rank"] > top_n
                        and self._team_remaining(t["team_id"], remaining) * WIN_POINTS
                        + t["points"] >= s["points"]]
            if not overtake:
                return {"label": "Safe", "cls": "success",
                        "req": f"Position #{s['rank']} is safe. No team below can "
                               f"match {s['points']}pts."}
        return {"label": "In contention", "cls": "info",
                "req": self._build_req(s, standings, remaining, top_n, rem_count, max_pts)}

    def _team_remaining(self, team_id, remaining):
        return sum(1 for e in remaining
                   if e.get("team_a_global_id") == team_id
                   or e.get("team_b_global_id") == team_id)

    def _build_req(self, s, standings, remaining, top_n, rem_count, max_pts):
        if rem_count == 0:
            return f"{s['points']}pts final — position #{s['rank']}."
        if s["rank"] <= top_n:
            threats = [t for t in standings if t["rank"] > top_n
                       and self._team_remaining(t["team_id"], remaining) * WIN_POINTS
                       + t["points"] >= s["points"]]
            if not threats:
                return f"Currently #{s['rank']}. Maintain position."
            names = ", ".join(t["team_name"] for t in threats)
            return (f"Currently #{s['rank']}. {names} can still overtake — "
                    f"winning remaining match{'es' if rem_count > 1 else ''} "
                    f"and strong NRR recommended.")
        # below the cut
        in_cut = [t for t in standings if t["rank"] <= top_n]
        leader_min = min(t["points"] for t in in_cut) if in_cut else max_pts
        if max_pts == leader_min:
            return (f"Must win all {rem_count} remaining to reach {max_pts}pts, "
                    f"then NRR decides vs tied teams.")
        if max_pts < leader_min:
            return f"Cannot reach required points (max: {max_pts}pts). Eliminated."
        if rem_count == 1:
            pts_after_win = s["points"] + WIN_POINTS
            sim = sorted(
                [{"name": t["team_name"], "points": (pts_after_win if t["team_id"] == s["team_id"]
                                                     else t["points"]),
                  "nrr": t["nrr"]} for t in standings],
                key=lambda x: (-x["points"], -x["nrr"]))
            sim_pos = next(i + 1 for i, t in enumerate(sim) if t["name"] == s["team_name"])
            same_pts = [t["team_name"] for t in standings
                        if t["team_id"] != s["team_id"] and t["points"] == pts_after_win]
            if sim_pos <= top_n and not same_pts:
                return f"Win remaining match → {pts_after_win}pts. Qualifies (est. #{sim_pos})."
            if sim_pos <= top_n and same_pts:
                return (f"Win remaining match → {pts_after_win}pts (est. #{sim_pos}). "
                        f"Tied on points with {', '.join(same_pts)} — NRR is the decider.")
            return (f"Win remaining match → {pts_after_win}pts (est. #{sim_pos}). "
                    f"May not be enough; NRR margin required. See calculator below.")
        wins_needed = max(1, leader_min + 1 - s["points"]) // WIN_POINTS + (
            1 if (leader_min + 1 - s["points"]) % WIN_POINTS else 0)
        if wins_needed >= rem_count:
            return (f"Must win all {rem_count} remaining matches "
                    f"({s['points']}pts → {max_pts}pts). NRR likely the decider.")
        return (f"Must win at least {wins_needed} of {rem_count} remaining matches; "
                f"NRR may be decisive.")

    # ------------------------------------------------------------------
    # required margin calculator (port of calcBattingFirst/calcChasing)
    # ------------------------------------------------------------------
    def match_stakes(self, season_id: str, match_id: str) -> dict:
        """'What's at stake' for a specific fixture: per-team qualification
        status + a margin hint to overtake the team directly above."""
        sc = self.scenarios(season_id)
        registry = self._registry(season_id)
        fixture = next((e for e in registry
                        if (e.get("match_id") or "").lower() == (match_id or "").lower()), None)
        if not fixture or not sc.get("rows"):
            return None
        a_id = fixture.get("team_a_global_id")
        b_id = fixture.get("team_b_global_id")
        if not a_id or not b_id:
            return None
        rows = {r["team_id"]: r for r in sc["rows"]}
        if a_id not in rows or b_id not in rows:
            return None
        teams = [rows[a_id], rows[b_id]]
        out = []
        for t in teams:
            entry = {"team_name": t["team_name"], "slug": t.get("slug", ""),
                     "status": t["status"], "status_cls": t["status_cls"],
                     "requirement": t["requirement"], "position": t["position"],
                     "points": t["points"], "nrr_display": t["nrr_display"]}
            # margin hint: overtake the team ranked directly above (if any)
            above = [r for r in sc["rows"] if r["position"] < t["position"]]
            rival = above[0] if above else None
            opponent = rows[b_id] if t["team_id"] == a_id else rows[a_id]
            if rival:
                mc = self.margin_calc(season_id, t["team_name"], opponent["team_name"],
                                      rival["team_name"], 30)
                if mc:
                    bf = mc["batting_first"]
                    if mc["direct"]:
                        entry["margin_hint"] = (
                            f"Head-to-head vs {rival['team_name']} (#{rival['position']}): "
                            f"win by {bf['min_margin']}+ runs batting first "
                            f"(opponent ~30) to come out ahead on NRR.")
                    else:
                        entry["margin_hint"] = (
                            f"To overtake {rival['team_name']} (#{rival['position']}) "
                            f"on NRR: win by {bf['min_margin']}+ if batting first "
                            f"(opponent ~30).")
            out.append(entry)
        return {"season_id": season_id, "match_id": match_id,
                "complete": sc["complete"], "qualify_count": sc["qualify_count"],
                "teams": out}

    def margin_calc(self, season_id, team_name, opponent_name, rival_name, opp_score):
        """What Team needs to overtake Rival on NRR in a match vs Opponent.

        Returns batting-first (win margin) + chasing (max balls) verdicts,
        both for the direct-clash (rival == opponent) and 3rd-party cases.
        """
        standings = self.scorer.league_table(season_id)
        if not standings:
            return None
        by_name = {s["team_name"]: s for s in standings}
        if team_name not in by_name or opponent_name not in by_name or rival_name not in by_name:
            return None
        if team_name == opponent_name or team_name == rival_name:
            return None
        team_a = self._stats_view(by_name[team_name])
        opponent = self._stats_view(by_name[opponent_name])
        rival = self._stats_view(by_name[rival_name])
        opp_score = max(0, int(opp_score or 0))
        bf = self._calc_batting_first(team_a, opponent, rival, opp_score)
        ch = self._calc_chasing(team_a, opponent, rival, opp_score)
        return {
            "team": team_name, "opponent": opponent_name, "rival": rival_name,
            "opp_score": opp_score, "max_balls": self._max_balls(),
            "direct": opponent_name == rival_name,
            "batting_first": bf, "chasing": ch,
        }

    def _stats_view(self, s):
        balls_for = int(s.get("balls_for") or 0)
        balls_against = int(s.get("balls_against") or 0)
        return {
            "name": s["team_name"], "position": s["rank"], "points": s["points"],
            "runsScored": int(s.get("runs_for") or 0), "ballsFaced": balls_for,
            "runsConceded": int(s.get("runs_against") or 0), "ballsBowled": balls_against,
            "runRateScored": (s.get("runs_for", 0) * 6.0 / balls_for) if balls_for else 0.0,
            "runRateConceded": (s.get("runs_against", 0) * 6.0 / balls_against) if balls_against else 0.0,
            "nrr": _exact_nrr(int(s.get("runs_for") or 0), balls_for,
                              int(s.get("runs_against") or 0), balls_against),
        }

    def _calc_batting_first(self, team_a, opponent, rival, opp_score):
        """Opponent scores S in MAX_BALLS; Team scores S+M in MAX_BALLS."""
        max_balls = self._max_balls()
        is_direct = opponent["name"] == rival["name"]
        s = opp_score
        a1 = 6.0 / (team_a["ballsFaced"] + max_balls)
        a2 = 6.0 / (team_a["ballsBowled"] + max_balls)

        if is_direct:
            b1 = 6.0 / (rival["ballsFaced"] + max_balls)
            b2 = 6.0 / (rival["ballsBowled"] + max_balls)
            rhs = ((rival["runsScored"] + s) * b1
                   - (rival["runsConceded"] + s) * b2
                   - (team_a["runsScored"] + s) * a1
                   + (team_a["runsConceded"] + s) * a2)
            crossover = rhs / (a1 + b2)
        else:
            target = rival["nrr"]
            rhs = target + (team_a["runsConceded"] + s) * a2 \
                - (team_a["runsScored"] + s) * a1
            crossover = rhs / a1

        min_margin = max(1, int(crossover) + 1)

        margins = [1, 2, 3, 4, 5, 6, 8, 10, 15, 20, 25, 30]
        if min_margin > 1:
            margins += [min_margin - 1, min_margin, min_margin + 1]
        margins = sorted({m for m in margins if m >= 1})[:10]

        table = []
        for m in margins:
            my_score = s + m
            nrr_a = ((team_a["runsScored"] + my_score) * 6.0 / (team_a["ballsFaced"] + max_balls)
                     - (team_a["runsConceded"] + s) * 6.0 / (team_a["ballsBowled"] + max_balls))
            if is_direct:
                nrr_b = ((rival["runsScored"] + s) * 6.0 / (rival["ballsFaced"] + max_balls)
                         - (rival["runsConceded"] + my_score) * 6.0 / (rival["ballsBowled"] + max_balls))
            else:
                nrr_b = rival["nrr"]
            table.append({"margin": m, "my_score": my_score, "nrr_a": nrr_a,
                          "nrr_b": nrr_b, "beats": nrr_a > nrr_b + 0.0005})
        return {"min_margin": min_margin, "crossover": crossover,
                "table": table, "opp_score": s, "direct": is_direct}

    def _calc_chasing(self, team_a, opponent, rival, target):
        """Opponent bats first and scores target in MAX_BALLS; Team chases
        target+1 in b balls (1 ≤ b ≤ MAX_BALLS)."""
        max_balls = self._max_balls()
        is_direct = opponent["name"] == rival["name"]
        my_score = target + 1
        table = []
        max_b = 0
        for b in range(1, max_balls + 1):
            nrr_a = ((team_a["runsScored"] + my_score) * 6.0 / (team_a["ballsFaced"] + b)
                     - (team_a["runsConceded"] + target) * 6.0 / (team_a["ballsBowled"] + max_balls))
            if is_direct:
                nrr_b = ((rival["runsScored"] + target) * 6.0 / (rival["ballsFaced"] + max_balls)
                         - (rival["runsConceded"] + my_score) * 6.0 / (rival["ballsBowled"] + b))
            else:
                nrr_b = rival["nrr"]
            beats = nrr_a > nrr_b + 0.0005
            if beats:
                max_b = b
            if b in (1, 3, 6, 9, 12, 15, 17, max_balls):
                table.append({"balls": b, "overs": _format_overs(b),
                              "nrr_a": nrr_a, "nrr_b": nrr_b, "beats": beats})
        if max_b and not any(r["balls"] == max_b for r in table):
            b = max_b
            nrr_a = ((team_a["runsScored"] + my_score) * 6.0 / (team_a["ballsFaced"] + b)
                     - (team_a["runsConceded"] + target) * 6.0 / (team_a["ballsBowled"] + max_balls))
            nrr_b = ((rival["runsScored"] + target) * 6.0 / (rival["ballsFaced"] + max_balls)
                     - (rival["runsConceded"] + my_score) * 6.0 / (rival["ballsBowled"] + b)) \
                if is_direct else rival["nrr"]
            table.append({"balls": b, "overs": _format_overs(b),
                          "nrr_a": nrr_a, "nrr_b": nrr_b, "beats": True})
            table.sort(key=lambda r: r["balls"])
        return {"max_balls": max_b,
                "any_win_sufficient": max_b >= max_balls,
                "impossible": max_b == 0,
                "table": table, "target": target, "my_score": my_score, "direct": is_direct}
