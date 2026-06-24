"""
Multi-tenant isolation tests.

These verify that queries scoped to tenant_id never leak data across clinics.
Phase 0: skeleton that establishes the pattern. Full coverage grows in Phases 1–2
as the service layer is built.
"""
import uuid

import pytest
from sqlalchemy import select

from app.models.clinic import Clinic
from app.models.lead import Lead


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


async def _create_lead(db_session, tenant_id: uuid.UUID, number: str) -> Lead:
    lead = Lead(tenant_id=tenant_id, whatsapp_number=number, status="new")
    db_session.add(lead)
    await db_session.flush()
    return lead


async def test_leads_are_isolated_by_tenant(db_session):
    clinic_a = await _create_clinic(db_session, "Clinica A", "wa_a")
    clinic_b = await _create_clinic(db_session, "Clinica B", "wa_b")

    await _create_lead(db_session, clinic_a.id, "+1111111111")
    await _create_lead(db_session, clinic_b.id, "+2222222222")

    # Querying for clinic_a's leads must not return clinic_b's leads
    result = await db_session.execute(
        select(Lead).where(Lead.tenant_id == clinic_a.id)
    )
    leads_a = result.scalars().all()

    assert len(leads_a) == 1
    assert all(lead.tenant_id == clinic_a.id for lead in leads_a)
    assert all(lead.whatsapp_number != "+2222222222" for lead in leads_a)


async def test_tenant_id_is_required_on_lead(db_session):
    """A lead without tenant_id must fail at the DB level."""
    from sqlalchemy.exc import IntegrityError

    lead = Lead(whatsapp_number="+9999999999", status="new")
    db_session.add(lead)
    with pytest.raises(IntegrityError):
        await db_session.flush()
