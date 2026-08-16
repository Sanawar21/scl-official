"""Change log — the audit-trail of rule/platform changes shown on the site.

Entries are written by admins (title + body + effective date) and displayed
newest-first on the public /changelog page. Bodies are markdown, rendered with
the same doc_service renderer used for the participant documents.
"""

import secrets
from datetime import datetime, timezone

from ..db import row_to_dict, rows_to_dicts


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ChangelogService:
    def __init__(self, db):
        self.db = db

    def list_entries(self, limit: int = 100) -> list:
        with self.db.read() as conn:
            rows = conn.execute(
                "SELECT * FROM changelog ORDER BY change_date DESC, created_at DESC "
                "LIMIT ?", (limit,)).fetchall()
        return rows_to_dicts(rows)

    def add_entry(self, title: str, body: str, change_date: str, author: str) -> dict:
        title = (title or "").strip()
        body = (body or "").strip()
        if not title:
            raise ValueError("A title is required")
        if not body:
            raise ValueError("Body text is required")
        change_date = (change_date or "").strip() or _now()[:10]
        entry = {
            "id": secrets.token_hex(8),
            "title": title,
            "body": body,
            "change_date": change_date,
            "author": (author or "admin").strip(),
            "created_at": _now(),
        }
        with self.db.write() as conn:
            conn.execute(
                "INSERT INTO changelog (id, title, body, change_date, author, created_at) "
                "VALUES (:id, :title, :body, :change_date, :author, :created_at)", entry)
        return entry

    def delete_entry(self, entry_id: str) -> bool:
        with self.db.write() as conn:
            cur = conn.execute("DELETE FROM changelog WHERE id = ?", (entry_id,))
            return cur.rowcount > 0

    def get_entry(self, entry_id: str) -> dict:
        with self.db.read() as conn:
            return row_to_dict(conn.execute(
                "SELECT * FROM changelog WHERE id = ?", (entry_id,)).fetchone())
