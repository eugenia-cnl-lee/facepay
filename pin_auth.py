"""PIN step-up authentication (Tier 6).

A PIN is a KNOWLEDGE factor used to step up higher-risk payments on top of the
face (an INHERENCE factor) — together, two-factor authentication.

Unlike a face template, a PIN is matched EXACTLY, so it is HASHED (salted
PBKDF2), never encrypted — the deliberate counterpart to the encrypt-not-hash
decision for biometrics (D20). Verification uses a constant-time comparison.
"""

import hashlib
import hmac
import os
import sqlite3

from template_store import DB_PATH, init_db

_ITERATIONS = 200_000


def _init():
    init_db()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pins (
                name TEXT PRIMARY KEY,
                salt BLOB NOT NULL,
                hash BLOB NOT NULL
            )
            """
        )


def _hash(pin, salt):
    return hashlib.pbkdf2_hmac("sha256", pin.encode(), salt, _ITERATIONS)


def set_pin(name, pin):
    _init()
    salt = os.urandom(16)
    digest = _hash(pin, salt)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO pins (name, salt, hash) VALUES (?, ?, ?)",
            (name, salt, digest),
        )


def has_pin(name):
    _init()
    with sqlite3.connect(DB_PATH) as conn:
        return conn.execute("SELECT 1 FROM pins WHERE name = ?", (name,)).fetchone() is not None


def verify_pin(name, pin):
    _init()
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("SELECT salt, hash FROM pins WHERE name = ?", (name,)).fetchone()
    if row is None:
        return False
    salt, stored = row
    return hmac.compare_digest(_hash(pin, salt), stored)
