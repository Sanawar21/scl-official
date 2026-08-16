"""Auth: admin seed, self-signup, login, and admin linking of accounts to players."""
import secrets
from datetime import datetime, timezone

from werkzeug.security import check_password_hash, generate_password_hash

from .. import rules as R
from ..db import row_to_dict, rows_to_dicts


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AuthService:
    def __init__(self, db):
        self.db = db

    # --- admin ------------------------------------------------------------
    def seed_admin_if_missing(self, username: str = None, password: str = None):
        username = username or "admin"
        password = password or "admin123"
        with self.db.write() as conn:
            row = conn.execute("SELECT id, username, password_hash FROM users WHERE role = ?",
                               (R.ROLE_ADMIN,)).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO users (id, username, password_hash, role, display_name, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (secrets.token_hex(8), username, generate_password_hash(password),
                     R.ROLE_ADMIN, "Administrator", _now()),
                )
                return
            # The admin already exists: sync credentials to the configured ones
            # so .env is authoritative (a stale password from an earlier default
            # otherwise makes admin login silently fail).
            if (row["username"] != username
                    or not check_password_hash(row["password_hash"], password)):
                conn.execute(
                    "UPDATE users SET username = ?, password_hash = ? WHERE id = ?",
                    (username, generate_password_hash(password), row["id"]),
                )

    # --- signup / login ---------------------------------------------------
    def signup(self, username: str, password: str, display_name: str = "") -> dict:
        username = (username or "").strip()
        if not username or len(username) < 3:
            raise ValueError("Username must be at least 3 characters")
        if not password or len(password) < 4:
            raise ValueError("Password must be at least 4 characters")
        with self.db.write() as conn:
            exists = conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone()
            if exists:
                raise ValueError("Username already taken")
            user_id = secrets.token_hex(8)
            conn.execute(
                "INSERT INTO users (id, username, password_hash, role, display_name, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (user_id, username, generate_password_hash(password), R.ROLE_PLAYER,
                 (display_name or "").strip() or username, _now()),
            )
            return self.get_by_username(username, conn=conn)

    def login(self, username: str, password: str) -> dict:
        user = self.get_by_username((username or "").strip())
        if not user or not check_password_hash(user["password_hash"], password or ""):
            return None
        return user

    def reset_password(self, user_id: str, new_password: str) -> dict:
        """Admin-driven password reset (no self-service/email in the app).

        The admin sets a new password (or one is generated) and shares it with
        the player out-of-band. The admin account itself is excluded — its
        credentials come from configuration (.env)."""
        new_password = new_password or ""
        if len(new_password) < 4:
            raise ValueError("Password must be at least 4 characters")
        with self.db.write() as conn:
            user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            if not user:
                raise ValueError("User not found")
            if user["role"] == R.ROLE_ADMIN:
                raise ValueError("Admin credentials are managed via configuration (.env)")
            conn.execute(
                "UPDATE users SET password_hash = ? WHERE id = ?",
                (generate_password_hash(new_password), user_id),
            )
            return self.get_user(user_id)

    # --- linking / management ---------------------------------------------
    def link_user_to_player(self, user_id: str, global_player_id: str) -> dict:
        with self.db.write() as conn:
            user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            if not user:
                raise ValueError("User not found")
            gp = conn.execute("SELECT id FROM global_players WHERE id = ?", (global_player_id,)).fetchone()
            if not gp:
                raise ValueError("Player not found")
            conn.execute(
                "UPDATE users SET global_player_id = ? WHERE id = ?",
                (global_player_id, user_id),
            )
            # Linking activates the player: their wallet is managed MANUALLY.
            # New unlinked signups default to auto mode; the admin linking the
            # account is the signal that the player runs their own finances.
            acct = conn.execute(
                "SELECT auto_vault FROM bank_accounts WHERE owner_type = 'player' "
                "AND owner_id = ?", (global_player_id,),
            ).fetchone()
            if acct:
                conn.execute(
                    "UPDATE bank_accounts SET auto_vault = 0 "
                    "WHERE owner_type = 'player' AND owner_id = ?", (global_player_id,),
                )
            else:
                conn.execute(
                    "INSERT INTO bank_accounts (id, owner_type, owner_id, liquid_cash, "
                    "locked_capital, auto_vault, created_at) VALUES (?, 'player', ?, 0, 0, 0, ?)",
                    (secrets.token_hex(8), global_player_id, _now()),
                )
            return self.get_by_username(user["username"], conn=conn)

    def unlink_user(self, user_id: str):
        with self.db.write() as conn:
            conn.execute("UPDATE users SET global_player_id = NULL WHERE id = ?", (user_id,))

    def user_view(self, user: dict) -> dict:
        """Enrich a raw users row with DERIVED role + team state.

        Manager status is never stored on the user row — it is derived from the
        player→team links, which are the single source of truth:
          - persistent team: ``global_teams.manager_player_id = global_player_id``
          - per-season team: ``teams.manager_player_id = global_player_id``
        ``users.role`` only distinguishes admin from player; ``users.team_id``
        is legacy and ignored. Always re-fetches by id so a link made after
        login is picked up immediately."""
        if not user:
            return None
        fresh = self.get_user(user.get("id")) or dict(user)
        view = dict(fresh)
        view["is_manager"] = False
        view["team_id"] = None
        view["team_name"] = None
        view["season_id"] = None
        if view.get("role") == R.ROLE_ADMIN:
            return view
        gp_id = view.get("global_player_id")
        if gp_id:
            with self.db.read() as conn:
                # Latest season the player's team participates in.
                row = conn.execute(
                    "SELECT t.id AS team_id, t.name AS team_name, t.season_id "
                    "FROM teams t JOIN seasons s ON s.id = t.season_id "
                    "WHERE t.manager_player_id = ? "
                    "ORDER BY s.created_at DESC LIMIT 1",
                    (gp_id,),
                ).fetchone()
                if row:
                    view["is_manager"] = True
                    view["team_id"] = row["team_id"]
                    view["team_name"] = row["team_name"]
                    view["season_id"] = row["season_id"]
                else:
                    # Persistent manager even when the team isn't in a season.
                    gt = conn.execute(
                        "SELECT id, name FROM global_teams "
                        "WHERE manager_player_id = ? LIMIT 1", (gp_id,)).fetchone()
                    if gt:
                        view["is_manager"] = True
                        view["team_name"] = gt["name"]
        view["role"] = R.ROLE_MANAGER if view["is_manager"] else R.ROLE_PLAYER
        return view

    # --- queries ------------------------------------------------------------
    def get_by_username(self, username: str, conn=None) -> dict:
        def _fetch(c):
            row = c.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
            return row_to_dict(row)

        if conn is not None:
            return _fetch(conn)
        with self.db.read() as c:
            return _fetch(c)

    def get_user(self, user_id: str) -> dict:
        with self.db.read() as conn:
            return row_to_dict(conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone())

    def list_unlinked_users(self) -> list:
        with self.db.read() as conn:
            rows = conn.execute(
                "SELECT * FROM users WHERE role != ? AND global_player_id IS NULL ORDER BY created_at",
                (R.ROLE_ADMIN,),
            ).fetchall()
            return rows_to_dicts(rows)

    def list_managers(self, season_id: str = None) -> list:
        """Accounts whose player manages a team (derived, not stored)."""
        with self.db.read() as conn:
            if season_id:
                rows = conn.execute(
                    "SELECT u.*, t.name AS team_name FROM users u "
                    "JOIN teams t ON t.manager_player_id = u.global_player_id "
                    "WHERE u.role != ? AND t.season_id = ? ORDER BY u.username",
                    (R.ROLE_ADMIN, season_id),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT u.*, gt.name AS team_name FROM users u "
                    "JOIN global_teams gt ON gt.manager_player_id = u.global_player_id "
                    "WHERE u.role != ? ORDER BY u.username",
                    (R.ROLE_ADMIN,),
                ).fetchall()
            return rows_to_dicts(rows)
