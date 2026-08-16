"""Default ruleset values (Season 2 docs) and phase vocabulary.

Season rules are fluid, so the auction engine reads everything from a per-season
ruleset row; these constants are the defaults used when a season is created.
"""

# Auction phase order (S2 rulebook). Tiers map to phase_a_<tier> phases;
# "break" and "phase_b" are special phases in the flow.
DEFAULT_PHASE_ORDER = ["platinum", "gold", "break", "silver", "phase_b"]

# S2: no per-tier purse — teams live on the manager's own funding (universal
# 10k) plus admin grants. Kept as zeros for S1 ruleset-row compatibility.
DEFAULT_TIER_PURSES = {"platinum": 0, "gold": 0, "silver": 0}
DEFAULT_TIER_BASE_PRICES = {"platinum": 3000, "gold": 2000, "silver": 1000}
DEFAULT_TIER_CREDITS = {"platinum": 3, "gold": 2, "silver": 1}

TOTAL_CREDITS = 8
BID_INCREMENT = 50
PHASE_B_PRICE = 200
CREDIT_REFUND_RATE = 1000
REQUIRED_PLAYERS = 3  # bought players beyond the manager
ROSTER_SIZE = 4  # manager + 3
BREAK_MINUTES = 5

MATCH_REWARD_AMOUNT = 200  # fixed reward paid to each playing team per finalized match

# Phases
PHASE_SETUP = "setup"
PHASE_BREAK = "break"
PHASE_B = "phase_b"
PHASE_COMPLETE = "complete"
PHASE_TRANSFERS = "transfers_open"

# Roles
ROLE_ADMIN = "admin"
ROLE_PLAYER = "player"
ROLE_MANAGER = "manager"

TIERS = ("platinum", "gold", "silver")
SPECIALITIES = ("ALL_ROUNDER", "BATTER", "BOWLER")

CONTROL_MANAGER = "manager_controlled"
CONTROL_TAKEOVER = "admin_takeover"


def tier_phase(tier: str) -> str:
    return f"phase_a_{tier}"


def is_tier_phase(phase: str) -> bool:
    return phase.startswith("phase_a_") and phase[len("phase_a_"):] in TIERS


def phase_tier(phase: str) -> str:
    return phase[len("phase_a_"):]
