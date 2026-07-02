"""
Availability service: computes free appointment slots from clinic business hours.

compute_slots() is a pure function (no DB, no async) so it can be unit-tested
without infrastructure.  AvailabilityService.get_available_slots() wraps it with
a DB query to fetch existing appointments.

Contract of verificar_disponibilidad:
  Accepts: date_str in "YYYY-MM-DD" format (local date in the clinic's timezone).
  The conversational layer is responsible for resolving natural-language dates to
  this format before calling the tool.

Future improvement (post-MVP):
  get_next_available_slots(from_date, n_days) — iterates forward to find the
  first day with open slots, for users who have no specific date preference.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.appointment import SLOT_FREEING_STATUSES, Appointment
from app.models.clinic import Clinic

logger = logging.getLogger(__name__)

_DEFAULT_SLOT_MINUTES = 30


def get_slot_duration_minutes(clinic: Clinic) -> int:
    """Slot granularity for *clinic*, in minutes.

    Single source of truth for slot duration — reused by get_available_slots
    below and by agendar_cita's grid validation, so neither hardcodes 30.
    """
    return (clinic.business_hours or {}).get("slot_duration_minutes", _DEFAULT_SLOT_MINUTES)


def resolve_clinic_timezone(clinic: Clinic) -> ZoneInfo:
    """Resolve a clinic's IANA timezone (stdlib zoneinfo, not pytz).

    Raises ValueError if clinic.timezone is missing/unknown so callers can
    map it to their own "fecha_invalida"-style response.
    """
    tz_str = clinic.timezone or "UTC"
    try:
        return ZoneInfo(tz_str)
    except (ZoneInfoNotFoundError, KeyError) as exc:
        raise ValueError(f"invalid timezone for clinic {clinic.id}: {tz_str}") from exc


@dataclass(frozen=True)
class BookedSlot:
    """A single occupied interval, always timezone-aware."""

    start: datetime
    duration_minutes: int

    @property
    def end(self) -> datetime:
        return self.start + timedelta(minutes=self.duration_minutes)


def compute_slots(
    business_hours: dict,
    timezone_str: str,
    date_str: str,
    booked: list[BookedSlot],
    *,
    reference_date: date | None = None,
) -> dict:
    """
    Pure function: returns free slots for a clinic on a given calendar day.

    Args:
        business_hours: clinic.business_hours JSONB — expected shape:
            {
              "slot_duration_minutes": 30,
              "days": {
                "monday": [{"from": "09:00", "to": "14:00"}, ...],
                ...
                "sunday": []
              }
            }
        timezone_str:   IANA timezone name, e.g. "America/Lima".
        date_str:       ISO date "YYYY-MM-DD" in the clinic's local calendar.
        booked:         pre-fetched booked intervals for that day.
        reference_date: override "today" for deterministic tests; defaults to
                        the current date in the clinic's timezone.

    Returns:
        {"disponible": True,  "slots": ["2026-07-06T09:00:00-05:00", ...]}
        {"disponible": False, "motivo": "cerrado" | "fecha_pasada"
                                       | "fecha_invalida" | "sin_huecos"}
    """
    # --- Validate date string -------------------------------------------------
    try:
        requested_date = date.fromisoformat(date_str)
    except (ValueError, TypeError):
        return {"disponible": False, "motivo": "fecha_invalida"}

    # --- Validate timezone ---------------------------------------------------
    try:
        tz = ZoneInfo(timezone_str)
    except (ZoneInfoNotFoundError, KeyError):
        logger.error("unknown timezone: %s", timezone_str)
        return {"disponible": False, "motivo": "fecha_invalida"}

    # --- Reject past dates ---------------------------------------------------
    today = reference_date if reference_date is not None else datetime.now(tz=tz).date()
    if requested_date < today:
        return {"disponible": False, "motivo": "fecha_pasada"}

    # --- Resolve weekday and look up ranges ----------------------------------
    weekday = requested_date.strftime("%A").lower()  # "monday" … "sunday"
    day_ranges: list[dict] = business_hours.get("days", {}).get(weekday, [])
    if not day_ranges:
        return {"disponible": False, "motivo": "cerrado"}

    slot_td = timedelta(
        minutes=business_hours.get("slot_duration_minutes", _DEFAULT_SLOT_MINUTES)
    )

    # --- Build candidate slot grid -------------------------------------------
    # Each range contributes slots at slot_td intervals: start, start+td, …
    # A slot is included iff slot_start + slot_td <= range_end (full slot fits).
    candidates: list[datetime] = []
    for rng in day_ranges:
        from_h, from_m = map(int, rng["from"].split(":"))
        to_h, to_m = map(int, rng["to"].split(":"))
        slot = datetime(
            requested_date.year, requested_date.month, requested_date.day,
            from_h, from_m, tzinfo=tz,
        )
        range_end = datetime(
            requested_date.year, requested_date.month, requested_date.day,
            to_h, to_m, tzinfo=tz,
        )
        while slot + slot_td <= range_end:
            candidates.append(slot)
            slot += slot_td

    # --- Remove slots that overlap any booked interval -----------------------
    # Overlap: booked.start < slot_end  AND  booked.end > slot_start
    free = [
        s for s in candidates
        if not any(b.start < s + slot_td and b.end > s for b in booked)
    ]

    if not free:
        return {"disponible": False, "motivo": "sin_huecos"}

    return {"disponible": True, "slots": [s.isoformat() for s in free]}


class AvailabilityService:
    """Wraps compute_slots with async DB access to fetch existing appointments."""

    @staticmethod
    async def get_available_slots(
        db: AsyncSession,
        clinic: Clinic,
        date_str: str,
    ) -> dict:
        """
        Returns available slots for *clinic* on *date_str* ("YYYY-MM-DD").

        Early-outs (invalid date, past date, closed day) skip the DB query.
        For open days, fetches non-cancelled/no-show appointments and their
        treatment durations, then delegates to compute_slots.
        """
        bh: dict = clinic.business_hours or {}
        tz_str: str = clinic.timezone or "UTC"

        # Early validation — avoid DB round-trip for obvious errors
        try:
            requested_date = date.fromisoformat(date_str)
        except (ValueError, TypeError):
            return {"disponible": False, "motivo": "fecha_invalida"}

        try:
            tz = resolve_clinic_timezone(clinic)
        except ValueError:
            return {"disponible": False, "motivo": "fecha_invalida"}

        today = datetime.now(tz=tz).date()
        if requested_date < today:
            return {"disponible": False, "motivo": "fecha_pasada"}

        weekday = requested_date.strftime("%A").lower()
        if not bh.get("days", {}).get(weekday):
            return {"disponible": False, "motivo": "cerrado"}

        # --- Fetch appointments that fall on this calendar day ---------------
        day_start = datetime(
            requested_date.year, requested_date.month, requested_date.day,
            tzinfo=tz,
        )
        day_end = day_start + timedelta(days=1)

        stmt = (
            select(Appointment)
            .options(selectinload(Appointment.treatment))
            .where(Appointment.tenant_id == clinic.id)
            .where(Appointment.status.not_in(SLOT_FREEING_STATUSES))
            .where(Appointment.scheduled_at >= day_start)
            .where(Appointment.scheduled_at < day_end)
        )
        rows = (await db.execute(stmt)).scalars().all()

        slot_minutes = get_slot_duration_minutes(clinic)
        booked = [
            BookedSlot(
                start=row.scheduled_at.astimezone(tz),
                duration_minutes=(
                    row.treatment.duration_minutes
                    if row.treatment and row.treatment.duration_minutes
                    else slot_minutes
                ),
            )
            for row in rows
        ]

        return compute_slots(bh, tz_str, date_str, booked, reference_date=today)
