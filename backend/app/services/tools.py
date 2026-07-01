"""
Tool dispatcher and dental tool definitions for the conversation loop.

DENTAL_TOOLS  — LLMTool list passed to the LLM on every completion.
ToolContext   — per-request context threaded through each handler.
ToolDispatcher — registry: tool name → async handler.  dispatch() never raises;
                 errors are contained and returned as JSON so the model can react.

verificar_disponibilidad — real implementation (AvailabilityService).
Remaining 4 handlers are stubs to be replaced in subsequent tasks.
"""
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Awaitable, Callable

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.providers import LLMTool
from app.models.clinic import Clinic
from app.models.conversation import Conversation
from app.models.lead import Lead
from app.models.treatment import Treatment
from app.services.availability import AvailabilityService

logger = logging.getLogger(__name__)

ToolHandler = Callable[["ToolContext", dict], Awaitable[str]]


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
        description="Transfiere la conversación a un operador humano de la clínica.",
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


# ---------------------------------------------------------------------------
# Stub handler — TODO(stub): replace with real implementation in Lote 2
# ---------------------------------------------------------------------------

async def _stub_agendar_cita(ctx: ToolContext, inputs: dict) -> str:
    # TODO(stub): insert appointment row with double-booking guard (design pending)
    logger.debug("stub agendar_cita inputs=%s", inputs)
    return json.dumps({"status": "scheduled", "appointment_id": "stub-001"})


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
    d.register("agendar_cita", _stub_agendar_cita)
    d.register("guardar_lead", _handle_guardar_lead)
    d.register("consultar_tratamiento", _handle_consultar_tratamiento)
    d.register("derivar_a_humano", _handle_derivar_a_humano)
    return d


DISPATCHER: ToolDispatcher = _make_dispatcher()
