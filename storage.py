"""
storage.py
==========
Local persistence for the desktop app -- a drop-in replacement for the web
app's Postgres tables (connections / signals), backed by a single SQLite
file stored next to this script.

The connection row (email/password/cookies/token) is kept on disk so that,
just like the Replit version, credentials only need to be pasted once --
closing and reopening the app reconnects automatically using the saved
cookies/token (see engine_worker.EngineWorker + app.py's boot sequence).
"""

import json
import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

from pairs import all_pairs

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "signal_desk.db")

_lock = threading.Lock()


def _connect():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _lock:
        conn = _connect()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS connection (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    email TEXT,
                    password TEXT,
                    cookies TEXT,
                    token TEXT,
                    status TEXT NOT NULL DEFAULT 'disconnected',
                    last_error TEXT,
                    last_connected_at TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    market TEXT,
                    direction TEXT NOT NULL,
                    confidence INTEGER NOT NULL,
                    price REAL,
                    indicators TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "INSERT OR IGNORE INTO connection (id, status) VALUES (1, 'disconnected')"
            )
            conn.commit()
        finally:
            conn.close()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def mask_email(email):
    if not email:
        return None
    if "@" not in email:
        return email
    user, domain = email.split("@", 1)
    visible = user[: min(2, len(user))]
    stars = "*" * max(len(user) - len(visible), 3)
    return f"{visible}{stars}@{domain}"


def get_connection() -> dict:
    with _lock:
        conn = _connect()
        try:
            row = conn.execute("SELECT * FROM connection WHERE id = 1").fetchone()
            data = dict(row) if row else {}
        finally:
            conn.close()
    data["email_masked"] = mask_email(data.get("email"))
    return data


def save_credentials(email, password, cookies, token):
    with _lock:
        conn = _connect()
        try:
            conn.execute(
                """
                UPDATE connection
                SET email = ?, password = ?, cookies = ?, token = ?,
                    status = 'connecting', last_error = NULL
                WHERE id = 1
                """,
                (email, password, cookies, token),
            )
            conn.commit()
        finally:
            conn.close()


def set_status(status: str, last_error: str = None, connected: bool = False):
    with _lock:
        conn = _connect()
        try:
            if connected:
                conn.execute(
                    """
                    UPDATE connection
                    SET status = ?, last_error = NULL, last_connected_at = ?
                    WHERE id = 1
                    """,
                    (status, _now_iso()),
                )
            else:
                conn.execute(
                    "UPDATE connection SET status = ?, last_error = ? WHERE id = 1",
                    (status, last_error),
                )
            conn.commit()
        finally:
            conn.close()


def clear_connection():
    with _lock:
        conn = _connect()
        try:
            conn.execute(
                """
                UPDATE connection
                SET status = 'disconnected', cookies = NULL, token = NULL,
                    password = NULL, last_error = NULL
                WHERE id = 1
                """
            )
            conn.commit()
        finally:
            conn.close()


def insert_signal(event: dict):
    with _lock:
        conn = _connect()
        try:
            conn.execute(
                """
                INSERT INTO signals (symbol, display_name, market, direction, confidence, price, indicators, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.get("symbol"),
                    event.get("displayName"),
                    event.get("market"),
                    event.get("direction", "neutral"),
                    int(event.get("confidence") or 0),
                    event.get("price"),
                    json.dumps(event.get("indicators") or {}),
                    _now_iso(),
                ),
            )
            conn.commit()
        finally:
            conn.close()


def _row_to_signal(row) -> dict:
    d = dict(row)
    try:
        d["indicators"] = json.loads(d.get("indicators") or "{}")
    except (TypeError, ValueError):
        d["indicators"] = {}
    return d


def list_recent_signals(limit: int = 200) -> list:
    """Latest signal per symbol (mirrors the web app's /api/signals which
    shows the current read for every monitored pair), most recent first."""
    with _lock:
        conn = _connect()
        try:
            rows = conn.execute(
                """
                SELECT s.* FROM signals s
                INNER JOIN (
                    SELECT symbol, MAX(id) AS max_id FROM signals GROUP BY symbol
                ) latest ON latest.symbol = s.symbol AND latest.max_id = s.id
                ORDER BY s.created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        finally:
            conn.close()
    return [_row_to_signal(r) for r in rows]


def list_signal_history(limit: int = 100) -> list:
    with _lock:
        conn = _connect()
        try:
            rows = conn.execute(
                "SELECT * FROM signals ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        finally:
            conn.close()
    return [_row_to_signal(r) for r in rows]


def dashboard_summary() -> dict:
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    with _lock:
        conn = _connect()
        try:
            rows = conn.execute(
                "SELECT direction, confidence FROM signals WHERE created_at >= ?",
                (since,),
            ).fetchall()
            status_row = conn.execute("SELECT status FROM connection WHERE id = 1").fetchone()
        finally:
            conn.close()

    call_count = sum(1 for r in rows if r["direction"] == "call")
    put_count = sum(1 for r in rows if r["direction"] == "put")
    avg_confidence = round(sum(r["confidence"] for r in rows) / len(rows), 1) if rows else 0.0

    return {
        "activePairs": len(all_pairs()),
        "signalsLast24h": len(rows),
        "callCount": call_count,
        "putCount": put_count,
        "averageConfidence": avg_confidence,
        "connectionStatus": status_row["status"] if status_row else "disconnected",
    }
