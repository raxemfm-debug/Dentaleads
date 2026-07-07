"""
DB-backed unit tests for the three simple tool handlers:
  guardar_lead, consultar_tratamiento, derivar_a_humano.

Each test uses a real SQLite in-memory session (via the db_session fixture from
conftest.py) so assertions can re-read committed rows and verify the exact state
of the DB after each handler call.

Handlers use flush() (not commit()) to stay inside the request transaction;
tests call db_session.refresh() to reload from the same flushed state.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
import pytest_asyncio

from app.models.appointment import Appointment
from app.models.clinic import Clinic
from app.models.conversation import Conversation
from app.models.lead import Lead
from app.models.treatment import Treatment
from app.services.tools import (
    ToolContext,
    _handle_agendar_cita,
    _handle_consultar_tratamiento,
    _handle_derivar_a_humano,
    _handle_guardar_lead,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def clinic(db_session):
    c = Clinic(
        name="Clínica Demo",
        whatsapp_phone_id=f"test-{uuid.uuid4()}",
        timezone="America/Lima",
        business_hours={},
        config={},
    )
    db_session.add(c)
    await db_session.flush()
    await db_session.refresh(c)
    return c


@pytest_asyncio.fixture
async def other_clinic(db_session):
    c = Clinic(
        name="Otra Clínica",
        whatsapp_phone_id=f"other-{uuid.uuid4()}",
        timezone="America/Lima",
        business_hours={},
        config={},
    )
    db_session.add(c)
    await db_session.flush()
    await db_session.refresh(c)
    return c


@pytest_asyncio.fixture
async def lead(db_session, clinic):
    l = Lead(tenant_id=clinic.id, whatsapp_number="+51999000001", source="whatsapp", status="new")
    db_session.add(l)
    await db_session.flush()
    await db_session.refresh(l)
    return l


@pytest_asyncio.fixture
async def conv(db_session, clinic, lead):
    c = Conversation(tenant_id=clinic.id, lead_id=lead.id, channel="whatsapp", status="bot")
    db_session.add(c)
    await db_session.flush()
    await db_session.refresh(c)
    return c


@pytest_asyncio.fixture
async def treatment(db_session, clinic):
    t = Treatment(
        tenant_id=clinic.id,
        name="Limpieza Dental",
        description="Profilaxis completa con ultrasonido.",
        duration_minutes=45,
        price_from=Decimal("80.00"),
        requires_consult=False,
        is_active=True,
    )
    db_session.add(t)
    await db_session.flush()
    await db_session.refresh(t)
    return t


def _ctx(db, clinic, conv, lead) -> ToolContext:
    return ToolContext(db=db, clinic=clinic, conv=conv, lead=lead)


# ---------------------------------------------------------------------------
# guardar_lead
# ---------------------------------------------------------------------------

class TestGuardarLead:
    async def test_saves_name(self, db_session, clinic, lead, conv):
        ctx = _ctx(db_session, clinic, conv, lead)
        result = json.loads(await _handle_guardar_lead(ctx, {"nombre": "Ana García"}))

        assert result["status"] == "saved"
        await db_session.refresh(lead)
        assert lead.name == "Ana García"

    async def test_sets_consent_at_when_name_provided(self, db_session, clinic, lead, conv):
        assert lead.consent_at is None
        ctx = _ctx(db_session, clinic, conv, lead)
        await _handle_guardar_lead(ctx, {"nombre": "Ana García"})

        await db_session.refresh(lead)
        assert lead.consent_at is not None

    async def test_does_not_overwrite_existing_consent_at(self, db_session, clinic, lead, conv):
        original = datetime(2026, 6, 1, tzinfo=timezone.utc)
        lead.consent_at = original
        await db_session.flush()

        ctx = _ctx(db_session, clinic, conv, lead)
        await _handle_guardar_lead(ctx, {"nombre": "Ana García"})

        await db_session.refresh(lead)
        # SQLite strips tzinfo on round-trip; compare naive values to verify
        # the original timestamp was not overwritten with datetime.now().
        assert lead.consent_at.replace(tzinfo=None) == original.replace(tzinfo=None)

    async def test_links_treatment_by_exact_name(self, db_session, clinic, lead, conv, treatment):
        ctx = _ctx(db_session, clinic, conv, lead)
        await _handle_guardar_lead(ctx, {"nombre": "Ana", "tratamiento_interes": "Limpieza Dental"})

        await db_session.refresh(lead)
        assert lead.interested_treatment_id == treatment.id

    async def test_treatment_lookup_is_case_insensitive(self, db_session, clinic, lead, conv, treatment):
        ctx = _ctx(db_session, clinic, conv, lead)
        await _handle_guardar_lead(ctx, {"nombre": "Ana", "tratamiento_interes": "LIMPIEZA DENTAL"})

        await db_session.refresh(lead)
        assert lead.interested_treatment_id == treatment.id

    async def test_unknown_treatment_leaves_id_none(self, db_session, clinic, lead, conv):
        ctx = _ctx(db_session, clinic, conv, lead)
        await _handle_guardar_lead(ctx, {"nombre": "Ana", "tratamiento_interes": "Cirugía Marciana"})

        await db_session.refresh(lead)
        assert lead.interested_treatment_id is None

    async def test_treatment_from_other_tenant_not_linked(self, db_session, clinic, lead, conv, other_clinic):
        foreign = Treatment(
            tenant_id=other_clinic.id,
            name="Limpieza Dental",  # same name, different tenant
            is_active=True,
        )
        db_session.add(foreign)
        await db_session.flush()

        ctx = _ctx(db_session, clinic, conv, lead)
        await _handle_guardar_lead(ctx, {"nombre": "Ana", "tratamiento_interes": "Limpieza Dental"})

        await db_session.refresh(lead)
        # No treatment belongs to clinic, so id stays None
        assert lead.interested_treatment_id is None

    async def test_no_inputs_is_a_noop(self, db_session, clinic, lead, conv):
        ctx = _ctx(db_session, clinic, conv, lead)
        result = json.loads(await _handle_guardar_lead(ctx, {}))

        assert result["status"] == "saved"
        await db_session.refresh(lead)
        assert lead.name is None
        assert lead.consent_at is None


# ---------------------------------------------------------------------------
# consultar_tratamiento
# ---------------------------------------------------------------------------

class TestConsultarTratamiento:
    async def test_found_returns_all_fields(self, db_session, clinic, lead, conv, treatment):
        ctx = _ctx(db_session, clinic, conv, lead)
        result = json.loads(await _handle_consultar_tratamiento(ctx, {"nombre": "Limpieza Dental"}))

        assert result["encontrado"] is True
        assert result["nombre"] == "Limpieza Dental"
        assert result["descripcion"] == "Profilaxis completa con ultrasonido."
        assert result["duracion_minutos"] == 45
        assert result["precio_desde"] == 80.0
        assert result["requiere_valoracion"] is False

    async def test_partial_match(self, db_session, clinic, lead, conv, treatment):
        ctx = _ctx(db_session, clinic, conv, lead)
        result = json.loads(await _handle_consultar_tratamiento(ctx, {"nombre": "limpieza"}))

        assert result["encontrado"] is True
        assert result["nombre"] == "Limpieza Dental"

    async def test_case_insensitive_search(self, db_session, clinic, lead, conv, treatment):
        ctx = _ctx(db_session, clinic, conv, lead)
        result = json.loads(await _handle_consultar_tratamiento(ctx, {"nombre": "LIMPIEZA"}))

        assert result["encontrado"] is True

    async def test_not_found(self, db_session, clinic, lead, conv):
        ctx = _ctx(db_session, clinic, conv, lead)
        result = json.loads(await _handle_consultar_tratamiento(ctx, {"nombre": "Ortodoncia"}))

        assert result["encontrado"] is False
        assert "Ortodoncia" in result["mensaje"]

    async def test_tenant_isolation(self, db_session, clinic, lead, conv, other_clinic):
        foreign = Treatment(
            tenant_id=other_clinic.id,
            name="Blanqueamiento Láser",
            is_active=True,
        )
        db_session.add(foreign)
        await db_session.flush()

        ctx = _ctx(db_session, clinic, conv, lead)
        result = json.loads(await _handle_consultar_tratamiento(ctx, {"nombre": "Blanqueamiento"}))

        assert result["encontrado"] is False

    async def test_inactive_treatment_not_returned(self, db_session, clinic, lead, conv):
        inactive = Treatment(
            tenant_id=clinic.id,
            name="Implante Antiguo",
            is_active=False,
        )
        db_session.add(inactive)
        await db_session.flush()

        ctx = _ctx(db_session, clinic, conv, lead)
        result = json.loads(await _handle_consultar_tratamiento(ctx, {"nombre": "Implante"}))

        assert result["encontrado"] is False

    async def test_none_price_serializes_as_null(self, db_session, clinic, lead, conv):
        t = Treatment(tenant_id=clinic.id, name="Consulta General", is_active=True)
        db_session.add(t)
        await db_session.flush()

        ctx = _ctx(db_session, clinic, conv, lead)
        result = json.loads(await _handle_consultar_tratamiento(ctx, {"nombre": "Consulta"}))

        assert result["encontrado"] is True
        assert result["precio_desde"] is None


# ---------------------------------------------------------------------------
# derivar_a_humano
# ---------------------------------------------------------------------------

class TestDerivarAHumano:
    async def test_sets_conversation_status_to_human(self, db_session, clinic, lead, conv):
        assert conv.status == "bot"
        ctx = _ctx(db_session, clinic, conv, lead)
        await _handle_derivar_a_humano(ctx, {"motivo": "El paciente solicita atención personalizada."})

        await db_session.refresh(conv)
        assert conv.status == "human"

    async def test_returns_handoff_requested_with_motivo(self, db_session, clinic, lead, conv):
        ctx = _ctx(db_session, clinic, conv, lead)
        result = json.loads(
            await _handle_derivar_a_humano(ctx, {"motivo": "Urgencia: dolor intenso."})
        )

        assert result["status"] == "handoff_requested"
        assert result["motivo"] == "Urgencia: dolor intenso."

    async def test_idempotent_on_already_human_conversation(self, db_session, clinic, lead, conv):
        ctx = _ctx(db_session, clinic, conv, lead)
        await _handle_derivar_a_humano(ctx, {"motivo": "Primera derivación"})
        result = json.loads(
            await _handle_derivar_a_humano(ctx, {"motivo": "Segunda derivación"})
        )

        await db_session.refresh(conv)
        assert conv.status == "human"
        assert result["status"] == "handoff_requested"

    async def test_empty_motivo_is_accepted(self, db_session, clinic, lead, conv):
        ctx = _ctx(db_session, clinic, conv, lead)
        result = json.loads(await _handle_derivar_a_humano(ctx, {}))

        assert result["status"] == "handoff_requested"
        assert result["motivo"] == ""
        await db_session.refresh(conv)
        assert conv.status == "human"


# ---------------------------------------------------------------------------
# agendar_cita
# ---------------------------------------------------------------------------

_LIMA = ZoneInfo("America/Lima")

# Monday with a morning + evening range, matching test_availability.py's
# BH_STANDARD shape. 2026-07-06 is a Monday.
_BH_LIMA = {
    "slot_duration_minutes": 30,
    "days": {
        "monday":    [{"from": "09:00", "to": "14:00"}, {"from": "16:00", "to": "20:00"}],
        "tuesday":   [{"from": "09:00", "to": "14:00"}],
        "wednesday": [{"from": "09:00", "to": "14:00"}],
        "thursday":  [{"from": "09:00", "to": "14:00"}],
        "friday":    [{"from": "09:00", "to": "14:00"}],
        "saturday":  [],
        "sunday":    [],
    },
}


@pytest.fixture
def now_lima() -> datetime:
    """Frozen "now" (before every hardcoded 2026-07-06 fecha_hora below) for
    agendar_cita's `now` override, so these tests stay deterministic instead
    of failing once the real wall-clock date passes 2026-07-06.
    """
    return datetime(2026, 7, 1, 9, 0, tzinfo=_LIMA)


@pytest_asyncio.fixture
async def lead2(db_session, clinic):
    l = Lead(tenant_id=clinic.id, whatsapp_number="+51999000002", source="whatsapp", status="new")
    db_session.add(l)
    await db_session.flush()
    await db_session.refresh(l)
    return l


class TestAgendarCita:
    async def test_schedules_appointment_and_persists_row(
        self, db_session, clinic, lead, conv, treatment, now_lima
    ):
        ctx = _ctx(db_session, clinic, conv, lead)
        result = json.loads(
            await _handle_agendar_cita(
                ctx,
                {"fecha_hora": "2026-07-06T10:00", "tratamiento": "Limpieza Dental"},
                now=now_lima,
            )
        )

        assert result["status"] == "scheduled"
        assert "aviso" not in result

        row = await db_session.get(Appointment, uuid.UUID(result["appointment_id"]))
        assert row.tenant_id == clinic.id
        assert row.lead_id == lead.id
        assert row.treatment_id == treatment.id
        assert row.status == "confirmed"

    async def test_sets_patient_name_and_consent(
        self, db_session, clinic, lead, conv, treatment, now_lima
    ):
        assert lead.consent_at is None
        ctx = _ctx(db_session, clinic, conv, lead)
        await _handle_agendar_cita(
            ctx,
            {
                "fecha_hora": "2026-07-06T10:00",
                "tratamiento": "Limpieza Dental",
                "nombre_paciente": "Ana García",
            },
            now=now_lima,
        )

        await db_session.refresh(lead)
        assert lead.name == "Ana García"
        assert lead.consent_at is not None

    async def test_unknown_treatment_schedules_with_none_and_aviso(
        self, db_session, clinic, lead, conv, now_lima
    ):
        ctx = _ctx(db_session, clinic, conv, lead)
        result = json.loads(
            await _handle_agendar_cita(
                ctx,
                {"fecha_hora": "2026-07-06T10:00", "tratamiento": "Cirugía Marciana"},
                now=now_lima,
            )
        )

        assert result["status"] == "scheduled"
        assert "no se encontró" in result["aviso"].lower()
        assert "consultar_tratamiento" in result["aviso"]

        row = await db_session.get(Appointment, uuid.UUID(result["appointment_id"]))
        assert row.treatment_id is None

    async def test_invalid_fecha_hora_returns_fecha_invalida(self, db_session, clinic, lead, conv, treatment):
        ctx = _ctx(db_session, clinic, conv, lead)
        result = json.loads(
            await _handle_agendar_cita(
                ctx, {"fecha_hora": "not-a-datetime", "tratamiento": "Limpieza Dental"}
            )
        )

        assert result["status"] == "fecha_invalida"

    async def test_rejects_double_booking_and_offers_alternativas(
        self, db_session, clinic, lead, lead2, conv, treatment, now_lima
    ):
        ctx1 = _ctx(db_session, clinic, conv, lead)
        first = json.loads(
            await _handle_agendar_cita(
                ctx1,
                {"fecha_hora": "2026-07-06T10:00", "tratamiento": "Limpieza Dental"},
                now=now_lima,
            )
        )
        assert first["status"] == "scheduled"

        ctx2 = _ctx(db_session, clinic, conv, lead2)
        second = json.loads(
            await _handle_agendar_cita(
                ctx2,
                {"fecha_hora": "2026-07-06T10:00", "tratamiento": "Limpieza Dental"},
                now=now_lima,
            )
        )

        assert second["status"] == "slot_no_disponible"
        assert isinstance(second["alternativas"], list)

        # Savepoint rollback must leave the session usable for further queries.
        row = await db_session.get(Appointment, uuid.UUID(first["appointment_id"]))
        assert row is not None

    async def test_conflict_at_lima_20h_offers_alternatives_for_same_local_day(
        self, db_session, clinic, lead, lead2, conv, treatment, now_lima
    ):
        """Regression/invariant test for the day-boundary case: a clash at
        20:00 America/Lima (UTC-5) is 01:00 UTC the *next* calendar day.
        Alternatives must be computed for the tenant's local day (2026-07-06),
        not the UTC day (2026-07-07), or the bot would offer slots for the
        wrong day after a collision.

        Note: this does NOT regress against a naive `scheduled_at.date()`
        implementation either — Python's `datetime.date()` does not normalize
        to UTC, so both approaches already agree here. It's kept as an
        explicit invariant check for this boundary case, not a before/after
        regression test.
        """
        clinic.timezone = "America/Lima"
        clinic.business_hours = _BH_LIMA
        await db_session.flush()

        ctx1 = _ctx(db_session, clinic, conv, lead)
        first = json.loads(
            await _handle_agendar_cita(
                ctx1,
                {"fecha_hora": "2026-07-06T20:00", "tratamiento": "Limpieza Dental"},
                now=now_lima,
            )
        )
        assert first["status"] == "scheduled"

        ctx2 = _ctx(db_session, clinic, conv, lead2)
        second = json.loads(
            await _handle_agendar_cita(
                ctx2,
                {"fecha_hora": "2026-07-06T20:00", "tratamiento": "Limpieza Dental"},
                now=now_lima,
            )
        )

        assert second["status"] == "slot_no_disponible"
        alternativas = second["alternativas"]
        assert len(alternativas) > 0
        assert all(slot.startswith("2026-07-06T") for slot in alternativas)
        assert not any(slot.startswith("2026-07-07T") for slot in alternativas)


# ---------------------------------------------------------------------------
# agendar_cita — pasada / grid / horizonte validations
#
# All tests inject `now` explicitly (mirrors compute_slots' reference_date)
# so they stay deterministic regardless of the wall-clock date the suite
# happens to run on.
# ---------------------------------------------------------------------------


class TestAgendarCitaValidations:
    async def test_fecha_pasada_rejects_past_datetime(self, db_session, clinic, lead, conv):
        ctx = _ctx(db_session, clinic, conv, lead)
        result = json.loads(
            await _handle_agendar_cita(
                ctx,
                {"fecha_hora": "2026-06-30T10:00", "tratamiento": "Limpieza Dental"},
                now=datetime(2026, 7, 1, 9, 0, tzinfo=_LIMA),
            )
        )

        assert result["status"] == "fecha_pasada"

    async def test_fecha_pasada_boundary_today_but_earlier_hour(self, db_session, clinic, lead, conv):
        """Frontera: hoy a las 09:00 cuando 'ahora' ya son las 14:00 del mismo día."""
        ctx = _ctx(db_session, clinic, conv, lead)
        result = json.loads(
            await _handle_agendar_cita(
                ctx,
                {"fecha_hora": "2026-07-06T09:00", "tratamiento": "Limpieza Dental"},
                now=datetime(2026, 7, 6, 14, 0, tzinfo=_LIMA),
            )
        )

        assert result["status"] == "fecha_pasada"

    async def test_hora_fuera_de_grid_rejected(self, db_session, clinic, lead, conv):
        # clinic fixture's business_hours={} -> default slot duration is 30 min
        ctx = _ctx(db_session, clinic, conv, lead)
        result = json.loads(
            await _handle_agendar_cita(
                ctx,
                {"fecha_hora": "2026-07-06T10:15", "tratamiento": "Limpieza Dental"},
                now=datetime(2026, 7, 1, 9, 0, tzinfo=_LIMA),
            )
        )

        assert result["status"] == "hora_fuera_de_grid"

    async def test_fecha_fuera_de_horizonte_rejected(self, db_session, clinic, lead, conv):
        ctx = _ctx(db_session, clinic, conv, lead)
        result = json.loads(
            await _handle_agendar_cita(
                ctx,
                # ~106 days after "now", past MAX_BOOKING_HORIZON_DAYS=90
                {"fecha_hora": "2026-10-15T10:00", "tratamiento": "Limpieza Dental"},
                now=datetime(2026, 7, 1, 9, 0, tzinfo=_LIMA),
            )
        )

        assert result["status"] == "fecha_fuera_de_horizonte"
