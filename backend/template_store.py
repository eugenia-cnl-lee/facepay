"""Encrypted, persistent store for biometric templates (Tier 3).

Face embeddings are ENCRYPTED at rest with Fernet (AES) — deliberately not
hashed, because face matching is approximate and a hash can't be compared by
distance. Identity records and biometric templates live in SEPARATE tables. The
encryption key is kept OUT of the database in its own file; in production it
should live in an environment variable / OS keystore / KMS, never beside the DB.
"""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from cryptography.fernet import Fernet

DB_PATH = Path("faces.db")
KEY_PATH = Path("facepay.key")


# Key management

def load_key():
    if not KEY_PATH.exists():
        KEY_PATH.write_bytes(Fernet.generate_key())
    return Fernet(KEY_PATH.read_bytes())


# Schema

def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS identities (
                id INTEGER PRIMARY KEY,
                name TEXT UNIQUE NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS templates (
                id INTEGER PRIMARY KEY,
                identity_id INTEGER NOT NULL,
                vector BLOB NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (identity_id) REFERENCES identities(id) ON DELETE CASCADE
            )
            """
        )


def _now():
    return datetime.now(timezone.utc).isoformat()


# Enrolment

def enrol_identity(name, embeddings):
    """Encrypt and store one person's templates; replaces any existing ones."""
    init_db()
    fernet = load_key()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("DELETE FROM identities WHERE name = ?", (name,))   # clean re-enrol
        cursor = conn.execute(
            "INSERT INTO identities (name, created_at) VALUES (?, ?)", (name, _now())
        )
        identity_id = cursor.lastrowid
        for embedding in embeddings:
            blob = fernet.encrypt(np.asarray(embedding, dtype=np.float32).tobytes())
            conn.execute(
                "INSERT INTO templates (identity_id, vector, created_at) VALUES (?, ?, ?)",
                (identity_id, blob, _now()),
            )


# Matching

def load_database():
    """Return {name: [embeddings]} with templates decrypted in memory for matching."""
    init_db()
    fernet = load_key()
    database = {}
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            """
            SELECT identities.name, templates.vector
            FROM templates
            JOIN identities ON identities.id = templates.identity_id
            """
        ).fetchall()

    for name, blob in rows:
        vector = np.frombuffer(fernet.decrypt(blob), dtype=np.float32)
        database.setdefault(name, []).append(vector)
    return database


# Revocation (GDPR right to erasure)

def delete_identity(name):
    init_db()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("DELETE FROM identities WHERE name = ?", (name,))
