"""
SQLite data layer shared by the user-facing booking bot and the admin bot.
Single file DB, safe for one running instance (per your choice).
"""

import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta

from config import DB_PATH, CANCEL_FREE_HOURS


# ─────────────────────────────────────────────
#  CONNECTION
# ─────────────────────────────────────────────
@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS reservations (
                id              TEXT PRIMARY KEY,
                tg_user_id      INTEGER NOT NULL,
                tg_username     TEXT,
                full_name       TEXT NOT NULL,
                phone           TEXT NOT NULL,
                hall_key        TEXT NOT NULL,
                date_str        TEXT NOT NULL,      -- 'YYYY-MM-DD'
                start_hour      INTEGER NOT NULL,
                duration        INTEGER NOT NULL,
                price           INTEGER NOT NULL,
                status          TEXT NOT NULL DEFAULT 'active',  -- active|cancelled
                created_at      TEXT NOT NULL,
                cancelled_at    TEXT
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_res_hall_date
            ON reservations(hall_key, date_str, status)
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_profiles (
                tg_user_id   INTEGER PRIMARY KEY,
                full_name    TEXT,
                phone        TEXT
            )
        """)


# ─────────────────────────────────────────────
#  PROFILE HELPERS (so returning users don't retype name/phone)
# ─────────────────────────────────────────────
def get_profile(tg_user_id: int):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM user_profiles WHERE tg_user_id=?", (tg_user_id,)
        ).fetchone()
        return dict(row) if row else None


def upsert_profile(tg_user_id: int, full_name: str, phone: str):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO user_profiles (tg_user_id, full_name, phone)
            VALUES (?, ?, ?)
            ON CONFLICT(tg_user_id) DO UPDATE SET
                full_name=excluded.full_name,
                phone=excluded.phone
        """, (tg_user_id, full_name, phone))


# ─────────────────────────────────────────────
#  AVAILABILITY
# ─────────────────────────────────────────────
def booked_hours(hall_key: str, date_str: str) -> set:
    """Return the set of hours already taken for a hall/date."""
    taken = set()
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT start_hour, duration FROM reservations
            WHERE hall_key=? AND date_str=? AND status='active'
        """, (hall_key, date_str)).fetchall()
    for r in rows:
        for h in range(r["start_hour"], r["start_hour"] + r["duration"]):
            taken.add(h)
    return taken


def can_book(hall_key: str, date_str: str, start_hour: int, duration: int) -> bool:
    taken = booked_hours(hall_key, date_str)
    wanted = set(range(start_hour, start_hour + duration))
    return taken.isdisjoint(wanted)


def available_starts(hall_key: str, date_str: str, duration: int, slot_starts, end_cap: int):
    return [
        s for s in slot_starts
        if s + duration <= end_cap and can_book(hall_key, date_str, s, duration)
    ]


# ─────────────────────────────────────────────
#  RESERVATIONS — CRUD
# ─────────────────────────────────────────────
def gen_id() -> str:
    return "RES-" + str(uuid.uuid4())[:8].upper()


def count_active_for_user(tg_user_id: int) -> int:
    with get_conn() as conn:
        row = conn.execute("""
            SELECT COUNT(*) AS c FROM reservations
            WHERE tg_user_id=? AND status='active'
        """, (tg_user_id,)).fetchone()
        return row["c"]


def create_reservation(tg_user_id, tg_username, full_name, phone,
                        hall_key, date_str, start_hour, duration, price):
    rid = gen_id()
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO reservations
            (id, tg_user_id, tg_username, full_name, phone, hall_key,
             date_str, start_hour, duration, price, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?)
        """, (
            rid, tg_user_id, tg_username, full_name, phone, hall_key,
            date_str, start_hour, duration, price,
            datetime.now().strftime("%Y-%m-%d %H:%M"),
        ))
    return rid


def get_reservation(res_id: str):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM reservations WHERE id=?", (res_id,)).fetchone()
        return dict(row) if row else None


def list_user_reservations(tg_user_id: int, only_active=False):
    q = "SELECT * FROM reservations WHERE tg_user_id=?"
    if only_active:
        q += " AND status='active'"
    q += " ORDER BY date_str DESC, start_hour DESC"
    with get_conn() as conn:
        rows = conn.execute(q, (tg_user_id,)).fetchall()
        return [dict(r) for r in rows]


def list_all_reservations(only_active=False, hall_key=None, date_str=None):
    q = "SELECT * FROM reservations WHERE 1=1"
    params = []
    if only_active:
        q += " AND status='active'"
    if hall_key:
        q += " AND hall_key=?"
        params.append(hall_key)
    if date_str:
        q += " AND date_str=?"
        params.append(date_str)
    q += " ORDER BY date_str ASC, start_hour ASC"
    with get_conn() as conn:
        rows = conn.execute(q, params).fetchall()
        return [dict(r) for r in rows]


def cancellation_fee(res: dict):
    """
    Returns (fee_amount, is_free) — mirrors the Streamlit app's 48h rule.
    """
    res_start = datetime.strptime(
        res["date_str"] + f" {res['start_hour']:02d}:00", "%Y-%m-%d %H:%M"
    )
    hours_left = (res_start - datetime.now()).total_seconds() / 3600
    if hours_left >= CANCEL_FREE_HOURS:
        return 0, True
    return res["price"], False


def cancel_reservation(res_id: str) -> bool:
    with get_conn() as conn:
        cur = conn.execute("""
            UPDATE reservations SET status='cancelled', cancelled_at=?
            WHERE id=? AND status='active'
        """, (datetime.now().strftime("%Y-%m-%d %H:%M"), res_id))
        return cur.rowcount > 0


def upcoming_active(days_ahead: int = 14):
    """All active reservations from today forward — used by admin 'today/week' views."""
    today_str = datetime.now().strftime("%Y-%m-%d")
    cutoff_str = (datetime.now() + timedelta(days=days_ahead)).strftime("%Y-%m-%d")
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT * FROM reservations
            WHERE status='active' AND date_str>=? AND date_str<=?
            ORDER BY date_str ASC, start_hour ASC
        """, (today_str, cutoff_str)).fetchall()
        return [dict(r) for r in rows]
