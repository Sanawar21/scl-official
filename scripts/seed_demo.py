"""Demo / test environment for the SCL platform.

Builds a FRESH demo database (default `data/demo.db`, never `data/scl.db`) with
realistic data so you can click through every surface as each role:

- admin (auction control, scorer, finances, wagers, account links)
- managers (bid during the auction, propose trades, see wallet/squad)
- players (account: deposit + vault, wagers: propose + stake)

Usage:
    ./.venv/Scripts/python.exe scripts/seed_demo.py [path/to/demo.db]

Then run the app against that DB and log in with the credentials printed below:

    SCL_DB_PATH=data/demo.db ./.venv/Scripts/python.exe run.py
    # or (Windows bash):
    SCL_DB_PATH=data/demo.db ./.venv/Scripts/python.exe -c "from app import create_app, socketio; app = create_app(); socketio.run(app, host='0.0.0.0', port=10001, debug=False, use_reloader=False)"

Re-run anytime to reset (the demo DB is deleted and rebuilt).
"""
import csv
import io
import os
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("SCL_CONFIG", str(ROOT / "config"))

DEMO_PASSWORD = "demo123"


def _csv_upload(rows, header, name="demo-match.csv"):
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=header)
    writer.writeheader()
    for r in rows:
        writer.writerow({c: r.get(c, "") for c in header})
    bio = io.BytesIO(buf.getvalue().encode("utf-8"))
    bio.filename = name
    return bio


def seed_demo(db_path: str) -> None:
    from app import create_app

    app = create_app({"SECRET_KEY": "demo", "DB_PATH": db_path,
                      "ADMIN_USERNAME": "admin", "ADMIN_PASSWORD": DEMO_PASSWORD})
    auction = app.extensions["auction_service"]
    auth = app.extensions["auth_service"]
    bank = app.extensions["bank_service"]
    wager = app.extensions["wager_service"]
    scorer = app.extensions["scorer_service"]

    # ------------------------------------------------------------------
    # 1. Season + players + teams (2 managers + 2 unmanaged teams)
    # ------------------------------------------------------------------
    season = auction.create_season("Demo Season")
    sid = season["id"]

    specs = [
        ("Ayaan", "platinum", "BATTER"), ("Bilal", "platinum", "ALL_ROUNDER"),
        ("Cyrus", "gold", "BATTER"), ("Dania", "gold", "ALL_ROUNDER"),
        ("Eshan", "gold", "BOWLER"), ("Farah", "silver", "BATTER"),
        ("Gul", "silver", "ALL_ROUNDER"), ("Hassan", "silver", "BOWLER"),
        ("Imran", "silver", "BATTER"), ("Junaid", "gold", "BOWLER"),
        ("Kiran", "platinum", "BOWLER"), ("Laila", "silver", "ALL_ROUNDER"),
    ]
    players = [auction.add_player(sid, *s) for s in specs]

    # teams[0]/[1] get manager accounts (Ayaan, Bilal); [2]/[3] stay unmanaged
    teams = [
        auction.create_team(sid, "Lions", players[0]["global_player_id"]),
        auction.create_team(sid, "Tigers", players[1]["global_player_id"]),
        auction.create_team(sid, "Eagles", players[2]["global_player_id"]),
        auction.create_team(sid, "Falcons", players[3]["global_player_id"]),
    ]

    # Manager + player users. S2 economy: no tier purses — every player is
    # funded with the universal 10k (managers' wallets are their teams' money).
    users = {}
    for uname, idx, display in [("ayaan", 0, "Ayaan"), ("bilal", 1, "Bilal"),
                                ("cyrus", 2, "Cyrus"), ("dania", 3, "Dania"),
                                ("farah", 5, "Farah"), ("gul", 6, "Gul")]:
        u = auth.signup(uname, DEMO_PASSWORD, display)
        u = auth.link_user_to_player(u["id"], players[idx]["global_player_id"])
        users[uname] = u
    # S2 funding: everyone gets 10k. The users who need to bid / stake / lock
    # manually get their wallets pre-created as MANUAL first, so their 10k lands
    # liquid; everyone else's wallet is auto-created on auto mode (10k vaulted).
    for uname in ("ayaan", "bilal", "cyrus", "farah", "gul"):
        acc = bank.get_or_create_account("player", users[uname]["global_player_id"])
        bank.set_auto(acc["id"], False)
    bank.fund_all_players(10000)
    for uname, team in [("ayaan", teams[0]), ("bilal", teams[1])]:
        auth.assign_manager(users[uname]["id"], team["id"])

    # Manager players are roster slots, not auction lots (like S1): mark them
    # sold to their own team so nominate_next skips them.
    with app.extensions["db"].write() as conn:
        for t in teams:
            conn.execute(
                "UPDATE players SET status = 'sold', sold_to_team_id = ? "
                "WHERE global_player_id = ?",
                (t["id"], t["manager_player_id"]))

    # ------------------------------------------------------------------
    # 2. Partial auction — a few lots sold (platinum + gold), one live lot
    # ------------------------------------------------------------------
    auction.set_phase(sid, "phase_a_platinum", actor="admin")
    # Lot 1: Kiran (platinum) → Tigers
    auction.nominate_next(sid, actor="admin")
    auction.place_bid(sid, teams[0]["id"], 3000, actor="manager")
    auction.place_bid(sid, teams[1]["id"], 3100, actor="manager")
    auction.close_current(sid, actor="admin")
    # Move to gold; sell two gold lots (Eshan → Lions, Junaid → Tigers)
    auction.set_phase(sid, "phase_a_gold", actor="admin")
    auction.nominate_next(sid, actor="admin")
    auction.place_bid(sid, teams[0]["id"], 2100, actor="manager")
    auction.place_bid(sid, teams[2]["id"], 2200, actor="manager")
    auction.close_current(sid, actor="admin")
    auction.nominate_next(sid, actor="admin")
    auction.place_bid(sid, teams[1]["id"], 2300, actor="manager")
    auction.close_current(sid, actor="admin")
    # Move to silver; leave the current lot OPEN so the admin can test
    # close/nominate (phase stays phase_a_silver; one live lot + a live bid)
    auction.set_phase(sid, "phase_a_silver", actor="admin")
    auction.nominate_next(sid, actor="admin")
    auction.place_bid(sid, teams[0]["id"], 1000, actor="manager")

    # ------------------------------------------------------------------
    # 3. Wagers across the lifecycle (proposed / calibrating / vetted / frozen)
    # ------------------------------------------------------------------
    w1 = wager.create_wager(users["farah"], "Will Lions top the table?",
                            "Demo market 1", "Yes", "No", "Yes", 200, season_id=sid)
    w2 = wager.create_wager(users["gul"], "Most sixes in Match 1?",
                            "Demo market 2", "Team A", "Team B", "Team A", 300, season_id=sid)
    w3 = wager.create_wager(users["ayaan"], "Will there be a century?",
                            "Demo market 3", "Yes", "No", "No", 250, season_id=sid)
    # push one to vetted, one to calibrating, freeze one
    wager.calibrate(w2["id"], "admin", 55.0)
    wager.finalize_calibration(w2["id"], "admin")
    wager.freeze(w2["id"], "admin")
    wager.calibrate(w3["id"], "admin", 40.0)  # stays calibrating (no finalize)

    # ------------------------------------------------------------------
    # 4. Vault position for a manager + a player
    # ------------------------------------------------------------------
    for uname in ("ayaan", "farah"):
        acc = bank.get_or_create_account("player", users[uname]["global_player_id"])
        if uname == "ayaan":
            bank.lock_to_vault(acc["id"], sid, 1000, reinvest=True)
        else:
            bank.lock_to_vault(acc["id"], sid, 500, reinvest=False)

    # ------------------------------------------------------------------
    # 5. One finalized match through the real scorer CSV path
    # ------------------------------------------------------------------
    gids = {}
    with app.extensions["db"].write() as conn:
        for t in teams:
            gid = secrets.token_hex(8)
            gids[t["id"]] = gid
            conn.execute("UPDATE teams SET global_team_id = ? WHERE id = ?", (gid, t["id"]))
    scorer.upsert_match_registry_entry(
        sid, "M1", match_number="Match 1", between="Lions vs Tigers",
        venue="Demo Ground", match_date="2026-08-10",
        team_a_global_id=gids[teams[0]["id"]], team_b_global_id=gids[teams[1]["id"]])

    p = {pl["name"]: pl["id"] for pl in players}
    cols = list(scorer.CSV_REQUIRED_COLUMNS)

    def dr(inn, bat_team, bat, bowler, runs, dism="", pr=0, pw=0):
        t = teams[0] if bat_team == "Lions" else teams[1]
        ot = teams[1] if bat_team == "Lions" else teams[0]
        return {"Match ID": "M1", "Innings Order": inn, "Batting Team": bat_team,
                "Batting Team ID": gids[t["id"]],
                "Batting Manager ID": t["manager_player_id"],
                "Over Number": "0", "Ball Number": "1", "Valid Ball?": "Yes",
                "Batter": bat, "Batter ID": p.get(bat, ""),
                "Non Strike Batter": "", "Non Strike Batter ID": "",
                "Bowler": bowler, "Bowler ID": p.get(bowler, ""),
                "Bowling Team": ot["name"], "Bowling Team ID": gids[ot["id"]],
                "Bowling Manager ID": ot["manager_player_id"],
                "Runs Bat": runs, "Runs Extra": "0", "Extras Type": "",
                "Dismissed Batter": dism or "None", "Dismissed Batter ID": p.get(dism, ""),
                "Progressive Runs": pr, "Progressive Wickets": pw,
                "Match Toss": "Lions won the toss", "Match Result": "Lions won"}

    rows = [
        dr(1, "Lions", "Ayaan", "Cyrus", 2, pr=2, pw=0),
        dr(1, "Lions", "Kiran", "Cyrus", 4, pr=6, pw=0),
        dr(1, "Lions", "Dania", "Cyrus", 6, pr=12, pw=0),
        dr(1, "Lions", "Gul", "Cyrus", 0, dism="Gul", pr=12, pw=1),
        dr(1, "Lions", "Farah", "Cyrus", 4, pr=16, pw=1),
        dr(2, "Tigers", "Bilal", "Ayaan", 1, pr=1, pw=0),
        dr(2, "Tigers", "Eshan", "Ayaan", 2, pr=3, pw=0),
        dr(2, "Tigers", "Junaid", "Ayaan", 6, pr=9, pw=0),
        dr(2, "Tigers", "Hassan", "Ayaan", 0, dism="Hassan", pr=9, pw=1),
        dr(2, "Tigers", "Imran", "Ayaan", 4, pr=13, pw=1),
    ]
    scorer.import_match_csv(_csv_upload(rows, cols), sid, "M1",
                            venue_override="Demo Ground", match_date="2026-08-10",
                            uploaded_by="demo")
    app.extensions["finance_service"].on_match_finalized(sid, "M1", actor="demo")

    # ------------------------------------------------------------------
    # 6. Publish a snapshot (public /season/<slug> page)
    # ------------------------------------------------------------------
    auction.publish(sid, "Demo Season", actor="demo")

    # ------------------------------------------------------------------
    # 7. Print the tour
    # ------------------------------------------------------------------
    print("=" * 72)
    print("Demo environment ready.")
    print(f"  DB        : {db_path}")
    print("  Server    : SCL_DB_PATH=<that path> ./.venv/Scripts/python.exe run.py")
    print("  Admin     : admin / demo123")
    print("  Managers  : ayaan / demo123, bilal / demo123   (linked to Lions/Tigers)")
    print("  Players   : cyrus, dania, farah, gul / demo123")
    print()
    print("  Try as admin   : /admin  -> auction control (close the open lot,")
    print("                   nominate next, set phase, scorer admin, finances,")
    print("                   wager admin, link accounts)")
    print("  Try as manager : /manager (bid/pass on the live lot, propose trades)")
    print("  Try as player  : /account (deposit, vault lock/reinvest), /wagers")
    print("                   (propose a market, stake on vetted ones)")
    print("  Public         : / (home), /live, /matches, /table, /leaderboards,")
    print("                   /teams, /players, /finances, /wagers")
    print("=" * 72)


if __name__ == "__main__":
    db_path = (sys.argv[1] if len(sys.argv) > 1
               else str(ROOT / "data" / "demo.db"))
    p = Path(db_path)
    if p.exists():
        p.unlink()
    for suffix in ("-wal", "-shm"):
        q = Path(str(p) + suffix)
        if q.exists():
            q.unlink()
    seed_demo(str(p))
