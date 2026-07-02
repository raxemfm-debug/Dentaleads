"""
Idempotent dev seed: one test clinic + 3 treatments.

Re-runnable — looks up existing rows by their natural key (whatsapp_phone_id
for the clinic, (tenant_id, name) for treatments) and leaves them untouched
instead of inserting duplicates.

Usage (from backend/, with DATABASE_URL/DATABASE_URL_SYNC pointed at a
reachable Postgres — e.g. localhost:5432 from the host, `db` from inside the
api container):
    python -m scripts.seed_dev
"""
import asyncio
import logging
from decimal import Decimal

from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.clinic import Clinic
from app.models.treatment import Treatment

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("seed_dev")

CLINIC_WHATSAPP_PHONE_ID = "1162389980294305"
CLINIC_NAME = "Clínica Dental Demo"
CLINIC_TIMEZONE = "America/Lima"

BUSINESS_HOURS = {
    "slot_duration_minutes": 30,
    "days": {
        "monday":    [{"from": "09:00", "to": "13:00"}, {"from": "15:00", "to": "19:00"}],
        "tuesday":   [{"from": "09:00", "to": "13:00"}, {"from": "15:00", "to": "19:00"}],
        "wednesday": [{"from": "09:00", "to": "13:00"}, {"from": "15:00", "to": "19:00"}],
        "thursday":  [{"from": "09:00", "to": "13:00"}, {"from": "15:00", "to": "19:00"}],
        "friday":    [{"from": "09:00", "to": "13:00"}, {"from": "15:00", "to": "19:00"}],
        "saturday":  [{"from": "09:00", "to": "13:00"}],
        "sunday":    [],
    },
}

TREATMENTS = [
    {
        "name": "Limpieza Dental",
        "description": "Profilaxis dental completa con ultrasonido.",
        "duration_minutes": 45,
        "price_from": Decimal("80.00"),
        "requires_consult": False,
    },
    {
        "name": "Ortodoncia",
        "description": "Tratamiento de ortodoncia con brackets o alineadores.",
        "duration_minutes": 60,
        "price_from": Decimal("2500.00"),
        "requires_consult": True,
    },
    {
        "name": "Blanqueamiento",
        "description": "Blanqueamiento dental profesional en consultorio.",
        "duration_minutes": 60,
        "price_from": Decimal("350.00"),
        "requires_consult": False,
    },
]


async def _get_or_create_clinic(db) -> tuple[Clinic, bool]:
    clinic = (
        await db.execute(
            select(Clinic).where(Clinic.whatsapp_phone_id == CLINIC_WHATSAPP_PHONE_ID)
        )
    ).scalars().first()
    if clinic is not None:
        return clinic, False

    clinic = Clinic(
        name=CLINIC_NAME,
        whatsapp_phone_id=CLINIC_WHATSAPP_PHONE_ID,
        timezone=CLINIC_TIMEZONE,
        business_hours=BUSINESS_HOURS,
        config={},
    )
    db.add(clinic)
    await db.flush()
    return clinic, True


async def _get_or_create_treatment(db, clinic: Clinic, spec: dict) -> tuple[Treatment, bool]:
    treatment = (
        await db.execute(
            select(Treatment).where(
                Treatment.tenant_id == clinic.id,
                Treatment.name == spec["name"],
            )
        )
    ).scalars().first()
    if treatment is not None:
        return treatment, False

    treatment = Treatment(tenant_id=clinic.id, **spec)
    db.add(treatment)
    await db.flush()
    return treatment, True


async def seed() -> Clinic:
    async with AsyncSessionLocal() as db:
        clinic, created = await _get_or_create_clinic(db)
        logger.info(
            "%s clínica '%s' (id=%s, whatsapp_phone_id=%s)",
            "creada" if created else "ya existía",
            clinic.name, clinic.id, clinic.whatsapp_phone_id,
        )

        for spec in TREATMENTS:
            treatment, created = await _get_or_create_treatment(db, clinic, spec)
            logger.info(
                "  %s tratamiento '%s' (price_from=%s)",
                "creado" if created else "ya existía",
                treatment.name, treatment.price_from,
            )

        await db.commit()
        await db.refresh(clinic)
        return clinic


if __name__ == "__main__":
    asyncio.run(seed())
