"""Rate limiting for authentication attempts (Tier 3).

Defends the match path against hill-climbing / similarity-probing: an attacker
repeatedly presenting slightly tweaked inputs to climb the similarity score
toward acceptance. After too many failures inside a sliding window, further
attempts are locked out. Attempts are persisted in the DB, so restarting the
app does NOT reset the counter.
"""

import math
import sqlite3
from datetime import datetime, timezone

from template_store import DB_PATH, init_db

MAX_FAILURES = 5        # failures allowed within the window before lockout
WINDOW_SECONDS = 60     # sliding window length


def _init():
    init_db()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS auth_attempts (
                id INTEGER PRIMARY KEY,
                outcome TEXT NOT NULL,
                at REAL NOT NULL
            )
            """
        )


def _now():
    return datetime.now(timezone.utc).timestamp()


def record(outcome):
    _init()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("INSERT INTO auth_attempts (outcome, at) VALUES (?, ?)", (outcome, _now()))


def locked_out():
    """Return (is_locked, seconds_remaining)."""
    _init()
    cutoff = _now() - WINDOW_SECONDS
    with sqlite3.connect(DB_PATH) as conn:
        failure_times = [
            row[0]
            for row in conn.execute(
                "SELECT at FROM auth_attempts WHERE outcome = 'failure' AND at >= ? ORDER BY at",
                (cutoff,),
            ).fetchall()
        ]

    if len(failure_times) >= MAX_FAILURES:
        remaining = WINDOW_SECONDS - (_now() - failure_times[0])
        return True, max(1, math.ceil(remaining))
    return False, 0
