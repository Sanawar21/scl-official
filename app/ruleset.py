"""Per-season ruleset model.

Wraps a rulesets row (JSON fields decoded) and answers flow questions:
which tier phases exist, what phase comes next, purse/base/credit lookups.
"""
from . import rules as R
from .db import json_loads


class Ruleset:
    def __init__(self, row: dict):
        self.id = row["id"]
        self.season_id = row["season_id"]
        self.phase_order = json_loads(row.get("phase_order"), R.DEFAULT_PHASE_ORDER)
        self.tier_purses = json_loads(row.get("tier_purses"), R.DEFAULT_TIER_PURSES)
        self.tier_base_prices = json_loads(row.get("tier_base_prices"), R.DEFAULT_TIER_BASE_PRICES)
        self.tier_credits = json_loads(row.get("tier_credits"), R.DEFAULT_TIER_CREDITS)
        self.total_credits = int(row.get("total_credits") or R.TOTAL_CREDITS)
        self.bid_increment = int(row.get("bid_increment") or R.BID_INCREMENT)
        self.phase_b_price = int(row.get("phase_b_price") or R.PHASE_B_PRICE)
        self.credit_refund_rate = int(row.get("credit_refund_rate") or R.CREDIT_REFUND_RATE)
        self.required_players = int(row.get("required_players") or R.REQUIRED_PLAYERS)
        self.roster_size = int(row.get("roster_size") or R.ROSTER_SIZE)
        self.break_minutes = int(row.get("break_minutes") or R.BREAK_MINUTES)
        self.match_reward_amount = int(row.get("match_reward_amount") or R.MATCH_REWARD_AMOUNT)

    # --- phase flow -------------------------------------------------------
    def tier_phases(self) -> list:
        return [R.tier_phase(t) for t in self.phase_order if t in R.TIERS]

    def flow(self) -> list:
        """The ordered list of phases (setup/complete/transfers excluded)."""
        phases = []
        for item in self.phase_order:
            if item in R.TIERS:
                phases.append(R.tier_phase(item))
            elif item == "break":
                phases.append(R.PHASE_BREAK)
            elif item == "phase_b":
                phases.append(R.PHASE_B)
        return phases

    def next_phase(self, current: str):
        flow = self.flow()
        if current == R.PHASE_SETUP:
            return flow[0] if flow else R.PHASE_COMPLETE
        if current in flow:
            idx = flow.index(current)
            if idx + 1 < len(flow):
                return flow[idx + 1]
            return R.PHASE_COMPLETE
        if current == R.PHASE_COMPLETE:
            return R.PHASE_TRANSFERS
        return None

    def can_skip_break(self, current: str) -> bool:
        """Whether a break phase may be skipped by admin (manual advance)."""
        return current == R.PHASE_BREAK

    # --- tier lookups -----------------------------------------------------
    def purse_for(self, tier: str) -> int:
        return int(self.tier_purses.get(tier, 0) or 0)

    def base_price_for(self, tier: str) -> int:
        return int(self.tier_base_prices.get(tier, 0) or 0)

    def credits_for(self, tier: str) -> int:
        return int(self.tier_credits.get(tier, 0) or 0)

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "season_id": self.season_id,
            "phase_order": list(self.phase_order),
            "tier_purses": dict(self.tier_purses),
            "tier_base_prices": dict(self.tier_base_prices),
            "tier_credits": dict(self.tier_credits),
            "total_credits": self.total_credits,
            "bid_increment": self.bid_increment,
            "phase_b_price": self.phase_b_price,
            "credit_refund_rate": self.credit_refund_rate,
            "required_players": self.required_players,
            "roster_size": self.roster_size,
            "break_minutes": self.break_minutes,
            "match_reward_amount": self.match_reward_amount,
        }
