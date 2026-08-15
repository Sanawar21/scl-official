"""Thread-safe SQLite access helpers.

Mirrors the reference app's LockedTinyDB pattern: a global RLock around
connection-per-operation, WAL journal mode, and JSON helpers for flexible fields.
"""
import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

from . import schema  # noqa: F401  (ensures schema.sql is packaged)


def json_dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


def json_loads(text, default=None):
    if text is None or text == "":
        return default
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return default


def row_to_dict(row) -> dict:
    if row is None:
        return None
    return {key: row[key] for key in row.keys()}


def rows_to_dicts(rows) -> list:
    return [row_to_dict(r) for r in rows]


class Database:
    def __init__(self, path: str):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @contextmanager
    def read(self):
        with self._lock:
            conn = self._connect()
            try:
                yield conn
            finally:
                conn.close()

    @contextmanager
    def write(self):
        with self._lock:
            conn = self._connect()
            # Take the write lock up-front so concurrent writers serialize cleanly.
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    # Lightweight migrations for existing DBs (schema.py uses CREATE IF NOT EXISTS,
    # so new columns on existing tables must be added here).
    _MIGRATIONS = (
        # (table, column, ddl)
        ("teams", "global_team_id", "ALTER TABLE teams ADD COLUMN global_team_id TEXT"),
        ("vault_positions", "unlocked", "ALTER TABLE vault_positions ADD COLUMN unlocked INTEGER NOT NULL DEFAULT 0"),
        ("vault_positions", "unlocked_at", "ALTER TABLE vault_positions ADD COLUMN unlocked_at TEXT"),
        ("rulesets", "match_reward_amount", "ALTER TABLE rulesets ADD COLUMN match_reward_amount INTEGER NOT NULL DEFAULT 200"),
    )
    # Column drops for existing DBs (schema.py already omits them).
    _DROP_MIGRATIONS = (
        ("teams", "purse_remaining"),
    )

    def bootstrap(self):
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("PRAGMA journal_mode = WAL")
                conn.executescript(schema.SQL)
                for table, column, ddl in self._MIGRATIONS:
                    cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
                    if column not in cols:
                        conn.execute(ddl)
                for table, column in self._DROP_MIGRATIONS:
                    cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
                    if column in cols:
                        conn.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
                conn.commit()
            finally:
                conn.close()


def get_db(app) -> Database:
    db = app.extensions.get("db")
    if db is None:
        db = Database(app.config["DB_PATH"])
        db.bootstrap()
        app.extensions["db"] = db
    return db
