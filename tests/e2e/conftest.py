"""E2E fixtures (Phase 0): a real HTTP server on a temp DB + login helpers.

The `server` fixture boots the actual Flask-SocketIO app (Werkzeug) on a random
local port against a fresh temp DB and seeds one season with players, teams,
users (admin / player / manager), and a wager. All e2e tests drive this server
through Playwright's Chromium. The real `data/scl.db` is never touched.
"""
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

    wager = wager.create_wager(users["alice"], "Will Thunder win Match 1?",
                               "Seeded e2e market", "Yes", "No", "Yes", 500,
                               season_id=sid)
    return {"season": season, "players": players, "teams": teams,
            "users": users, "wager": wager}


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
