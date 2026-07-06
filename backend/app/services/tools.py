"""
Tool dispatcher and dental tool definitions for the conversation loop.

DENTAL_TOOLS  — LLMTool list passed to the LLM on every completion.
ToolContext   — per-request context threaded through each handler.
ToolDispatcher — registry: tool name → async handler.  dispatch() never raises;
                 errors are contained and returned as JSON so the model can react.

All 5 tools have real implementations. agendar_cita validates in order
(parse → fecha_pasada → hora_fuera_de_grid → fecha_fuera_de_horizonte →
tratamiento → insert) before guarding against double booking with a
SAVEPOINT (db.begin_nested()) around the insert: on IntegrityError from
uq_appointments_tenant_slot it rolls back just the savepoint (the outer
session/transaction stays usable) and returns free alternatives for the
same day instead of propagating the error.
"""
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.providers import LLMTool
from app.models.appointment import Appointment
from app.models.clinic import Clinic
from app.models.conversation import Conversation
from app.models.lead import Lead
from app.models.treatment import Treatment
from app.services.availability import (
    AvailabilityService,
    get_slot_duration_minutes,
    resolve_clinic_timezone,
)

logger = logging.getLogger(__name__)

ToolHandler = Callable[["ToolContext", dict], Awaitable[str]]

# TODO(deuda): valor fijo igual para todas las clínicas; podría migrar a
# clinic.config (p.ej. "booking_horizon_days") si algún tenant necesita un
# horizonte de agendamiento distinto.
MAX_BOOKING_HORIZON_DAYS = 90

@dataclass
class ToolContext:
    db: AsyncSession
    clinic: Clinic
    conv: Conversation
    lead: Lead


# ---------------------------------------------------------------------------
# Tool schemas exposed to the LLM
# ---------------------------------------------------------------------------

DENTAL_TOOLS: list[LLMTool] = [
    LLMTool(
        name="verificar_disponibilidad",
        description="Verifica los turnos disponibles para una fecha dada.",
        input_schema={
            "type": "object",
            "properties": {
                "fecha": {
                    "type": "string",
                    "description": "Fecha en formato YYYY-MM-DD",
                },
            },
            "required": ["fecha"],
        },
    ),
    LLMTool(
        name="agendar_cita",
        description="Agenda una cita para el paciente en un horario disponible.",
        input_schema={
            "type": "object",
            "properties": {
                "fecha_hora": {
                    "type": "string",
                    "description": "Fecha y hora en formato YYYY-MM-DDTHH:MM",
                },
                "tratamiento": {"type": "string", "description": "Nombre del tratamiento"},
                "nombre_paciente": {"type": "string"},
            },
            "required": ["fecha_hora", "tratamiento"],
        },
    ),
    LLMTool(
        name="guardar_lead",
        description="Registra los datos de contacto e interés del paciente potencial.",
        input_schema={
            "type": "object",
            "properties": {
                "nombre": {"type": "string"},
                "tratamiento_interes": {"type": "string"},
            },
            "required": ["nombre"],
        },
    ),
    LLMTool(
        name="consultar_tratamiento",
        description=(
            "Devuelve información sobre un tratamiento dental "
            "(precio orientativo, duración, si requiere valoración previa)."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "nombre": {"type": "string", "description": "Nombre del tratamiento"},
            },
            "required": ["nombre"],
        },
    ),
    LLMTool(
        name="derivar_a_humano",
        description=(
            "Marca la conversación para que el/la doctor(a) o el personal del "
            "consultorio la revise y confirme por este mismo chat lo que el bot "
            "no pudo resolver."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "motivo": {"type": "string", "description": "Razón de la derivación"},
            },
            "required": ["motivo"],
        },
    ),
]


# ---------------------------------------------------------------------------
# Real handlers
# ---------------------------------------------------------------------------

async def _handle_verificar_disponibilidad(ctx: ToolContext, inputs: dict) -> str:
    result = await AvailabilityService.get_available_slots(
        ctx.db, ctx.clinic, inputs.get("fecha", "")
    )
    return json.dumps(result, ensure_ascii=False)


async def _handle_guardar_lead(ctx: ToolContext, inputs: dict) -> str:
    nombre: str | None = inputs.get("nombre")
    tratamiento_interes: str | None = inputs.get("tratamiento_interes")

    if nombre:
        ctx.lead.name = nombre
        if ctx.lead.consent_at is None:
            ctx.lead.consent_at = datetime.now(tz=timezone.utc)

    if tratamiento_interes:
        row = (
            await ctx.db.execute(
                select(Treatment)
                .where(Treatment.tenant_id == ctx.clinic.id)
                .where(func.lower(Treatment.name) == tratamiento_interes.lower())
                .where(Treatment.is_active.is_(True))
                .limit(1)
            )
        ).scalars().first()
        if row is not None:
            ctx.lead.interested_treatment_id = row.id

    await ctx.db.flush()
    return json.dumps({"status": "saved", "lead_id": str(ctx.lead.id)}, ensure_ascii=False)


async def _handle_consultar_tratamiento(ctx: ToolContext, inputs: dict) -> str:
    nombre: str = inputs.get("nombre", "")
    row = (
        await ctx.db.execute(
            select(Treatment)
            .where(Treatment.tenant_id == ctx.clinic.id)
            .where(func.lower(Treatment.name).like(f"%{nombre.lower()}%"))
            .where(Treatment.is_active.is_(True))
            .limit(1)
        )
    ).scalars().first()

    if row is None:
        return json.dumps({"encontrado": False, "mensaje": f"No hay información sobre '{nombre}' en esta clínica."}, ensure_ascii=False)

    return json.dumps({
        "encontrado": True,
        "nombre": row.name,
        "descripcion": row.description,
        "duracion_minutos": row.duration_minutes,
        "precio_desde": float(row.price_from) if row.price_from is not None else None,
        "requiere_valoracion": row.requires_consult,
    }, ensure_ascii=False)


async def _handle_derivar_a_humano(ctx: ToolContext, inputs: dict) -> str:
    ctx.conv.status = "human"
    await ctx.db.flush()
    return json.dumps({"status": "handoff_requested", "motivo": inputs.get("motivo", "")}, ensure_ascii=False)


async def _handle_agendar_cita(
    ctx: ToolContext,
    inputs: dict,
    *,
    now: datetime | None = None,
) -> str:
    """
    now: override for "current time" in tests (mirrors compute_slots'
    reference_date), so the fecha_pasada/horizonte checks stay deterministic
    instead of depending on wall-clock time. Production callers never pass it.
    """
    fecha_hora_str: str = inputs.get("fecha_hora", "")
    tratamiento_nombre: str = inputs.get("tratamiento", "")
    nombre_paciente: str | None = inputs.get("nombre_paciente")

    # --- parse -----------------------------------------------------------
    try:
        tz = resolve_clinic_timezone(ctx.clinic)
    except ValueError:
        logger.error("clinic %s has invalid timezone: %s", ctx.clinic.id, ctx.clinic.timezone)
        return json.dumps({"status": "fecha_invalida"}, ensure_ascii=False)

    try:
        naive = datetime.fromisoformat(fecha_hora_str)
    except (ValueError, TypeError):
        return json.dumps({"status": "fecha_invalida"}, ensure_ascii=False)
    # fecha_hora carries no offset per the tool's contract, so naive.date()
    # already *is* the tenant's local day — reused as-is for alternatives,
    # independent of whatever scheduled_at ends up looking like.
    local_date_str = naive.date().isoformat()
    scheduled_at = naive.replace(tzinfo=tz) if naive.tzinfo is None else naive.astimezone(tz)

    current = now if now is not None else datetime.now(tz=tz)

    # --- pasada ------------------------------------------------------------
    # Full-datetime comparison (not just the date) so "hoy a las 09:00" is
    # rejected once it's already 14:00 today — verificar_disponibilidad's
    # fecha_pasada only compares calendar dates, which isn't precise enough
    # for actually booking a slot.
    if scheduled_at < current:
        return json.dumps({"status": "fecha_pasada"}, ensure_ascii=False)

    # --- grid ----------------------------------------------------------------
    slot_duration = get_slot_duration_minutes(ctx.clinic)
    if scheduled_at.minute % slot_duration != 0:
        return json.dumps({"status": "hora_fuera_de_grid"}, ensure_ascii=False)

    # --- horizonte -------------------------------------------------------
    if scheduled_at > current + timedelta(days=MAX_BOOKING_HORIZON_DAYS):
        return json.dumps({"status": "fecha_fuera_de_horizonte"}, ensure_ascii=False)

    # --- tratamiento -------------------------------------------------------
    treatment = (
        await ctx.db.execute(
            select(Treatment)
            .where(Treatment.tenant_id == ctx.clinic.id)
            .where(func.lower(Treatment.name) == tratamiento_nombre.lower())
            .where(Treatment.is_active.is_(True))
            .limit(1)
        )
    ).scalars().first()

    aviso: str | None = None
    if treatment is None:
        # Appointment.treatment_id is nullable (ondelete="SET NULL"), so an
        # unrecognized name doesn't block scheduling — flag it instead.
        aviso = (
            f"No se encontró el tratamiento '{tratamiento_nombre}' en el catálogo; "
            "la cita se agendó sin tratamiento vinculado. Usa consultar_tratamiento "
            "para confirmar el nombre exacto."
        )

    if nombre_paciente:
        ctx.lead.name = nombre_paciente
        if ctx.lead.consent_at is None:
            ctx.lead.consent_at = datetime.now(tz=timezone.utc)

    # --- insert --------------------------------------------------------------
    appointment = Appointment(
        tenant_id=ctx.clinic.id,
        lead_id=ctx.lead.id,
        treatment_id=treatment.id if treatment else None,
        scheduled_at=scheduled_at,
        status="confirmed",
    )

    try:
        async with ctx.db.begin_nested():
            ctx.db.add(appointment)
            await ctx.db.flush()
    except IntegrityError:
        # solo revierte al savepoint; la sesión sigue viva
        alternativas = await AvailabilityService.get_available_slots(
            ctx.db, ctx.clinic, local_date_str
        )
        return json.dumps(
            {"status": "slot_no_disponible", "alternativas": alternativas.get("slots", [])},
            ensure_ascii=False,
        )

    result: dict = {"status": "scheduled", "appointment_id": str(appointment.id)}
    if aviso:
        result["aviso"] = aviso
    return json.dumps(result, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

class ToolDispatcher:
    """Registry mapping tool names to async handlers.

    dispatch() is guaranteed to return a str and never raise: unknown tools and
    handler exceptions are both caught and returned as error JSON so the model
    can react to failures rather than crashing the conversation loop.
    """

    def __init__(self) -> None:
        self._registry: dict[str, ToolHandler] = {}

    def register(self, name: str, handler: ToolHandler) -> None:
        self._registry[name] = handler

    async def dispatch(self, name: str, inputs: dict, ctx: ToolContext) -> str:
        handler = self._registry.get(name)
        if handler is None:
            logger.warning("unknown tool requested: '%s'", name)
            return json.dumps({"error": f"tool '{name}' not implemented"})
        try:
            return await handler(ctx, inputs)
        except Exception as exc:
            logger.exception("tool '%s' raised: inputs=%s", name, inputs)
            return json.dumps({"error": str(exc)})


def _make_dispatcher() -> ToolDispatcher:
    d = ToolDispatcher()
    d.register("verificar_disponibilidad", _handle_verificar_disponibilidad)
    d.register("agendar_cita", _handle_agendar_cita)
    d.register("guardar_lead", _handle_guardar_lead)
    d.register("consultar_tratamiento", _handle_consultar_tratamiento)
    d.register("derivar_a_humano", _handle_derivar_a_humano)
    return d


DISPATCHER: ToolDispatcher = _make_dispatcher()
