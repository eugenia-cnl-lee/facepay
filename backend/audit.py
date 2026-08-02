"""Append-only audit log (Tier 3).

Records security-relevant events — enrolment, authentication decisions,
liveness failures, lockouts — for non-repudiation and forensics. Entries are
only ever inserted, never updated or deleted.
"""

import sqlite3
from datetime import datetime, timezone

from template_store import DB_PATH, init_db


def _init():
    init_db()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY,
                at TEXT NOT NULL,
                event TEXT NOT NULL,
                subject TEXT,
                detail TEXT
            )
            """
        )


def log(event, subject=None, detail=None):
    _init()
    at = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO audit_log (at, event, subject, detail) VALUES (?, ?, ?, ?)",
            (at, event, subject, detail),
        )


def recent(limit=20):
    _init()
    with sqlite3.connect(DB_PATH) as conn:
        return conn.execute(
            "SELECT at, event, subject, detail FROM audit_log ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
