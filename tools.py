"""
tools.py — Agent tool functions for MyKare Voice Backend.

These 7 functions are the interface between the LLM agent and the database.
Every function returns a plain dict with a "success" key (True/False) so the
LLM can reliably determine the outcome before forming a voice response.

Return shapes:
  Success: {"success": True,  "data": <result>, "message": <human readable>}
  Failure: {"success": False, "error": <code>,   "message": <human readable>}
"""

import json
import re
from collections import defaultdict
from datetime import datetime, timezone

from db import (
    book_appointment as db_book_appointment,
    cancel_appointment as db_cancel_appointment,
    create_call_log,
    fetch_available_slots,
    get_appointments,
    get_call_log,
    init_db,
    is_slot_available,
    modify_appointment as db_modify_appointment,
    update_call_log,
    upsert_user,
)


# ─── Helpers ──────────────────────────────────────────────────────


def _clean_phone(phone: str) -> str:
    """Strip spaces, dashes, brackets, and plus signs from a phone number."""
    return re.sub(r"[\s\-\(\)\+]", "", phone)


def _validate_phone(phone: str) -> bool:
    """Return True if the cleaned phone is 10–15 digits."""
    return bool(re.fullmatch(r"\d{10,15}", phone))


def _validate_date_format(date_str: str) -> bool:
    """Return True if date_str is a valid YYYY-MM-DD date."""
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def _validate_time_format(time_str: str) -> bool:
    """Return True if time_str is a valid HH:MM 24-hour time."""
    try:
        datetime.strptime(time_str, "%H:%M")
        return True
    except ValueError:
        return False


def _is_past_date(date_str: str) -> bool:
    """Return True if the date is strictly before today."""
    return datetime.strptime(date_str, "%Y-%m-%d").date() < datetime.now().date()


def _format_date_human(date_str: str) -> str:
    """Convert YYYY-MM-DD to a human-readable string like 'Thursday, May 1st'."""
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    day = dt.day
    if 11 <= day <= 13:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    return dt.strftime(f"%A, %B {day}{suffix}")


def _format_time_human(time_str: str) -> str:
    """Convert HH:MM 24hr to a human-readable string like '2:30 PM'."""
    dt = datetime.strptime(time_str, "%H:%M")
    if dt.minute == 0:
        return dt.strftime("%-I %p")
    return dt.strftime("%-I:%M %p")


def _format_appointment_human(appt: dict) -> str:
    """Build a natural language string for an appointment."""
    date_h = _format_date_human(appt["slot_date"])
    time_h = _format_time_human(appt["slot_time"])
    status = appt.get("status", "confirmed").capitalize()
    return f"{date_h} at {time_h} — Status: {status}"


# ─── TOOL 1 — identify_user ──────────────────────────────────────


def identify_user(phone: str, name: str = None) -> dict:
    """Identify or register a caller by phone number."""
    try:
        if not phone or not isinstance(phone, str):
            return {
                "success": False,
                "error": "invalid_phone",
                "message": "I didn't catch your phone number. "
                           "Could you please repeat it clearly?",
            }

        cleaned = _clean_phone(phone)

        if not _validate_phone(cleaned):
            return {
                "success": False,
                "error": "invalid_phone",
                "message": "That doesn't seem like a valid phone number. "
                           "Could you please share your 10-digit mobile number?",
            }

        user = upsert_user(cleaned, name)
        existing_name = user.get("name")

        if existing_name and name is None:
            # User already existed with a name — returning visitor
            return {
                "success": True,
                "data": user,
                "message": f"Welcome back, {existing_name}! How can I help you today?",
            }
        elif existing_name and name is not None:
            # User existed, but we just updated their name
            return {
                "success": True,
                "data": user,
                "message": f"Welcome back, {existing_name}! "
                           "I've updated your name on file.",
            }
        else:
            return {
                "success": True,
                "data": user,
                "message": "Got it, I've noted your number. "
                           "Could I also get your name, please?",
            }
    except Exception as exc:
        return {
            "success": False,
            "error": "internal_error",
            "message": f"I'm sorry, something went wrong while looking you up. "
                       f"Please try again. ({exc})",
        }


# ─── TOOL 2 — fetch_slots ────────────────────────────────────────


def fetch_slots(preferred_date: str = None) -> dict:
    """Return available appointment slots, optionally filtered by date."""
    try:
        all_slots = fetch_available_slots(limit=15)

        if not all_slots:
            return {
                "success": False,
                "error": "no_slots_available",
                "message": "I'm sorry, there are no available appointment "
                           "slots right now. Please try again tomorrow.",
            }

        filtered = all_slots
        date_note = None

        if preferred_date:
            if not _validate_date_format(preferred_date):
                return {
                    "success": False,
                    "error": "invalid_date",
                    "message": "I couldn't understand that date. "
                               "Could you please say it again?",
                }
            date_filtered = [
                s for s in all_slots if s["slot_date"] == preferred_date
            ]
            if date_filtered:
                filtered = date_filtered
            else:
                date_note = (
                    f"There are no slots available on "
                    f"{_format_date_human(preferred_date)}, "
                    f"but here are some other options."
                )

        # Group by date
        grouped = defaultdict(list)
        for s in filtered:
            grouped[s["slot_date"]].append(s["slot_time"])

        grouped_output = [
            {
                "date": date,
                "date_human": _format_date_human(date),
                "times": times,
                "times_human": [_format_time_human(t) for t in times],
            }
            for date, times in sorted(grouped.items())
        ]

        total = sum(len(g["times"]) for g in grouped_output)
        msg = f"I found {total} available slot{'s' if total != 1 else ''} "
        msg += f"across {len(grouped_output)} day{'s' if len(grouped_output) != 1 else ''}."
        if date_note:
            msg = date_note + " " + msg

        return {
            "success": True,
            "data": grouped_output,
            "message": msg,
        }
    except Exception as exc:
        return {
            "success": False,
            "error": "internal_error",
            "message": f"I'm sorry, I couldn't fetch the available slots "
                       f"right now. Please try again. ({exc})",
        }


# ─── TOOL 3 — book_appointment ───────────────────────────────────


def book_appointment(
    phone: str,
    name: str,
    slot_date: str,
    slot_time: str,
    notes: str = None,
) -> dict:
    """Book an appointment for the caller."""
    try:
        # Validate required fields
        missing = []
        if not phone:
            missing.append("phone number")
        if not name:
            missing.append("name")
        if not slot_date:
            missing.append("date")
        if not slot_time:
            missing.append("time")
        if missing:
            return {
                "success": False,
                "error": "missing_fields",
                "message": f"I still need your {', '.join(missing)} "
                           "to complete the booking.",
            }

        # Validate formats
        if not _validate_date_format(slot_date):
            return {
                "success": False,
                "error": "invalid_date",
                "message": "That date doesn't look right. "
                           "Could you please say it again?",
            }
        if not _validate_time_format(slot_time):
            return {
                "success": False,
                "error": "invalid_time",
                "message": "I couldn't understand the time. "
                           "Could you tell me the time you'd prefer?",
            }

        # Check for past date
        if _is_past_date(slot_date):
            return {
                "success": False,
                "error": "past_date",
                "message": "That date has already passed. "
                           "Would you like to pick a date from today onwards?",
            }

        appt = db_book_appointment(phone, name, slot_date, slot_time, notes)

        date_h = _format_date_human(slot_date)
        time_h = _format_time_human(slot_time)
        return {
            "success": True,
            "data": appt,
            "message": f"Your appointment is confirmed for {date_h} "
                       f"at {time_h}. We look forward to seeing you, {name}!",
        }
    except ValueError as ve:
        return {
            "success": False,
            "error": "slot_unavailable",
            "message": "That time slot is already taken. "
                       "Let me fetch other available options for you.",
        }
    except Exception as exc:
        return {
            "success": False,
            "error": "internal_error",
            "message": f"I'm sorry, I couldn't complete the booking "
                       f"right now. Please try again. ({exc})",
        }


# ─── TOOL 4 — retrieve_appointments ──────────────────────────────


def retrieve_appointments(phone: str) -> dict:
    """Fetch all active appointments for a caller."""
    try:
        if not phone:
            return {
                "success": False,
                "error": "missing_fields",
                "message": "I need your phone number to look up your appointments.",
            }

        appts = get_appointments(phone, include_cancelled=False)

        if not appts:
            return {
                "success": True,
                "data": [],
                "message": "You don't have any upcoming appointments at the moment.",
            }

        # Enrich each appointment with a human-readable summary
        for a in appts:
            a["human_readable"] = _format_appointment_human(a)

        count = len(appts)
        return {
            "success": True,
            "data": appts,
            "message": f"You have {count} upcoming appointment{'s' if count != 1 else ''}.",
        }
    except Exception as exc:
        return {
            "success": False,
            "error": "internal_error",
            "message": f"I'm sorry, I couldn't retrieve your appointments "
                       f"right now. Please try again. ({exc})",
        }


# ─── TOOL 5 — cancel_appointment ─────────────────────────────────


def cancel_appointment(appointment_id: int, phone: str) -> dict:
    """Cancel a specific appointment by ID."""
    try:
        if not appointment_id or not phone:
            return {
                "success": False,
                "error": "missing_fields",
                "message": "I need the appointment details and your phone "
                           "number to cancel it.",
            }

        cancelled = db_cancel_appointment(appointment_id, phone)
        date_h = _format_date_human(cancelled["slot_date"])
        time_h = _format_time_human(cancelled["slot_time"])

        return {
            "success": True,
            "data": cancelled,
            "message": f"Done! Your appointment on {date_h} at {time_h} "
                       "has been cancelled. The slot is now available again.",
        }
    except ValueError as ve:
        msg = str(ve)
        if "already cancelled" in msg.lower():
            return {
                "success": False,
                "error": "already_cancelled",
                "message": "That appointment has already been cancelled.",
            }
        return {
            "success": False,
            "error": "not_found",
            "message": "I couldn't find that appointment under your phone number. "
                       "Would you like me to look up your appointments?",
        }
    except Exception as exc:
        return {
            "success": False,
            "error": "internal_error",
            "message": f"I'm sorry, something went wrong while cancelling. "
                       f"Please try again. ({exc})",
        }


# ─── TOOL 6 — modify_appointment ─────────────────────────────────


def modify_appointment(
    appointment_id: int,
    phone: str,
    new_date: str,
    new_time: str,
) -> dict:
    """Reschedule an existing appointment to a new date and time."""
    try:
        # Validate required fields
        missing = []
        if not appointment_id:
            missing.append("appointment ID")
        if not phone:
            missing.append("phone number")
        if not new_date:
            missing.append("new date")
        if not new_time:
            missing.append("new time")
        if missing:
            return {
                "success": False,
                "error": "missing_fields",
                "message": f"I still need the {', '.join(missing)} "
                           "to reschedule your appointment.",
            }

        # Validate date format
        if not _validate_date_format(new_date):
            return {
                "success": False,
                "error": "invalid_date",
                "message": "I couldn't understand the new date. "
                           "Could you say it again?",
            }

        # Check for past date
        if _is_past_date(new_date):
            return {
                "success": False,
                "error": "past_date",
                "message": "That date has already passed. "
                           "Let me find available slots from today onwards.",
            }

        # Fetch old appointment details before modifying so we can
        # include the old slot in the confirmation message.
        old_appts = get_appointments(phone, include_cancelled=False)
        old_appt = next((a for a in old_appts if a["id"] == appointment_id), None)

        updated = db_modify_appointment(appointment_id, phone, new_date, new_time)

        new_date_h = _format_date_human(new_date)
        new_time_h = _format_time_human(new_time)

        if old_appt:
            old_date_h = _format_date_human(old_appt["slot_date"])
            old_time_h = _format_time_human(old_appt["slot_time"])
            msg = (
                f"Done! Your appointment has been moved from "
                f"{old_date_h} at {old_time_h} to "
                f"{new_date_h} at {new_time_h}."
            )
        else:
            msg = (
                f"Your appointment has been rescheduled to "
                f"{new_date_h} at {new_time_h}."
            )

        return {
            "success": True,
            "data": updated,
            "message": msg,
        }
    except ValueError as ve:
        msg = str(ve)
        if "not available" in msg.lower():
            return {
                "success": False,
                "error": "slot_unavailable",
                "message": "The new time slot you requested isn't available. "
                           "Let me check what else is open for you.",
            }
        return {
            "success": False,
            "error": "not_found",
            "message": "I couldn't find a confirmed appointment with those "
                       "details. Would you like me to look up your appointments?",
        }
    except Exception as exc:
        return {
            "success": False,
            "error": "internal_error",
            "message": f"I'm sorry, something went wrong while rescheduling. "
                       f"Please try again. ({exc})",
        }


# ─── TOOL 7 — end_conversation ───────────────────────────────────


def end_conversation(
    session_id: str,
    phone: str = None,
    conversation_history: list = None,
) -> dict:
    """Wrap up the call: extract a summary and persist it to call_logs."""
    try:
        if not session_id:
            return {
                "success": False,
                "error": "missing_fields",
                "message": "I need a session ID to close this conversation.",
            }

        conversation_history = conversation_history or []

        # ── Extract intent from conversation text ──
        all_text = " ".join(
            msg.get("content", "") for msg in conversation_history
        ).lower()

        intent = "inquiry"  # default
        if any(kw in all_text for kw in ["book", "schedule", "appointment for"]):
            intent = "booking"
        elif any(kw in all_text for kw in ["cancel", "remove", "delete"]):
            intent = "cancellation"
        elif any(kw in all_text for kw in ["modify", "change", "reschedule", "move"]):
            intent = "modification"
        elif any(kw in all_text for kw in ["check", "view", "see", "list", "show"]):
            intent = "inquiry"
        elif any(kw in all_text for kw in ["human", "person", "connect", "escalat"]):
            intent = "escalation"

        # ── Extract phone from conversation if not provided ──
        if not phone:
            phone_match = re.search(r"\b\d{10}\b", all_text)
            if phone_match:
                phone = phone_match.group()

        # ── Fetch appointments for the user ──
        appointments = []
        if phone:
            appointments = get_appointments(phone, include_cancelled=False)

        # ── Build summary ──
        ended_at = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        appt_ids = [a["id"] for a in appointments]

        summary = {
            "session_id": session_id,
            "phone": phone,
            "intent": intent,
            "appointments": appointments,
            "total_messages": len(conversation_history),
            "ended_at": ended_at,
        }

        # ── Persist to call_logs ──
        create_call_log(session_id, phone)
        update_call_log(
            session_id,
            phone=phone,
            intent=intent,
            appointments_json=json.dumps(appt_ids),
            ended_at=ended_at,
            summary=f"Call with intent '{intent}', "
                    f"{len(conversation_history)} messages exchanged.",
        )

        return {
            "success": True,
            "data": summary,
            "message": "Thank you for calling Mykare Health. "
                       "Have a wonderful day! Goodbye.",
        }
    except Exception as exc:
        return {
            "success": False,
            "error": "internal_error",
            "message": f"I'm sorry, I had trouble wrapping up. "
                       f"Your appointments are safe. ({exc})",
        }


# ─── Validation ───────────────────────────────────────────────────

if __name__ == "__main__":
    import os

    from prompts import SYSTEM_PROMPT

    print("=" * 60)
    print("  MyKare Tools — Validation Suite")
    print("=" * 60)

    # Clean up any previous test database
    DB_PATH = os.getenv("DB_PATH", "mykare.db")
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    init_db()

    passed = 0
    failed = 0

    def report(step: int, label: str, result: dict, expect_success: bool,
               expect_error: str = None):
        global passed, failed
        ok = result.get("success") == expect_success
        if expect_error and not expect_success:
            ok = ok and result.get("error") == expect_error
        tag = "✅ PASS" if ok else "❌ FAIL"
        if ok:
            passed += 1
        else:
            failed += 1
        print(f"\n[{step}/10] {label}")
        print(f"  {tag}")
        print(f"  success={result.get('success')}, "
              f"error={result.get('error', '—')}")
        print(f"  message: {result.get('message', '—')}")
        if not ok:
            print(f"  ⚠ Expected success={expect_success}"
                  + (f", error={expect_error}" if expect_error else ""))

    # ── Step 1: identify_user — valid phone ──
    r1 = identify_user("9876543210", name="Priya Menon")
    report(1, "identify_user — valid phone & name", r1, expect_success=True)

    # ── Step 2: identify_user — invalid phone ──
    r2 = identify_user("123")
    report(2, "identify_user — invalid phone '123'", r2,
           expect_success=False, expect_error="invalid_phone")

    # ── Step 3: fetch_slots — no preferred date ──
    r3 = fetch_slots()
    report(3, "fetch_slots — no date filter", r3, expect_success=True)

    # ── Step 4: book_appointment — first available slot ──
    first_group = r3["data"][0]
    first_date = first_group["date"]
    first_time = first_group["times"][0]
    r4 = book_appointment("9876543210", "Priya Menon", first_date, first_time)
    report(4, f"book_appointment — {first_date} {first_time}", r4,
           expect_success=True)

    # ── Step 5: book same slot again — expect slot_unavailable ──
    r5 = book_appointment("1111111111", "Other User", first_date, first_time)
    report(5, "book_appointment — same slot again", r5,
           expect_success=False, expect_error="slot_unavailable")

    # ── Step 6: retrieve_appointments — expect 1 ──
    r6 = retrieve_appointments("9876543210")
    report(6, "retrieve_appointments — 9876543210", r6, expect_success=True)
    count = len(r6.get("data", []))
    if count != 1:
        print(f"  ⚠ Expected 1 appointment, got {count}")
        failed += 1
        passed -= 1

    # ── Step 7: modify_appointment — move to next slot ──
    appt_id = r6["data"][0]["id"]
    # Pick next available slot (different from the booked one)
    next_slots = fetch_slots()
    next_group = next_slots["data"][0]
    new_date = next_group["date"]
    new_time = next_group["times"][0]
    # Make sure it's actually a different slot
    if new_date == first_date and new_time == first_time:
        new_time = next_group["times"][1] if len(next_group["times"]) > 1 else next_group["times"][0]
    r7 = modify_appointment(appt_id, "9876543210", new_date, new_time)
    report(7, f"modify_appointment — to {new_date} {new_time}", r7,
           expect_success=True)

    # ── Step 8: cancel_appointment ──
    r8 = cancel_appointment(appt_id, "9876543210")
    report(8, "cancel_appointment", r8, expect_success=True)

    # ── Step 9: end_conversation ──
    mock_history = [
        {"role": "assistant", "content": "Hello! Thank you for calling Mykare Health."},
        {"role": "user", "content": "Hi, I'd like to book an appointment."},
        {"role": "assistant", "content": "Of course! Could I get your phone number?"},
        {"role": "user", "content": "It's 9876543210."},
        {"role": "assistant", "content": "Thank you! When would you like to come in?"},
        {"role": "user", "content": "Tomorrow morning at 10 works."},
    ]
    r9 = end_conversation("test-session-001", "9876543210", mock_history)
    report(9, "end_conversation — with mock history", r9, expect_success=True)

    # ── Step 10: Verify SYSTEM_PROMPT ──
    print(f"\n[10/10] SYSTEM_PROMPT length check")
    prompt_len = len(SYSTEM_PROMPT)
    ok = isinstance(SYSTEM_PROMPT, str) and prompt_len > 500
    if ok:
        passed += 1
        print(f"  ✅ PASS — SYSTEM_PROMPT is {prompt_len} characters")
    else:
        failed += 1
        print(f"  ❌ FAIL — SYSTEM_PROMPT length = {prompt_len} (need > 500)")

    # ── Final summary ──
    print("\n" + "=" * 60)
    if failed == 0:
        print(f"  🎉 All {passed}/10 validation steps passed!")
    else:
        print(f"  ⚠ {passed} passed, {failed} failed out of 10 steps.")
    print("=" * 60)
