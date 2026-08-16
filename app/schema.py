"""SQLite schema for the SCL platform (auction + banking domain first)."""

SQL = """
CREATE TABLE IF NOT EXISTS global_players (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  tier TEXT NOT NULL DEFAULT 'silver',
  speciality TEXT NOT NULL DEFAULT 'ALL_ROUNDER',
  created_at TEXT NOT NULL
);

-- Persistent team identity (spans seasons). Per-season participation rows in
-- `teams` link here via teams.global_team_id. The manager's player wallet IS
-- the team's money; a team not in any season still exists here (logo/about).
CREATE TABLE IF NOT EXISTS global_teams (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  logo TEXT,
  banner TEXT,
  about TEXT,
  manager_player_id TEXT REFERENCES global_players(id),
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY,
  username TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'player',
  display_name TEXT,
  global_player_id TEXT REFERENCES global_players(id),
  team_id TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS seasons (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'setup',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rulesets (
  id TEXT PRIMARY KEY,
  season_id TEXT NOT NULL UNIQUE REFERENCES seasons(id),
  phase_order TEXT NOT NULL,
  tier_purses TEXT NOT NULL,
  tier_base_prices TEXT NOT NULL,
  tier_credits TEXT NOT NULL,
  total_credits INTEGER NOT NULL DEFAULT 8,
  bid_increment INTEGER NOT NULL DEFAULT 50,
  phase_b_price INTEGER NOT NULL DEFAULT 200,
  credit_refund_rate INTEGER NOT NULL DEFAULT 1000,
  required_players INTEGER NOT NULL DEFAULT 3,
  roster_size INTEGER NOT NULL DEFAULT 4,
  break_minutes INTEGER NOT NULL DEFAULT 5,
  match_reward_amount INTEGER NOT NULL DEFAULT 250
);

CREATE TABLE IF NOT EXISTS players (
  id TEXT PRIMARY KEY,
  season_id TEXT NOT NULL REFERENCES seasons(id),
  global_player_id TEXT REFERENCES global_players(id),
  name TEXT NOT NULL,
  tier TEXT NOT NULL,
  speciality TEXT NOT NULL DEFAULT 'ALL_ROUNDER',
  base_price INTEGER NOT NULL DEFAULT 0,
  credits INTEGER NOT NULL DEFAULT 1,
  status TEXT NOT NULL DEFAULT 'unsold',
  sold_to_team_id TEXT,
  sold_price INTEGER NOT NULL DEFAULT 0,
  phase_sold TEXT,
  current_bid INTEGER NOT NULL DEFAULT 0,
  current_bidder_team_id TEXT,
  nominated_phase_a INTEGER NOT NULL DEFAULT 0,
  nomination_order INTEGER
);

CREATE TABLE IF NOT EXISTS teams (
  id TEXT PRIMARY KEY,
  season_id TEXT NOT NULL REFERENCES seasons(id),
  global_team_id TEXT,
  name TEXT NOT NULL,
  manager_player_id TEXT,
  manager_tier TEXT NOT NULL DEFAULT 'silver',
  spent INTEGER NOT NULL DEFAULT 0,
  credits_remaining INTEGER NOT NULL DEFAULT 0,
  players TEXT NOT NULL DEFAULT '[]',
  bench TEXT NOT NULL DEFAULT '[]',
  is_active INTEGER NOT NULL DEFAULT 1,
  control_status TEXT NOT NULL DEFAULT 'manager_controlled',
  takeover_reason TEXT,
  takeover_by TEXT,
  takeover_at TEXT
);

CREATE TABLE IF NOT EXISTS bids (
  id TEXT PRIMARY KEY,
  season_id TEXT NOT NULL,
  ts TEXT NOT NULL,
  team_id TEXT NOT NULL,
  player_id TEXT NOT NULL,
  amount INTEGER NOT NULL,
  phase TEXT,
  kind TEXT NOT NULL DEFAULT 'bid'
);

CREATE TABLE IF NOT EXISTS trade_requests (
  id TEXT PRIMARY KEY,
  season_id TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  created_at TEXT NOT NULL,
  from_team_id TEXT NOT NULL,
  to_team_id TEXT NOT NULL,
  offered_player_id TEXT NOT NULL,
  requested_player_id TEXT,
  cash_from_initiator INTEGER NOT NULL DEFAULT 0,
  cash_from_target INTEGER NOT NULL DEFAULT 0,
  responded_at TEXT,
  responded_by_team_id TEXT
);

CREATE TABLE IF NOT EXISTS transfers (
  id TEXT PRIMARY KEY,
  season_id TEXT NOT NULL,
  team_from TEXT,
  team_to TEXT NOT NULL,
  player_id TEXT NOT NULL,
  price INTEGER NOT NULL DEFAULT 0,
  credits INTEGER NOT NULL DEFAULT 0,
  created_by TEXT,
  created_at TEXT NOT NULL,
  note TEXT
);

CREATE TABLE IF NOT EXISTS auction_action_log (
  id TEXT PRIMARY KEY,
  season_id TEXT NOT NULL,
  action_type TEXT NOT NULL,
  actor TEXT,
  ref_player_id TEXT,
  ref_team_id TEXT,
  before_state TEXT,
  after_state TEXT,
  created_at TEXT NOT NULL,
  undone_at TEXT,
  undo_of TEXT
);

CREATE TABLE IF NOT EXISTS auction_meta (
  season_id TEXT PRIMARY KEY,
  phase TEXT NOT NULL DEFAULT 'setup',
  current_player_id TEXT,
  nomination_history TEXT NOT NULL DEFAULT '[]',
  break_started_at TEXT
);

CREATE TABLE IF NOT EXISTS season_snapshots (
  id TEXT PRIMARY KEY,
  season_id TEXT NOT NULL,
  name TEXT NOT NULL,
  published_at TEXT NOT NULL,
  payload TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bank_accounts (
  id TEXT PRIMARY KEY,
  owner_type TEXT NOT NULL,
  owner_id TEXT NOT NULL,
  liquid_cash INTEGER NOT NULL DEFAULT 0,
  locked_capital INTEGER NOT NULL DEFAULT 0,
  auto_vault INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS vault_positions (
  id TEXT PRIMARY KEY,
  account_id TEXT NOT NULL REFERENCES bank_accounts(id),
  season_id TEXT NOT NULL,
  principal INTEGER NOT NULL DEFAULT 0,
  locked_capital INTEGER NOT NULL DEFAULT 0,
  reinvest INTEGER NOT NULL DEFAULT 1,
  last_yield_match INTEGER NOT NULL DEFAULT 0,
  unlocked INTEGER NOT NULL DEFAULT 0,
  unlocked_at TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bank_transactions (
  id TEXT PRIMARY KEY,
  account_id TEXT NOT NULL,
  type TEXT NOT NULL,
  amount INTEGER NOT NULL,
  balance_after INTEGER,
  comment TEXT,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_players_season ON players(season_id);
CREATE INDEX IF NOT EXISTS idx_players_status ON players(season_id, status);
CREATE INDEX IF NOT EXISTS idx_teams_season ON teams(season_id);
CREATE INDEX IF NOT EXISTS idx_bids_season_player ON bids(season_id, player_id);
CREATE INDEX IF NOT EXISTS idx_bids_season ON bids(season_id);
CREATE INDEX IF NOT EXISTS idx_transfers_season ON transfers(season_id);
CREATE INDEX IF NOT EXISTS idx_actionlog_season ON auction_action_log(season_id);
CREATE TABLE IF NOT EXISTS wagers (
  id TEXT PRIMARY KEY,
  season_id TEXT REFERENCES seasons(id),
  title TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  side_a TEXT NOT NULL DEFAULT 'Yes',
  side_b TEXT NOT NULL DEFAULT 'No',
  status TEXT NOT NULL DEFAULT 'proposed',
  accepting_bets INTEGER NOT NULL DEFAULT 0,
  initiator_user_id TEXT,
  initiator_name TEXT NOT NULL,
  house_probability REAL,
  calibration_estimates TEXT NOT NULL DEFAULT '[]',
  house_injected INTEGER NOT NULL DEFAULT 0,
  winning_side TEXT,
  veto_reason TEXT,
  void_reason TEXT,
  history TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  resolved_at TEXT
);

CREATE TABLE IF NOT EXISTS wager_bets (
  id TEXT PRIMARY KEY,
  wager_id TEXT NOT NULL REFERENCES wagers(id),
  user_id TEXT NOT NULL,
  username TEXT NOT NULL,
  side TEXT NOT NULL,
  amount INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'open',
  payout INTEGER,
  stake_tx_id TEXT,
  created_at TEXT NOT NULL,
  settled_at TEXT
);

CREATE TABLE IF NOT EXISTS match_registry (
  match_key TEXT PRIMARY KEY,
  season_id TEXT NOT NULL REFERENCES seasons(id),
  match_id TEXT NOT NULL,
  match_number TEXT,
  match_title TEXT,
  "between" TEXT,
  venue TEXT,
  match_date TEXT,
  team_a_global_id TEXT,
  team_b_global_id TEXT,
  walkover INTEGER NOT NULL DEFAULT 0,
  walkover_winner_team_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (season_id, match_id)
);

CREATE TABLE IF NOT EXISTS match_stats (
  match_key TEXT PRIMARY KEY REFERENCES match_registry(match_key),
  season_id TEXT NOT NULL,
  match_id TEXT NOT NULL,
  result TEXT,
  toss TEXT,
  winner_team_id TEXT,
  delivery_rows INTEGER DEFAULT 0,
  team_rows INTEGER DEFAULT 0,
  player_rows INTEGER DEFAULT 0,
  source_file TEXT,
  uploaded_by TEXT,
  uploaded_at TEXT,
  include_in_fantasy_points INTEGER NOT NULL DEFAULT 1,
  delivery_log TEXT
);

CREATE TABLE IF NOT EXISTS match_team_stats (
  id TEXT PRIMARY KEY,
  match_key TEXT NOT NULL REFERENCES match_registry(match_key),
  season_id TEXT NOT NULL,
  team_id TEXT NOT NULL,
  team_name TEXT NOT NULL,
  runs_scored INTEGER DEFAULT 0,
  balls_faced INTEGER DEFAULT 0,
  wickets_lost INTEGER DEFAULT 0,
  fours INTEGER DEFAULT 0,
  sixes INTEGER DEFAULT 0,
  wides_faced INTEGER DEFAULT 0,
  noballs_faced INTEGER DEFAULT 0,
  runs_conceded INTEGER DEFAULT 0,
  balls_bowled INTEGER DEFAULT 0,
  wickets_taken INTEGER DEFAULT 0,
  wides_bowled INTEGER DEFAULT 0,
  noballs_bowled INTEGER DEFAULT 0,
  overs_faced TEXT,
  overs_bowled TEXT,
  run_rate_for REAL,
  run_rate_against REAL,
  result TEXT,
  wins INTEGER DEFAULT 0,
  losses INTEGER DEFAULT 0,
  ties INTEGER DEFAULT 0,
  no_results INTEGER DEFAULT 0,
  fantasy_points INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS match_player_stats (
  id TEXT PRIMARY KEY,
  match_key TEXT NOT NULL REFERENCES match_registry(match_key),
  season_id TEXT NOT NULL,
  player_id TEXT NOT NULL,
  player_name TEXT NOT NULL,
  team_id TEXT NOT NULL,
  team_name TEXT NOT NULL,
  role TEXT,
  tier TEXT,
  matches INTEGER DEFAULT 1,
  innings_batted INTEGER DEFAULT 0,
  not_out INTEGER DEFAULT 0,
  dismissed INTEGER DEFAULT 0,
  runs INTEGER DEFAULT 0,
  balls_faced INTEGER DEFAULT 0,
  fours INTEGER DEFAULT 0,
  sixes INTEGER DEFAULT 0,
  innings_bowled INTEGER DEFAULT 0,
  balls_bowled INTEGER DEFAULT 0,
  runs_conceded INTEGER DEFAULT 0,
  wickets INTEGER DEFAULT 0,
  wides INTEGER DEFAULT 0,
  noballs INTEGER DEFAULT 0,
  strike_rate REAL,
  economy REAL,
  fantasy_score INTEGER DEFAULT 0,
  fantasy_bat_points REAL,
  fantasy_bowl_points REAL,
  batter_order INTEGER
);

CREATE INDEX IF NOT EXISTS idx_match_registry_season ON match_registry(season_id);
CREATE INDEX IF NOT EXISTS idx_match_team_season ON match_team_stats(season_id);
CREATE INDEX IF NOT EXISTS idx_match_team_team ON match_team_stats(season_id, team_id);
CREATE INDEX IF NOT EXISTS idx_match_player_season ON match_player_stats(season_id);
CREATE INDEX IF NOT EXISTS idx_match_player_player ON match_player_stats(season_id, player_id);
CREATE INDEX IF NOT EXISTS idx_wagers_status ON wagers(status);
CREATE INDEX IF NOT EXISTS idx_wager_bets_wager ON wager_bets(wager_id);
CREATE INDEX IF NOT EXISTS idx_wager_bets_user ON wager_bets(user_id);
CREATE INDEX IF NOT EXISTS idx_bank_owner ON bank_accounts(owner_type, owner_id);
CREATE INDEX IF NOT EXISTS idx_banktx_account ON bank_transactions(account_id);

CREATE TABLE IF NOT EXISTS season_finance_entries (
  id TEXT PRIMARY KEY,
  season_id TEXT NOT NULL REFERENCES seasons(id),
  match_id TEXT,
  team_id TEXT,
  team_name TEXT,
  type TEXT NOT NULL,
  operation TEXT,
  amount INTEGER NOT NULL DEFAULT 0,
  comment TEXT,
  created_by TEXT,
  from_team_id TEXT,
  to_team_id TEXT,
  before_wallet INTEGER,
  after_wallet INTEGER,
  undone_at TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_finance_season ON season_finance_entries(season_id);

CREATE TABLE IF NOT EXISTS changelog (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  body TEXT NOT NULL,
  change_date TEXT NOT NULL,
  author TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_changelog_date ON changelog(change_date);
"""
