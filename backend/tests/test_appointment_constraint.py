"""
Tests for the double-booking guard: uq_appointments_tenant_slot.

Partial unique index on (tenant_id, scheduled_at) that excludes rows whose
status is in SLOT_FREEING_STATUSES ("cancelled", "no_show") — mirrors the
"is this appointment occupying the slot" definition used by
app.services.availability, so the DB constraint and the availability query
never disagree about slot occupancy.
"""
from datetime import datetime, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from app.models.appointment import SLOT_FREEING_STATUSES, Appointment
from app.models.clinic import Clinic
from app.models.lead import Lead

SCHEDULED_AT = datetime(2026, 7, 6, 10, 0, tzinfo=timezone.utc)


async def _create_clinic(db_session, name: str, phone_id: str) -> Clinic:
    clinic = Clinic(
        name=name,
        whatsapp_phone_id=phone_id,
        timezone="UTC",
        business_hours={},
        config={},
    )
    db_session.add(clinic)
    await db_session.flush()
    return clinic


async def _create_lead(db_session, tenant_id, number: str) -> Lead:
    lead = Lead(tenant_id=tenant_id, whatsapp_number=number, status="new")
    db_session.add(lead)
    await db_session.flush()
    return lead


async def test_rejects_double_booking_same_tenant_and_slot(db_session):
    """Two active appointments for the same tenant at the same instant collide."""
    clinic = await _create_clinic(db_session, "Clinica A", "wa_a")
    lead1 = await _create_lead(db_session, clinic.id, "+1111111111")
    lead2 = await _create_lead(db_session, clinic.id, "+2222222222")

    db_session.add(
        Appointment(tenant_id=clinic.id, lead_id=lead1.id, scheduled_at=SCHEDULED_AT, status="confirmed")
    )
    await db_session.flush()

    db_session.add(
        Appointment(tenant_id=clinic.id, lead_id=lead2.id, scheduled_at=SCHEDULED_AT, status="confirmed")
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_allows_same_slot_for_different_tenant(db_session):
    """The unique index is scoped per tenant_id — another clinic can use the same instant."""
    clinic_a = await _create_clinic(db_session, "Clinica A", "wa_a")
    clinic_b = await _create_clinic(db_session, "Clinica B", "wa_b")
    lead_a = await _create_lead(db_session, clinic_a.id, "+1111111111")
    lead_b = await _create_lead(db_session, clinic_b.id, "+2222222222")

    db_session.add(
        Appointment(tenant_id=clinic_a.id, lead_id=lead_a.id, scheduled_at=SCHEDULED_AT, status="confirmed")
    )
    db_session.add(
        Appointment(tenant_id=clinic_b.id, lead_id=lead_b.id, scheduled_at=SCHEDULED_AT, status="confirmed")
    )

    await db_session.flush()  # must not raise


@pytest.mark.parametrize("freeing_status", SLOT_FREEING_STATUSES)
async def test_slot_is_freed_when_status_is_slot_freeing(db_session, freeing_status):
    """Once the existing appointment moves to a slot-freeing status, the slot can be rebooked."""
    clinic = await _create_clinic(db_session, "Clinica A", "wa_a")
    lead1 = await _create_lead(db_session, clinic.id, "+1111111111")
    lead2 = await _create_lead(db_session, clinic.id, "+2222222222")

    db_session.add(
        Appointment(tenant_id=clinic.id, lead_id=lead1.id, scheduled_at=SCHEDULED_AT, status=freeing_status)
    )
    await db_session.flush()

    db_session.add(
        Appointment(tenant_id=clinic.id, lead_id=lead2.id, scheduled_at=SCHEDULED_AT, status="confirmed")
    )
    await db_session.flush()  # must not raise
