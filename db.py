"""
db.py — SQLite database layer for MyKare Voice Backend.

Uses Python's built-in sqlite3 module with WAL mode and foreign key
enforcement. All rows are returned as plain Python dicts.
"""

import contextlib
import os
import sqlite3
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

load_dotenv()

DB_PATH = os.getenv("DB_PATH", "mykare.db")


# ─── Connection Helper ─────────────────────────────────────────────


@contextlib.contextmanager
def get_connection():
    """Context manager that provides a sqlite3 connection with WAL mode,
    foreign key enforcement, and automatic commit/rollback semantics."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ─── Schema ────────────────────────────────────────────────────────


def init_db():
    """Create all tables (idempotent) and seed initial slot data."""
    with get_connection() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                phone      TEXT    UNIQUE NOT NULL,
                name       TEXT,
                created_at TEXT    DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS slots (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                slot_date TEXT    NOT NULL,
                slot_time TEXT    NOT NULL,
                is_booked INTEGER NOT NULL DEFAULT 0,
                UNIQUE (slot_date, slot_time)
            );

            CREATE TABLE IF NOT EXISTS appointments (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                phone      TEXT    NOT NULL,
                name       TEXT    NOT NULL,
                slot_date  TEXT    NOT NULL,
                slot_time  TEXT    NOT NULL,
                status     TEXT    NOT NULL DEFAULT 'confirmed'
                                  CHECK (status IN ('confirmed', 'cancelled', 'modified')),
                notes      TEXT,
                created_at TEXT    DEFAULT (datetime('now')),
                updated_at TEXT    DEFAULT (datetime('now')),
                FOREIGN KEY (phone) REFERENCES users(phone)
            );

            CREATE TABLE IF NOT EXISTS call_logs (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id        TEXT    UNIQUE NOT NULL,
                phone             TEXT,
                summary           TEXT,
                intent            TEXT,
                appointments_json TEXT,
                preferences       TEXT,
                duration_secs     INTEGER,
                started_at        TEXT    DEFAULT (datetime('now')),
                ended_at          TEXT
            );
            """
        )
    _seed_slots()
    print("✅ Database initialized.")


# ─── Slot Seeding ──────────────────────────────────────────────────


def _seed_slots():
    """Insert available time slots for the next 7 days (starting tomorrow).
    Uses INSERT OR IGNORE so re-running on restart never creates duplicates."""
    times = [
        "09:00", "09:30", "10:00", "10:30", "11:00", "11:30",
        "14:00", "14:30", "15:00", "15:30", "16:00",
    ]
    tomorrow = datetime.now().date() + timedelta(days=1)
    rows = []
    for day_offset in range(7):
        date_str = (tomorrow + timedelta(days=day_offset)).isoformat()
        for t in times:
            rows.append((date_str, t))

    with get_connection() as conn:
        cursor = conn.executemany(
            "INSERT OR IGNORE INTO slots (slot_date, slot_time) VALUES (?, ?)",
            rows,
        )
        inserted = cursor.rowcount
    print(f"📅 Seeded slots: {inserted} new slot(s) inserted.")


# ─── User Operations ──────────────────────────────────────────────


def upsert_user(phone: str, name: str = None) -> dict:
    """Create a user or update their name if they already exist."""
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO users (phone, name)
            VALUES (?, ?)
            ON CONFLICT(phone) DO UPDATE SET
                name = COALESCE(excluded.name, users.name)
            """,
            (phone, name),
        )
        row = conn.execute(
            "SELECT * FROM users WHERE phone = ?", (phone,)
        ).fetchone()
    return dict(row)


def get_user(phone: str):
    """Fetch a user by phone number. Returns dict or None."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE phone = ?", (phone,)
        ).fetchone()
    return dict(row) if row else None


# ─── Slot Operations ──────────────────────────────────────────────


def fetch_available_slots(limit: int = 10) -> list:
    """Return upcoming unbooked slots (today or later), ordered by date/time."""
    today = datetime.now().date().isoformat()
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT slot_date, slot_time
            FROM slots
            WHERE is_booked = 0 AND slot_date >= ?
            ORDER BY slot_date, slot_time
            LIMIT ?
            """,
            (today, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def is_slot_available(slot_date: str, slot_time: str) -> bool:
    """Return True if the slot exists and is not booked."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT is_booked FROM slots WHERE slot_date = ? AND slot_time = ?",
            (slot_date, slot_time),
        ).fetchone()
    return row is not None and row["is_booked"] == 0


def _mark_slot(slot_date: str, slot_time: str, booked: bool):
    """Mark a slot as booked (True) or available (False)."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE slots SET is_booked = ? WHERE slot_date = ? AND slot_time = ?",
            (1 if booked else 0, slot_date, slot_time),
        )


# ─── Appointment Operations ───────────────────────────────────────


def book_appointment(
    phone: str,
    name: str,
    slot_date: str,
    slot_time: str,
    notes: str = None,
) -> dict:
    """Book an appointment: ensure user, validate slot, insert, mark slot."""
    upsert_user(phone, name)

    if not is_slot_available(slot_date, slot_time):
        raise ValueError(
            f"Slot {slot_date} {slot_time} is not available for booking."
        )

    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO appointments (phone, name, slot_date, slot_time, notes)
            VALUES (?, ?, ?, ?, ?)
            """,
            (phone, name, slot_date, slot_time, notes),
        )
        appt_id = cursor.lastrowid
        # Mark slot inside the same transaction so both succeed or fail together
        conn.execute(
            "UPDATE slots SET is_booked = 1 WHERE slot_date = ? AND slot_time = ?",
            (slot_date, slot_time),
        )
        row = conn.execute(
            "SELECT * FROM appointments WHERE id = ?", (appt_id,)
        ).fetchone()
    return dict(row)


def get_appointments(phone: str, include_cancelled: bool = False) -> list:
    """Return appointments for a phone number, optionally including cancelled."""
    with get_connection() as conn:
        if include_cancelled:
            rows = conn.execute(
                """
                SELECT * FROM appointments
                WHERE phone = ?
                ORDER BY slot_date, slot_time
                """,
                (phone,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM appointments
                WHERE phone = ? AND status != 'cancelled'
                ORDER BY slot_date, slot_time
                """,
                (phone,),
            ).fetchall()
    return [dict(r) for r in rows]


def cancel_appointment(appointment_id: int, phone: str) -> dict:
    """Cancel an appointment (only if it belongs to the given phone)."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM appointments WHERE id = ? AND phone = ?",
            (appointment_id, phone),
        ).fetchone()
        if not row:
            raise ValueError(
                f"Appointment {appointment_id} not found for phone {phone}."
            )
        if row["status"] == "cancelled":
            raise ValueError(
                f"Appointment {appointment_id} is already cancelled."
            )

        now = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            """
            UPDATE appointments
            SET status = 'cancelled', updated_at = ?
            WHERE id = ?
            """,
            (now, appointment_id),
        )
        # Free the slot
        conn.execute(
            "UPDATE slots SET is_booked = 0 WHERE slot_date = ? AND slot_time = ?",
            (row["slot_date"], row["slot_time"]),
        )
        updated = conn.execute(
            "SELECT * FROM appointments WHERE id = ?", (appointment_id,)
        ).fetchone()
    return dict(updated)


def modify_appointment(
    appointment_id: int,
    phone: str,
    new_date: str,
    new_time: str,
) -> dict:
    """Reschedule a confirmed appointment to a new slot."""
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT * FROM appointments
            WHERE id = ? AND phone = ? AND status = 'confirmed'
            """,
            (appointment_id, phone),
        ).fetchone()
        if not row:
            raise ValueError(
                f"Confirmed appointment {appointment_id} not found for phone {phone}."
            )

        # Check new slot availability (read within same connection context)
        new_slot = conn.execute(
            "SELECT is_booked FROM slots WHERE slot_date = ? AND slot_time = ?",
            (new_date, new_time),
        ).fetchone()
        if new_slot is None or new_slot["is_booked"] != 0:
            raise ValueError(
                f"Slot {new_date} {new_time} is not available for rebooking."
            )

        old_date, old_time = row["slot_date"], row["slot_time"]
        now = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        conn.execute(
            """
            UPDATE appointments
            SET slot_date = ?, slot_time = ?, status = 'modified', updated_at = ?
            WHERE id = ?
            """,
            (new_date, new_time, now, appointment_id),
        )
        # Free old slot
        conn.execute(
            "UPDATE slots SET is_booked = 0 WHERE slot_date = ? AND slot_time = ?",
            (old_date, old_time),
        )
        # Book new slot
        conn.execute(
            "UPDATE slots SET is_booked = 1 WHERE slot_date = ? AND slot_time = ?",
            (new_date, new_time),
        )
        updated = conn.execute(
            "SELECT * FROM appointments WHERE id = ?", (appointment_id,)
        ).fetchone()
    return dict(updated)


# ─── Call Log Operations ──────────────────────────────────────────


def create_call_log(session_id: str, phone: str = None) -> dict:
    """Insert a new call log (safe to call multiple times — uses INSERT OR IGNORE)."""
    with get_connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO call_logs (session_id, phone) VALUES (?, ?)",
            (session_id, phone),
        )
        row = conn.execute(
            "SELECT * FROM call_logs WHERE session_id = ?", (session_id,)
        ).fetchone()
    return dict(row)


_ALLOWED_CALL_LOG_FIELDS = {
    "phone", "summary", "intent", "appointments_json",
    "preferences", "duration_secs", "ended_at",
}


def update_call_log(session_id: str, **kwargs) -> dict:
    """Update specific fields on a call log entry."""
    fields = {k: v for k, v in kwargs.items() if k in _ALLOWED_CALL_LOG_FIELDS}
    if not fields:
        raise ValueError(
            "No valid fields provided. Allowed: "
            + ", ".join(sorted(_ALLOWED_CALL_LOG_FIELDS))
        )

    set_clause = ", ".join(f"{col} = ?" for col in fields)
    values = list(fields.values()) + [session_id]

    with get_connection() as conn:
        conn.execute(
            f"UPDATE call_logs SET {set_clause} WHERE session_id = ?",
            values,
        )
        row = conn.execute(
            "SELECT * FROM call_logs WHERE session_id = ?", (session_id,)
        ).fetchone()
    return dict(row)


def get_call_log(session_id: str):
    """Fetch a call log by session_id. Returns dict or None."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM call_logs WHERE session_id = ?", (session_id,)
        ).fetchone()
    return dict(row) if row else None


# ─── Health Check ─────────────────────────────────────────────────


def db_health() -> dict:
    """Run a read-only health check on the database. Never raises."""
    try:
        with get_connection() as conn:
            tables = {}
            for table in ("users", "slots", "appointments", "call_logs"):
                count = conn.execute(
                    f"SELECT COUNT(*) AS cnt FROM {table}"
                ).fetchone()["cnt"]
                tables[table] = count

            available = conn.execute(
                "SELECT COUNT(*) AS cnt FROM slots WHERE is_booked = 0"
            ).fetchone()["cnt"]

        return {
            "status": "ok",
            "db_path": DB_PATH,
            "tables": tables,
            "available_slots": available,
        }
    except Exception as exc:
        return {
            "status": "error",
            "db_path": DB_PATH,
            "detail": str(exc),
        }


# ─── Validation ───────────────────────────────────────────────────

if __name__ == "__main__":
    import json

    print("=" * 60)
    print("  MyKare DB — Validation Suite")
    print("=" * 60)

    # Clean up any previous test database
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)

    # Step 1 — Initialize database
    print("\n[1/9] Initializing database...")
    init_db()
    print("     ✅ init_db() completed.")

    # Step 2 — Health check
    print("\n[2/9] Running health check...")
    health = db_health()
    print(f"     {json.dumps(health, indent=6)}")
    assert health["status"] == "ok", "Health check failed!"
    print("     ✅ Health check passed.")

    # Step 3 — Fetch available slots
    print("\n[3/9] Fetching available slots (first 3)...")
    slots = fetch_available_slots(limit=3)
    for s in slots:
        print(f"     📅 {s['slot_date']}  🕐 {s['slot_time']}")
    assert len(slots) == 3, f"Expected 3 slots, got {len(slots)}"
    print("     ✅ Slots fetched.")

    # Step 4 — Book appointment
    first_slot = slots[0]
    print(f"\n[4/9] Booking appointment at {first_slot['slot_date']} {first_slot['slot_time']}...")
    appt = book_appointment(
        phone="9876543210",
        name="Test User",
        slot_date=first_slot["slot_date"],
        slot_time=first_slot["slot_time"],
    )
    print(f"     Appointment ID: {appt['id']}, Status: {appt['status']}")
    assert appt["status"] == "confirmed"
    print("     ✅ Appointment booked.")

    # Step 5 — Double-book same slot (expect ValueError)
    print(f"\n[5/9] Attempting to double-book the same slot...")
    try:
        book_appointment(
            phone="1111111111",
            name="Other User",
            slot_date=first_slot["slot_date"],
            slot_time=first_slot["slot_time"],
        )
        raise AssertionError("Should have raised ValueError!")
    except ValueError as e:
        print(f"     Caught expected error: {e}")
    print("     ✅ Double-booking correctly prevented.")

    # Step 6 — Fetch appointments
    print("\n[6/9] Fetching appointments for 9876543210...")
    appts = get_appointments("9876543210")
    print(f"     Found {len(appts)} appointment(s).")
    assert len(appts) == 1, f"Expected 1 appointment, got {len(appts)}"
    print("     ✅ Appointment list correct.")

    # Step 7 — Cancel appointment
    print(f"\n[7/9] Cancelling appointment {appt['id']}...")
    cancelled = cancel_appointment(appt["id"], "9876543210")
    print(f"     Status: {cancelled['status']}")
    assert cancelled["status"] == "cancelled"
    print("     ✅ Appointment cancelled.")

    # Step 8 — Confirm slot is available again
    print(f"\n[8/9] Checking if slot {first_slot['slot_date']} {first_slot['slot_time']} is available again...")
    available = is_slot_available(first_slot["slot_date"], first_slot["slot_time"])
    print(f"     Available: {available}")
    assert available is True
    print("     ✅ Slot correctly freed.")

    # Step 9 — Final health check
    print("\n[9/9] Final health check...")
    final_health = db_health()
    print(f"     {json.dumps(final_health, indent=6)}")
    assert final_health["status"] == "ok"
    print("     ✅ Final health check passed.")

    print("\n" + "=" * 60)
    print("  🎉 All 9 validation steps passed!")
    print("=" * 60)
