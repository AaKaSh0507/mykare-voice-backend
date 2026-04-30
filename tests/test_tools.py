import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from db import init_db
from prompts import SYSTEM_PROMPT
from tools import (
    book_appointment,
    cancel_appointment,
    end_conversation,
    fetch_slots,
    identify_user,
    modify_appointment,
    retrieve_appointments,
)


def test_tools_validation_suite():
    print("=" * 60)
    print("  MyKare Tools — Validation Suite")
    print("=" * 60)

    db_path = os.getenv("DB_PATH", "mykare.db")
    if os.path.exists(db_path):
        os.remove(db_path)
    init_db()

    passed = 0
    failed = 0

    def report(step, label, result, expect_success, expect_error=None):
        nonlocal passed, failed
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
        print(f"  success={result.get('success')}, error={result.get('error', '—')}")
        print(f"  message: {result.get('message', '—')}")
        if not ok:
            print(
                f"  ⚠ Expected success={expect_success}"
                + (f", error={expect_error}" if expect_error else "")
            )

    r1 = identify_user("9876543210", name="Priya Menon")
    report(1, "identify_user — valid phone & name", r1, expect_success=True)

    r2 = identify_user("123")
    report(
        2,
        "identify_user — invalid phone '123'",
        r2,
        expect_success=False,
        expect_error="invalid_phone",
    )

    r3 = fetch_slots()
    report(3, "fetch_slots — no date filter", r3, expect_success=True)

    first_group = r3["data"][0]
    first_date = first_group["date"]
    first_time = first_group["times"][0]
    r4 = book_appointment("9876543210", "Priya Menon", first_date, first_time)
    report(4, f"book_appointment — {first_date} {first_time}", r4, expect_success=True)

    r5 = book_appointment("1111111111", "Other User", first_date, first_time)
    report(
        5,
        "book_appointment — same slot again",
        r5,
        expect_success=False,
        expect_error="slot_unavailable",
    )

    r6 = retrieve_appointments("9876543210")
    report(6, "retrieve_appointments — 9876543210", r6, expect_success=True)
    count = len(r6.get("data", []))
    if count != 1:
        print(f"  ⚠ Expected 1 appointment, got {count}")
        failed += 1
        passed -= 1

    appt_id = r6["data"][0]["id"]
    next_slots = fetch_slots()
    next_group = next_slots["data"][0]
    new_date = next_group["date"]
    new_time = next_group["times"][0]
    if new_date == first_date and new_time == first_time:
        new_time = (
            next_group["times"][1]
            if len(next_group["times"]) > 1
            else next_group["times"][0]
        )
    r7 = modify_appointment(appt_id, "9876543210", new_date, new_time)
    report(7, f"modify_appointment — to {new_date} {new_time}", r7, expect_success=True)

    r8 = cancel_appointment(appt_id, "9876543210")
    report(8, "cancel_appointment", r8, expect_success=True)

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

    print("\n[10/10] SYSTEM_PROMPT length check")
    prompt_len = len(SYSTEM_PROMPT)
    ok = isinstance(SYSTEM_PROMPT, str) and prompt_len > 500
    if ok:
        passed += 1
        print(f"  ✅ PASS — SYSTEM_PROMPT is {prompt_len} characters")
    else:
        failed += 1
        print(f"  ❌ FAIL — SYSTEM_PROMPT length = {prompt_len} (need > 500)")

    print("\n" + "=" * 60)
    if failed == 0:
        print(f"  🎉 All {passed}/10 validation steps passed!")
    else:
        print(f"  ⚠ {passed} passed, {failed} failed out of 10 steps.")
    print("=" * 60)

    assert failed == 0, f"{failed} validation step(s) failed."


if __name__ == "__main__":
    test_tools_validation_suite()
