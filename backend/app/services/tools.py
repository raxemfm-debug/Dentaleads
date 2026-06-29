"""
Tool dispatcher and dental tool definitions for the conversation loop.

DENTAL_TOOLS  — LLMTool list passed to the LLM on every completion.
ToolContext   — per-request context threaded through each handler.
ToolDispatcher — registry: tool name → async handler.  dispatch() never raises;
                 errors are contained and returned as JSON so the model can react.

All 5 handlers are stubs.  Real implementations land in Phase 2.
"""
import json
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.providers import LLMTool
from app.models.clinic import Clinic
from app.models.conversation import Conversation
from app.models.lead import Lead

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
# Stub handlers — TODO(stub): replace with real implementations in Phase 2
# ---------------------------------------------------------------------------

async def _stub_verificar_disponibilidad(ctx: ToolContext, inputs: dict) -> str:
    # TODO(stub): query appointments table for real availability slots
    logger.debug("stub verificar_disponibilidad inputs=%s", inputs)
    return json.dumps({
        "slots": ["2026-07-01T10:00", "2026-07-01T11:00", "2026-07-02T09:00"],
    })


async def _stub_agendar_cita(ctx: ToolContext, inputs: dict) -> str:
    # TODO(stub): insert appointment row, confirm, and update lead status
    logger.debug("stub agendar_cita inputs=%s", inputs)
    return json.dumps({"status": "scheduled", "appointment_id": "stub-001"})


async def _stub_guardar_lead(ctx: ToolContext, inputs: dict) -> str:
    # TODO(stub): upsert lead fields from inputs (name, treatment interest)
    logger.debug("stub guardar_lead inputs=%s", inputs)
    return json.dumps({"status": "saved", "lead_id": "stub-lead"})


async def _stub_consultar_tratamiento(ctx: ToolContext, inputs: dict) -> str:
    # TODO(stub): look up treatment from the treatments table for this tenant
    logger.debug("stub consultar_tratamiento inputs=%s", inputs)
    return json.dumps({
        "nombre": inputs.get("nombre", ""),
        "description": "Consulta con el profesional para más detalles.",
        "duration_minutes": 60,
        "price_from": None,
    })


async def _stub_derivar_a_humano(ctx: ToolContext, inputs: dict) -> str:
    # TODO(stub): set conversation.status = "human" and notify staff
    logger.debug("stub derivar_a_humano inputs=%s", inputs)
    return json.dumps({"status": "handoff_requested"})


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
    d.register("verificar_disponibilidad", _stub_verificar_disponibilidad)
    d.register("agendar_cita", _stub_agendar_cita)
    d.register("guardar_lead", _stub_guardar_lead)
    d.register("consultar_tratamiento", _stub_consultar_tratamiento)
    d.register("derivar_a_humano", _stub_derivar_a_humano)
    return d


DISPATCHER: ToolDispatcher = _make_dispatcher()
