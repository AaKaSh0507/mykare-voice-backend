import json
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from db import (
    DB_PATH,
    book_appointment,
    cancel_appointment,
    db_health,
    fetch_available_slots,
    get_appointments,
    init_db,
    is_slot_available,
)


def test_db_validation_suite():
    print("=" * 60)
    print("  MyKare DB — Validation Suite")
    print("=" * 60)

    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)

    print("\n[1/9] Initializing database...")
    init_db()
    print("     ✅ init_db() completed.")

    print("\n[2/9] Running health check...")
    health = db_health()
    print(f"     {json.dumps(health, indent=6)}")
    assert health["status"] == "ok", "Health check failed!"
    print("     ✅ Health check passed.")

    print("\n[3/9] Fetching available slots (first 3)...")
    slots = fetch_available_slots(limit=3)
    for slot in slots:
        print(f"     📅 {slot['slot_date']}  🕐 {slot['slot_time']}")
    assert len(slots) == 3, f"Expected 3 slots, got {len(slots)}"
    print("     ✅ Slots fetched.")

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

    print("\n[5/9] Attempting to double-book the same slot...")
    try:
        book_appointment(
            phone="1111111111",
            name="Other User",
            slot_date=first_slot["slot_date"],
            slot_time=first_slot["slot_time"],
        )
        raise AssertionError("Should have raised ValueError!")
    except ValueError as exc:
        print(f"     Caught expected error: {exc}")
    print("     ✅ Double-booking correctly prevented.")

    print("\n[6/9] Fetching appointments for 9876543210...")
    appts = get_appointments("9876543210")
    print(f"     Found {len(appts)} appointment(s).")
    assert len(appts) == 1, f"Expected 1 appointment, got {len(appts)}"
    print("     ✅ Appointment list correct.")

    print(f"\n[7/9] Cancelling appointment {appt['id']}...")
    cancelled = cancel_appointment(appt["id"], "9876543210")
    print(f"     Status: {cancelled['status']}")
    assert cancelled["status"] == "cancelled"
    print("     ✅ Appointment cancelled.")

    print(f"\n[8/9] Checking if slot {first_slot['slot_date']} {first_slot['slot_time']} is available again...")
    available = is_slot_available(first_slot["slot_date"], first_slot["slot_time"])
    print(f"     Available: {available}")
    assert available is True
    print("     ✅ Slot correctly freed.")

    print("\n[9/9] Final health check...")
    final_health = db_health()
    print(f"     {json.dumps(final_health, indent=6)}")
    assert final_health["status"] == "ok"
    print("     ✅ Final health check passed.")

    print("\n" + "=" * 60)
    print("  🎉 All 9 validation steps passed!")
    print("=" * 60)


if __name__ == "__main__":
    test_db_validation_suite()
