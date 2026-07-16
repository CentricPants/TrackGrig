"""
models.py
Database access layer for PhantomTrack.
Uses SQLite for simplicity (swap for PostgreSQL by changing the connection
logic if you need multi-writer concurrency at scale).
"""

import sqlite3
import os
import secrets
from contextlib import contextmanager
from typing import Optional

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "database", "fleet.db")
SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "..", "database", "schema.sql")


def init_db():
    """Create tables if they don't exist yet."""
    with get_conn() as conn:
        with open(SCHEMA_PATH, "r") as f:
            conn.executescript(f.read())
        conn.commit()


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def register_device(device_id: str) -> str:
    """Create a device record (if missing) and return its API key."""
    api_key = secrets.token_hex(16)
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT api_key FROM devices WHERE device_id = ?", (device_id,)
        ).fetchone()
        if existing:
            return existing["api_key"]
        conn.execute(
            "INSERT INTO devices (device_id, api_key) VALUES (?, ?)",
            (device_id, api_key),
        )
        conn.commit()
    return api_key


def verify_device_key(device_id: str, api_key: str) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT api_key FROM devices WHERE device_id = ?", (device_id,)
        ).fetchone()
        return bool(row) and row["api_key"] == api_key


def upsert_location(device_id: str, lat: float, lon: float, speed: float, timestamp: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO locations (device_id, lat, lon, speed, timestamp) VALUES (?, ?, ?, ?, ?)",
            (device_id, lat, lon, speed, timestamp),
        )
        conn.execute(
            """UPDATE devices SET last_seen = ?, last_lat = ?, last_lon = ?, last_speed = ?
               WHERE device_id = ?""",
            (timestamp, lat, lon, speed, device_id),
        )
        conn.commit()


def get_last_location(device_id: str) -> Optional[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM locations WHERE device_id = ? ORDER BY id DESC LIMIT 1",
            (device_id,),
        ).fetchone()


def insert_alert(device_id: str, alert_type: str, description: str, timestamp: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO alerts (device_id, type, description, timestamp) VALUES (?, ?, ?, ?)",
            (device_id, alert_type, description, timestamp),
        )
        conn.commit()


def list_devices():
    with get_conn() as conn:
        return [dict(r) for r in conn.execute("SELECT device_id, last_seen, last_lat, last_lon, last_speed FROM devices").fetchall()]


def device_history(device_id: str, limit: int = 200):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT lat, lon, speed, timestamp FROM locations WHERE device_id = ? ORDER BY id DESC LIMIT ?",
            (device_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def list_alerts(limit: int = 100):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT device_id, type, description, timestamp FROM alerts ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
