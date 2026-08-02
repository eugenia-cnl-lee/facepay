"""Wallet + accounts store for the payment layer (Tier 4).

Deliberately kept in a SEPARATE database from the biometric store (faces.db):
financial and biometric data are isolated (least privilege, smaller blast
radius). Money is stored as integer pence, never floats. Transfers are atomic
(one DB transaction) and idempotent (a repeated idempotency key charges once).
"""

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path("wallet.db")


class InsufficientFunds(Exception):
    pass


def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS accounts (
                id INTEGER PRIMARY KEY,
                name TEXT UNIQUE NOT NULL,
                kind TEXT NOT NULL,
                balance_pence INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY,
                idempotency_key TEXT UNIQUE NOT NULL,
                payer TEXT NOT NULL,
                payee TEXT NOT NULL,
                amount_pence INTEGER NOT NULL,
                kind TEXT NOT NULL,
                at TEXT NOT NULL
            )
            """
        )


def _now():
    return datetime.now(timezone.utc).isoformat()


def ensure_account(name, kind, opening_balance_pence=0):
    init_db()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO accounts (name, kind, balance_pence, created_at) "
            "VALUES (?, ?, ?, ?)",
            (name, kind, opening_balance_pence, _now()),
        )


def get_balance(name):
    init_db()
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT balance_pence FROM accounts WHERE name = ?", (name,)
        ).fetchone()
    return row[0] if row else None


def _find_payment(conn, idempotency_key):
    return conn.execute(
        "SELECT payer, payee, amount_pence FROM payments WHERE idempotency_key = ?",
        (idempotency_key,),
    ).fetchone()


def transfer(idempotency_key, payer, payee, amount_pence, kind="payment"):
    """Atomically move money payer -> payee. Idempotent on idempotency_key."""
    init_db()
    with sqlite3.connect(DB_PATH) as conn:
        existing = _find_payment(conn, idempotency_key)
        if existing:
            return {"status": "duplicate", "amount_pence": existing[2]}

        payer_row = conn.execute(
            "SELECT balance_pence FROM accounts WHERE name = ?", (payer,)
        ).fetchone()
        if payer_row is None:
            raise ValueError(f"No such account: {payer}")
        if payer_row[0] < amount_pence:
            raise InsufficientFunds(f"{payer} has {payer_row[0]}p, needs {amount_pence}p")

        # all three statements commit together, or not at all (atomicity)
        conn.execute(
            "UPDATE accounts SET balance_pence = balance_pence - ? WHERE name = ?",
            (amount_pence, payer),
        )
        conn.execute(
            "UPDATE accounts SET balance_pence = balance_pence + ? WHERE name = ?",
            (amount_pence, payee),
        )
        conn.execute(
            "INSERT INTO payments (idempotency_key, payer, payee, amount_pence, kind, at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (idempotency_key, payer, payee, amount_pence, kind, _now()),
        )
    return {"status": "ok", "amount_pence": amount_pence}


def topup(name, amount_pence):
    """Load funds onto an account (money coming in)."""
    ensure_account(name, "customer")
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "UPDATE accounts SET balance_pence = balance_pence + ? WHERE name = ?",
            (amount_pence, name),
        )
        conn.execute(
            "INSERT INTO payments (idempotency_key, payer, payee, amount_pence, kind, at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (uuid.uuid4().hex, "TOPUP", name, amount_pence, "topup", _now()),
        )


def refund(original_key):
    """Reverse a prior payment: credit the original payer, debit the payee.

    The refund's idempotency key is derived from the original, so refunding the
    same payment twice is a no-op.
    """
    init_db()
    with sqlite3.connect(DB_PATH) as conn:
        original = _find_payment(conn, original_key)
    if original is None:
        raise ValueError("Original payment not found")
    payer, payee, amount_pence = original
    return transfer(f"refund:{original_key}", payee, payer, amount_pence, kind="refund")


def refund_by_id(payment_id):
    """Refund a payment referenced by its row id (as shown in history)."""
    init_db()
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT idempotency_key FROM payments WHERE id = ? AND kind = 'payment'",
            (payment_id,),
        ).fetchone()
    if row is None:
        raise ValueError(f"no payment with id {payment_id}")
    return refund(row[0])


def history(name, limit=20):
    init_db()
    with sqlite3.connect(DB_PATH) as conn:
        return conn.execute(
            "SELECT id, at, kind, payer, payee, amount_pence FROM payments "
            "WHERE payer = ? OR payee = ? ORDER BY id DESC LIMIT ?",
            (name, name, limit),
        ).fetchall()


def format_money(pence):
    return f"£{pence / 100:.2f}"
