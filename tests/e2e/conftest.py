"""E2E fixtures (Phase 0): a real HTTP server on a temp DB + login helpers.

The `server` fixture boots the actual Flask-SocketIO app (Werkzeug) on a random
local port against a fresh temp DB and seeds one season with players, teams,
users (admin / player / manager), a wager, and a finalized match. All e2e tests
drive this server through Playwright's Chromium. The real `data/scl.db` is never
touched.
"""
import csv
import io
import secrets
import socket
import threading
import time
import urllib.request

import pytest


def _seed(app):
    """Seed a season + users + a wager for e2e (mirrors tests/conftest._setup)."""
    auction = app.extensions["auction_service"]
    auth = app.extensions["auth_service"]
    bank = app.extensions["bank_service"]
    wager = app.extensions["wager_service"]

    season = auction.create_season("Test Season")
    sid = season["id"]

    specs = [
        ("Alice", "platinum", "BATTER"),
        ("Bob", "gold", "ALL_ROUNDER"),
        ("Cara", "silver", "BOWLER"),
        ("Dave", "platinum", "ALL_ROUNDER"),
    ]
    players = [auction.add_player(sid, *s) for s in specs]
    teams = [
        auction.create_team(sid, "Thunder", players[3]["global_player_id"]),  # Dave
        auction.create_team(sid, "Blaze", players[1]["global_player_id"]),      # Bob
    ]

    users = {}
    for uname, idx, pw in [("alice", 0, "alicepw"), ("cara", 2, "carapw")]:
        u = auth.signup(uname, pw, uname.title())
        u = auth.link_user_to_player(u["id"], players[idx]["global_player_id"])
        acc = bank.get_or_create_account("player", u["global_player_id"])
        bank.adjust(acc["id"], 5000, "e2e seed funding", tx_type="deposit")
        users[uname] = u
    dave = auth.signup("dave", "davepw", "Dave")
    dave = auth.link_user_to_player(dave["id"], players[3]["global_player_id"])
    auth.assign_manager(dave["id"], teams[0]["id"])
    users["dave"] = dave

    market = wager.create_wager(users["alice"], "Will Thunder win Match 1?",
                                "Seeded e2e market", "Yes", "No", "Yes", 500,
                                season_id=sid)
    # push the market to vetted so the stake flow + fair odds are usable in tests
    wager.calibrate(market["id"], "admin", 60.0)         # house p(No) = 60%
    wager.finalize_calibration(market["id"], "admin")
    market = wager.get_wager(market["id"])

    _seed_match(app, season, players, teams)
    auction.publish(sid, "Test Season")  # published snapshot page
    return {"season": season, "players": players, "teams": teams,
            "users": users, "wager": market}


def _seed_match(app, season, players, teams):
    """Import one finalized match (M1) through the real scorer CSV path."""
    db = app.extensions["db"]
    scorer = app.extensions["scorer_service"]
    sid = season["id"]

    # create_team leaves global_team_id NULL (the prod import sets it) — assign
    # one so the scorer's identity maps (local->global) resolve.
    gids = {}
    with db.write() as conn:
        for t in teams:
            gid = secrets.token_hex(8)
            gids[t["id"]] = gid
            conn.execute("UPDATE teams SET global_team_id = ? WHERE id = ?", (gid, t["id"]))

    scorer.upsert_match_registry_entry(
        sid, "M1", match_number="Match 1", between="Thunder vs Blaze",
        venue="Ground 1", match_date="2026-08-01",
        team_a_global_id=gids[teams[0]["id"]], team_b_global_id=gids[teams[1]["id"]])

    p = {pl["name"]: pl["id"] for pl in players}
    t0, t1 = teams  # Thunder, Blaze
    cols = list(scorer.CSV_REQUIRED_COLUMNS) + ["Batter Order"]

    def row(**kw):
        r = {c: "" for c in cols}
        r.update(kw)
        return r

    def dr(inn, over, ball, bat_team, bat, bowler, bowl_team, runs,
           dism="", pr=0, pw=0, order=1):
        return row(**{"Match ID": "M1", "Innings Order": inn,
                      "Batting Team": bat_team,
                      "Batting Team ID": t0["id"] if bat_team == "Thunder" else t1["id"],
                      "Batting Manager ID": t0["manager_player_id"] if bat_team == "Thunder" else t1["manager_player_id"],
                      "Over Number": over, "Ball Number": ball, "Valid Ball?": "Yes",
                      "Batter": bat, "Batter ID": p[bat],
                      "Non Strike Batter": "", "Non Strike Batter ID": "",
                      "Bowler": bowler, "Bowler ID": p[bowler],
                      "Bowling Team": bowl_team,
                      "Bowling Team ID": t1["id"] if bowl_team == "Blaze" else t0["id"],
                      "Bowling Manager ID": t1["manager_player_id"] if bowl_team == "Blaze" else t0["manager_player_id"],
                      "Runs Bat": runs, "Runs Extra": 0, "Extras Type": "",
                      "Dismissed Batter": dism, "Dismissed Batter ID": p.get(dism, ""),
                      "Progressive Runs": pr, "Progressive Wickets": pw,
                      "Match Toss": "Thunder won the toss",
                      "Match Result": "Thunder won by 3 runs",
                      "Batter Order": order})

    rows = [
        dr(1, 1, 1, "Thunder", "Alice", "Bob", "Blaze", 1, pr=1, pw=0, order=1),
        dr(1, 1, 2, "Thunder", "Alice", "Bob", "Blaze", 0, dism="Alice", pr=1, pw=1, order=1),
        dr(1, 1, 3, "Thunder", "Dave", "Bob", "Blaze", 2, pr=3, pw=1, order=2),
        dr(2, 1, 1, "Blaze", "Bob", "Dave", "Thunder", 4, pr=4, pw=0, order=1),
        dr(2, 1, 2, "Blaze", "Cara", "Dave", "Thunder", 2, pr=6, pw=0, order=2),
    ]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=cols)
    writer.writeheader()
    for r in rows:
        writer.writerow(r)
    bio = io.BytesIO(buf.getvalue().encode("utf-8"))
    bio.filename = "m1.csv"
    scorer.import_match_csv(bio, sid, "M1", venue_override="Ground 1",
                            match_date="2026-08-01", uploaded_by="e2e")


@pytest.fixture(scope="session")
def server(tmp_path_factory):
    from app import create_app, socketio

    db_path = str(tmp_path_factory.mktemp("e2e") / "e2e.db")
    app = create_app({"SECRET_KEY": "e2e", "DB_PATH": db_path,
                      "ADMIN_USERNAME": "admin", "ADMIN_PASSWORD": "admin123"})
    seed = _seed(app)

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    thread = threading.Thread(
        target=socketio.run,
        kwargs={"app": app, "host": "127.0.0.1", "port": port,
                "use_reloader": False, "debug": False,
                "allow_unsafe_werkzeug": True},
        daemon=True,
    )
    thread.start()

    base_url = f"http://127.0.0.1:{port}"
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(base_url + "/", timeout=2) as resp:
                if resp.status == 200:
                    break
        except Exception:
            time.sleep(0.2)
    else:
        raise RuntimeError("E2E server failed to start")

    yield base_url, seed
    # daemon thread dies with the interpreter; nothing else to tear down


@pytest.fixture(scope="session")
def base_url(server):
    return server[0]


@pytest.fixture(scope="session")
def seed(server):
    return server[1]


@pytest.fixture()
def login(page, base_url):
    """Log a user in through the real login form. Yields after redirect settles."""
    def _login(username: str, password: str):
        page.goto(base_url + "/auth/login")
        page.fill('input[name="username"]', username)
        page.fill('input[name="password"]', password)
        page.click('button[type="submit"]')
        page.wait_for_load_state("networkidle")
    return _login
