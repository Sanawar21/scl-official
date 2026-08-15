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
            row = conn.execute("SELECT id FROM users WHERE role = ?", (R.ROLE_ADMIN,)).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO users (id, username, password_hash, role, display_name, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (secrets.token_hex(8), username, generate_password_hash(password),
                     R.ROLE_ADMIN, "Administrator", _now()),
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
            return self.get_by_username(user["username"], conn=conn)

    def unlink_user(self, user_id: str):
        with self.db.write() as conn:
            conn.execute("UPDATE users SET global_player_id = NULL WHERE id = ?", (user_id,))

    def assign_manager(self, user_id: str, team_id: str) -> dict:
        """Promote a linked player account to manager for a team."""
        with self.db.write() as conn:
            user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            if not user:
                raise ValueError("User not found")
            if not user["global_player_id"]:
                raise ValueError("User must be linked to a player first")
            team = conn.execute("SELECT id, manager_player_id FROM teams WHERE id = ?", (team_id,)).fetchone()
            if not team:
                raise ValueError("Team not found")
            if team["manager_player_id"] != user["global_player_id"]:
                raise ValueError("User's player is not this team's manager")
            conn.execute(
                "UPDATE users SET role = ?, team_id = ? WHERE id = ?",
                (R.ROLE_MANAGER, team_id, user_id),
            )
            return self.get_by_username(user["username"], conn=conn)

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
        with self.db.read() as conn:
            if season_id:
                rows = conn.execute(
                    "SELECT u.*, t.name AS team_name FROM users u "
                    "JOIN teams t ON t.id = u.team_id "
                    "WHERE u.role = ? AND t.season_id = ? ORDER BY u.username",
                    (R.ROLE_MANAGER, season_id),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT u.*, t.name AS team_name FROM users u "
                    "LEFT JOIN teams t ON t.id = u.team_id "
                    "WHERE u.role = ? ORDER BY u.username",
                    (R.ROLE_MANAGER,),
                ).fetchall()
            return rows_to_dicts(rows)
