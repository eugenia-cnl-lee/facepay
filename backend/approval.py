"""Approver registry + dual-control check (Tier 6 top rung).

Very high-value payments need a SECOND, different enrolled person to approve — a
two-person rule (dual control / maker-checker). Self-approval is explicitly
blocked: the payer cannot be their own approver. Only people designated as
approvers (e.g. a manager) can approve.
"""

import sqlite3

from template_store import DB_PATH, init_db


def _init():
    init_db()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS approvers (name TEXT PRIMARY KEY)")


def mark_approver(name):
    _init()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("INSERT OR IGNORE INTO approvers (name) VALUES (?)", (name,))


def is_approver(name):
    _init()
    with sqlite3.connect(DB_PATH) as conn:
        return conn.execute("SELECT 1 FROM approvers WHERE name = ?", (name,)).fetchone() is not None


def check(payer, approver):
    """Return (ok, reason) for a payer/approver pair."""
    if approver in ("UNKNOWN", "NO_FACE", None):
        return False, "approver not recognised"
    if approver == payer:
        return False, "self-approval blocked"
    if not is_approver(approver):
        return False, f"{approver} is not an authorised approver"
    return True, "approved"
