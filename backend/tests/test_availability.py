"""
Unit tests for AvailabilityService.compute_slots.

All tests use the pure compute_slots() function — no DB, no async.
Fixture dates are fixed via the reference_date parameter so the suite
never becomes flaky as calendar dates roll forward.

Timezone coverage:
  - America/Lima  (UTC-5, no DST)  — primary market
  - Europe/Madrid (UTC+2 in summer CEST) — secondary market

Key dates used (all in July 2026 to keep them clearly "future"):
  - 2026-07-06 (Monday)  — two-range workday tests
  - 2026-07-05 (Sunday)  — closed-day test
  - 2026-07-15 (Wednesday) — timezone comparison tests
"""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.services.availability import BookedSlot, compute_slots

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

REFERENCE_DATE = date(2026, 7, 1)  # "today" for all tests — all July dates are future

TZ_LIMA = ZoneInfo("America/Lima")    # UTC-5, no DST
TZ_MADRID = ZoneInfo("Europe/Madrid") # UTC+2 in July (CEST)

# Standard two-range business hours (morning + lunch break + afternoon)
BH_STANDARD = {
    "slot_duration_minutes": 30,
    "days": {
        "monday":    [{"from": "09:00", "to": "14:00"}, {"from": "16:00", "to": "20:00"}],
        "tuesday":   [{"from": "09:00", "to": "14:00"}, {"from": "16:00", "to": "20:00"}],
        "wednesday": [{"from": "09:00", "to": "14:00"}, {"from": "16:00", "to": "20:00"}],
        "thursday":  [{"from": "09:00", "to": "14:00"}, {"from": "16:00", "to": "20:00"}],
        "friday":    [{"from": "09:00", "to": "14:00"}, {"from": "16:00", "to": "19:00"}],
        "saturday":  [{"from": "09:00", "to": "13:00"}],
        "sunday":    [],
    },
}


def _lima_dt(h: int, m: int, day: int = 6) -> datetime:
    """Timezone-aware datetime in America/Lima for 2026-07-{day}."""
    return datetime(2026, 7, day, h, m, tzinfo=TZ_LIMA)


def _madrid_dt(h: int, m: int, day: int = 15) -> datetime:
    """Timezone-aware datetime in Europe/Madrid for 2026-07-{day}."""
    return datetime(2026, 7, day, h, m, tzinfo=TZ_MADRID)


# ---------------------------------------------------------------------------
# 1. Workday with two ranges — Lima
# ---------------------------------------------------------------------------

def test_two_range_workday_lima_slot_count():
    """Monday with 09:00-14:00 + 16:00-20:00 at 30-min slots → 18 free slots."""
    result = compute_slots(BH_STANDARD, "America/Lima", "2026-07-06", [], reference_date=REFERENCE_DATE)

    assert result["disponible"] is True
    slots = result["slots"]
    # Morning: 09:00…13:30 = 10 slots; afternoon: 16:00…19:30 = 8 slots
    assert len(slots) == 18


def test_two_range_workday_lima_first_and_last_slot():
    """First slot is 09:00-05:00 and last is 19:30-05:00 (Lima UTC-5 offset)."""
    result = compute_slots(BH_STANDARD, "America/Lima", "2026-07-06", [], reference_date=REFERENCE_DATE)

    slots = result["slots"]
    assert slots[0] == "2026-07-06T09:00:00-05:00"
    assert slots[-1] == "2026-07-06T19:30:00-05:00"


def test_two_range_workday_lima_lunch_gap():
    """No slots exist between 14:00 and 16:00 (lunch break)."""
    result = compute_slots(BH_STANDARD, "America/Lima", "2026-07-06", [], reference_date=REFERENCE_DATE)

    slots = result["slots"]
    # 14:00 and 14:30 and 15:00 and 15:30 must not appear
    for blocked_hour in ("14:00", "14:30", "15:00", "15:30"):
        assert not any(blocked_hour in s for s in slots), f"{blocked_hour} should not be a slot"


# ---------------------------------------------------------------------------
# 2. Closed day (Sunday with empty range)
# ---------------------------------------------------------------------------

def test_closed_day_returns_cerrado():
    """Sunday [] → disponible=False, motivo='cerrado'."""
    result = compute_slots(BH_STANDARD, "America/Lima", "2026-07-05", [], reference_date=REFERENCE_DATE)

    assert result == {"disponible": False, "motivo": "cerrado"}


def test_missing_day_key_treated_as_closed():
    """A day key absent from 'days' is also treated as closed."""
    bh_sparse = {
        "slot_duration_minutes": 30,
        "days": {
            "monday": [{"from": "09:00", "to": "13:00"}],
            # tuesday … sunday absent
        },
    }
    result = compute_slots(bh_sparse, "America/Lima", "2026-07-07", [], reference_date=REFERENCE_DATE)
    # 2026-07-07 is a Tuesday
    assert result == {"disponible": False, "motivo": "cerrado"}


# ---------------------------------------------------------------------------
# 3. Single slot blocked by a normal appointment
# ---------------------------------------------------------------------------

def test_single_appointment_blocks_one_slot():
    """Appointment at 10:00 (30 min) removes that slot; others remain free."""
    booked = [BookedSlot(start=_lima_dt(10, 0), duration_minutes=30)]
    result = compute_slots(BH_STANDARD, "America/Lima", "2026-07-06", booked, reference_date=REFERENCE_DATE)

    assert result["disponible"] is True
    slots = result["slots"]
    assert "2026-07-06T10:00:00-05:00" not in slots
    # Neighbouring slots must be free
    assert "2026-07-06T09:30:00-05:00" in slots
    assert "2026-07-06T10:30:00-05:00" in slots


def test_all_slots_blocked_returns_sin_huecos():
    """Booking every slot on Saturday returns sin_huecos."""
    # Saturday: 09:00-13:00 → 8 slots (09:00, 09:30, …, 12:30)
    booked = [
        BookedSlot(start=_lima_dt(h, m, day=11), duration_minutes=30)
        for h, m in [(9, 0), (9, 30), (10, 0), (10, 30), (11, 0), (11, 30), (12, 0), (12, 30)]
    ]
    result = compute_slots(BH_STANDARD, "America/Lima", "2026-07-11", booked, reference_date=REFERENCE_DATE)
    # 2026-07-11 is a Saturday
    assert result == {"disponible": False, "motivo": "sin_huecos"}


# ---------------------------------------------------------------------------
# 4. Long treatment blocks 2+ slots (overlap arithmetic)
# ---------------------------------------------------------------------------

def test_60min_treatment_blocks_two_consecutive_slots():
    """A 60-minute appointment at 10:00 must block both 10:00 and 10:30."""
    booked = [BookedSlot(start=_lima_dt(10, 0), duration_minutes=60)]
    result = compute_slots(BH_STANDARD, "America/Lima", "2026-07-06", booked, reference_date=REFERENCE_DATE)

    slots = result["slots"]
    assert "2026-07-06T10:00:00-05:00" not in slots
    assert "2026-07-06T10:30:00-05:00" not in slots
    # Slots immediately before and after must remain free
    assert "2026-07-06T09:30:00-05:00" in slots
    assert "2026-07-06T11:00:00-05:00" in slots


def test_90min_treatment_blocks_three_consecutive_slots():
    """A 90-minute appointment at 09:00 blocks 09:00, 09:30, and 10:00."""
    booked = [BookedSlot(start=_lima_dt(9, 0), duration_minutes=90)]
    result = compute_slots(BH_STANDARD, "America/Lima", "2026-07-06", booked, reference_date=REFERENCE_DATE)

    slots = result["slots"]
    for blocked in ("09:00", "09:30", "10:00"):
        assert f"2026-07-06T{blocked}:00-05:00" not in slots
    assert "2026-07-06T10:30:00-05:00" in slots


def test_long_treatment_spanning_range_boundary():
    """60-min appointment at 13:30 blocks 13:30 AND the 14:00 slot is already outside range.
    The key assertion: no slot in the 14:00-16:00 gap appears, and 13:30 is removed."""
    booked = [BookedSlot(start=_lima_dt(13, 30), duration_minutes=60)]
    result = compute_slots(BH_STANDARD, "America/Lima", "2026-07-06", booked, reference_date=REFERENCE_DATE)

    slots = result["slots"]
    assert "2026-07-06T13:30:00-05:00" not in slots
    # The 14:xx slots never existed (outside business hours), so no bleed-over
    assert not any("T14:" in s or "T15:" in s for s in slots)
    # Afternoon range still unaffected
    assert "2026-07-06T16:00:00-05:00" in slots


# ---------------------------------------------------------------------------
# 5. Timezone comparison — Lima vs Madrid, same business-hours schema
# ---------------------------------------------------------------------------

def test_lima_slot_carries_correct_utc_offset():
    """Lima (UTC-5) slots must carry -05:00 offset in isoformat."""
    result = compute_slots(BH_STANDARD, "America/Lima", "2026-07-15", [], reference_date=REFERENCE_DATE)

    assert result["disponible"] is True
    assert all("-05:00" in s for s in result["slots"])


def test_madrid_slot_carries_correct_utc_offset():
    """Madrid in July (CEST, UTC+2) slots must carry +02:00 offset."""
    result = compute_slots(BH_STANDARD, "Europe/Madrid", "2026-07-15", [], reference_date=REFERENCE_DATE)

    assert result["disponible"] is True
    assert all("+02:00" in s for s in result["slots"])


def test_lima_and_madrid_same_local_time_differ_in_utc():
    """The 09:00 slot on 2026-07-15 represents different UTC instants in each tz."""
    lima_result = compute_slots(BH_STANDARD, "America/Lima", "2026-07-15", [], reference_date=REFERENCE_DATE)
    madrid_result = compute_slots(BH_STANDARD, "Europe/Madrid", "2026-07-15", [], reference_date=REFERENCE_DATE)

    lima_first = datetime.fromisoformat(lima_result["slots"][0])
    madrid_first = datetime.fromisoformat(madrid_result["slots"][0])

    # Both are 09:00 local but 7 hours apart in UTC (Lima UTC-5, Madrid CEST UTC+2)
    assert lima_first.hour == 9
    assert madrid_first.hour == 9
    diff = abs((lima_first.utcoffset() or timedelta()) - (madrid_first.utcoffset() or timedelta()))
    assert diff == timedelta(hours=7)


def test_madrid_appointment_blocks_correct_local_slot():
    """An appointment at 10:00 Madrid time must block the 10:00 slot (not bleed into Lima time)."""
    booked = [BookedSlot(start=_madrid_dt(10, 0), duration_minutes=30)]
    result = compute_slots(BH_STANDARD, "Europe/Madrid", "2026-07-15", booked, reference_date=REFERENCE_DATE)

    slots = result["slots"]
    assert "2026-07-15T10:00:00+02:00" not in slots
    assert "2026-07-15T09:30:00+02:00" in slots
    assert "2026-07-15T10:30:00+02:00" in slots


# ---------------------------------------------------------------------------
# 6. Past date and invalid date format
# ---------------------------------------------------------------------------

def test_past_date_returns_fecha_pasada():
    """A date before reference_date returns fecha_pasada."""
    result = compute_slots(
        BH_STANDARD, "America/Lima", "2026-06-30", [],
        reference_date=date(2026, 7, 1),  # reference is July 1; June 30 is past
    )
    assert result == {"disponible": False, "motivo": "fecha_pasada"}


def test_today_is_not_past():
    """The reference date itself (today) is valid and should not return fecha_pasada."""
    # 2026-07-07 is a Tuesday — open day
    result = compute_slots(
        BH_STANDARD, "America/Lima", "2026-07-07", [],
        reference_date=date(2026, 7, 7),  # today == requested date
    )
    assert result["disponible"] is True


def test_invalid_date_format_returns_fecha_invalida():
    """Non-ISO date strings return fecha_invalida."""
    for bad in ("not-a-date", "07/06/2026", "2026/07/06", "", "2026-13-01"):
        result = compute_slots(BH_STANDARD, "America/Lima", bad, [], reference_date=REFERENCE_DATE)
        assert result == {"disponible": False, "motivo": "fecha_invalida"}, f"failed for: {bad!r}"


def test_invalid_timezone_returns_fecha_invalida():
    """An unknown timezone string returns fecha_invalida."""
    result = compute_slots(BH_STANDARD, "Not/A/Timezone", "2026-07-06", [], reference_date=REFERENCE_DATE)
    assert result == {"disponible": False, "motivo": "fecha_invalida"}
