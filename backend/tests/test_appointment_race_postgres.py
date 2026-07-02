"""
Real-Postgres concurrency test for uq_appointments_tenant_slot.

test_appointment_constraint.py proves the constraint's *shape* against SQLite
using a single session (sequential flush/flush) — that's enough to verify the
index excludes SLOT_FREEING_STATUSES, but it never exercises a genuine race:
two independent connections racing to insert the same (tenant_id,
scheduled_at) slot at the same instant. SQLite has no real concurrent-writer
story, so that race is only observable against a real Postgres server.

Requires TEST_POSTGRES_URL (async DSN, e.g.
postgresql+asyncpg://dentalbot:dentalbot@localhost:5432/dentalbot_test)
pointing at a database with migrations already applied (`alembic upgrade
head`). Skipped entirely when unset, so the regular suite (SQLite, no real
Postgres needed) is unaffected.

Point TEST_POSTGRES_URL at a dedicated test database, not the dev one — this
test also cleans up every row it creates (appointment, leads, clinic) in a
`finally` block regardless, so it never leaves residue behind either way.
"""
import asyncio
import os
import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.appointment import Appointment
from app.models.clinic import Clinic
from app.models.lead import Lead

TEST_POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL")

pytestmark = pytest.mark.skipif(
    not TEST_POSTGRES_URL,
    reason=(
        "set TEST_POSTGRES_URL (async DSN, migrations already applied) to run "
        "the real Postgres double-booking race test"
    ),
)

SCHEDULED_AT = datetime(2026, 8, 3, 10, 0, tzinfo=timezone.utc)
GATHER_TIMEOUT_SECONDS = 10  # a deadlock would hang past this instead of failing fast


@pytest_asyncio.fixture
async def pg_session_factory():
    engine = create_async_engine(TEST_POSTGRES_URL, pool_size=5)
    yield async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    await engine.dispose()


async def _insert_appointment(session_factory, tenant_id, lead_id) -> Exception | None:
    """Opens its own session (and thus its own DB connection), commits one
    appointment. Returns the IntegrityError instead of raising, so both
    racers can be awaited via gather() without one cancelling the other."""
    async with session_factory() as session:
        session.add(
            Appointment(
                tenant_id=tenant_id,
                lead_id=lead_id,
                scheduled_at=SCHEDULED_AT,
                status="confirmed",
            )
        )
        try:
            await session.commit()
            return None
        except IntegrityError as exc:
            await session.rollback()
            return exc


async def test_concurrent_inserts_same_slot_only_one_wins(pg_session_factory):
    async with pg_session_factory() as setup:
        clinic = Clinic(
            name="Race Test Clinic",
            whatsapp_phone_id=f"race-{uuid.uuid4()}",
            timezone="UTC",
            business_hours={},
            config={},
        )
        setup.add(clinic)
        await setup.flush()
        lead1 = Lead(tenant_id=clinic.id, whatsapp_number="+1000000001", status="new")
        lead2 = Lead(tenant_id=clinic.id, whatsapp_number="+1000000002", status="new")
        setup.add_all([lead1, lead2])
        await setup.flush()
        clinic_id, lead1_id, lead2_id = clinic.id, lead1.id, lead2.id
        await setup.commit()

    try:
        # Two independent sessions/connections racing the same slot — gather()
        # schedules both commits concurrently instead of sequentially.
        results = await asyncio.wait_for(
            asyncio.gather(
                _insert_appointment(pg_session_factory, clinic_id, lead1_id),
                _insert_appointment(pg_session_factory, clinic_id, lead2_id),
            ),
            timeout=GATHER_TIMEOUT_SECONDS,
        )

        successes = [r for r in results if r is None]
        failures = [r for r in results if r is not None]

        assert len(successes) == 1, f"expected exactly one winner, got {results}"
        assert len(failures) == 1
        assert isinstance(failures[0], IntegrityError)

        async with pg_session_factory() as check:
            rows = (
                await check.execute(
                    select(Appointment).where(
                        Appointment.tenant_id == clinic_id,
                        Appointment.scheduled_at == SCHEDULED_AT,
                    )
                )
            ).scalars().all()
            assert len(rows) == 1
    finally:
        async with pg_session_factory() as cleanup:
            await cleanup.execute(delete(Appointment).where(Appointment.tenant_id == clinic_id))
            await cleanup.execute(delete(Lead).where(Lead.tenant_id == clinic_id))
            await cleanup.execute(delete(Clinic).where(Clinic.id == clinic_id))
            await cleanup.commit()
