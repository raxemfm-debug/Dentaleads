"""
Conversation loop — inbound message → LLM → persist → reply.

Entry point: handle(). All provider calls go through the abstract
MessagingProvider and LLMProvider interfaces so concrete implementations
can be swapped without touching this module.
"""
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import TenantNotFoundError
from app.core.providers import (
    InboundMessage,
    LLMMessage,
    LLMProvider,
    LLMProviderError,
    LLMResponse,
    MessagingProvider,
    MessagingProviderError,
    OutboundMessage,
)
from app.models.clinic import Clinic
from app.models.conversation import Conversation
from app.models.lead import Lead
from app.models.message import Message
from app.services.availability import resolve_clinic_timezone
from app.services.tools import DENTAL_TOOLS, DISPATCHER, ToolContext, ToolDispatcher

logger = logging.getLogger(__name__)

HISTORY_LIMIT = 20
MAX_TOOL_ITERATIONS = 5

LLM_FALLBACK_MESSAGE = (
    "Disculpa, tuve un problema técnico. ¿Puedes intentar de nuevo en un momento?"
)

_DEFAULT_SYSTEM = (
    "Eres el asistente virtual de {clinic_name}, una clínica dental. "
    "{extra}"
    "Responde siempre en español con un tono cálido y profesional. "
    "Nunca emitas diagnósticos ni reemplaces la valoración del profesional."
)

_DIAS_ES = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
_MESES_ES = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]


def _format_fecha_actual(now: datetime, tz_name: str) -> str:
    dia = _DIAS_ES[now.weekday()]
    mes = _MESES_ES[now.month - 1]
    return (
        f"Hoy es {dia} {now.day} de {mes} de {now.year}, {now:%H:%M} ({tz_name}). "
        'Interpreta toda fecha relativa ("el viernes", "mañana") respecto a HOY '
        "y nunca propongas fechas pasadas."
    )


def _build_system_prompt(clinic: Clinic, *, now: datetime | None = None) -> str:
    """Compose the clinic's system prompt, prefixed with the current date/time.

    now: override for "current time" in tests (mirrors _handle_agendar_cita's
    pattern in tools.py), so the injected date stays deterministic instead of
    depending on wall-clock time. Production callers never pass it. DT-005: without
    this, the model has no notion of "today" and hallucinates dates when resolving
    relative expressions like "el viernes".
    """
    cfg = clinic.config or {}
    clinic_name = cfg.get("clinic_name") or clinic.name
    extra = cfg.get("description", "").strip()
    template = cfg.get("system_prompt_template") or _DEFAULT_SYSTEM
    base = template.format(clinic_name=clinic_name, extra=extra + " " if extra else "")

    try:
        tz = resolve_clinic_timezone(clinic)
    except ValueError:
        logger.error(
            "clinic %s has invalid timezone '%s', falling back to UTC for system prompt date",
            clinic.id, clinic.timezone,
        )
        tz = timezone.utc

    current = now if now is not None else datetime.now(tz=tz)
    fecha_actual = _format_fecha_actual(current, clinic.timezone or "UTC")
    return f"{fecha_actual}\n\n{base}"


async def is_wamid_processed(db: AsyncSession, wamid: str) -> bool:
    """True if a message with this WhatsApp message id (wamid) was already persisted.

    Meta retries webhook deliveries it considers failed (e.g. while send_message keeps
    failing due to an expired token, DT-004). Without this guard, every retry would
    re-run the LLM and persist a duplicate user+assistant pair for the same inbound
    message. Callers must check this before invoking handle().
    """
    if not wamid:
        return False
    existing = (
        await db.execute(select(Message.id).where(Message.wamid == wamid))
    ).scalars().first()
    return existing is not None


async def _get_or_create_lead(
    db: AsyncSession,
    tenant_id,
    whatsapp_number: str,
) -> Lead:
    lead = (
        await db.execute(
            select(Lead).where(
                Lead.tenant_id == tenant_id,
                Lead.whatsapp_number == whatsapp_number,
            )
        )
    ).scalars().first()
    if lead is None:
        lead = Lead(
            tenant_id=tenant_id,
            whatsapp_number=whatsapp_number,
            source="whatsapp",
            status="new",
        )
        db.add(lead)
        await db.flush()
    return lead


async def _get_or_create_conversation(
    db: AsyncSession,
    tenant_id,
    lead_id,
) -> Conversation:
    conv = (
        await db.execute(
            select(Conversation)
            .where(
                Conversation.tenant_id == tenant_id,
                Conversation.lead_id == lead_id,
                Conversation.status.in_(["bot", "human"]),
            )
            .order_by(Conversation.last_message_at.desc())
            .limit(1)
        )
    ).scalars().first()
    if conv is None:
        conv = Conversation(
            tenant_id=tenant_id,
            lead_id=lead_id,
            channel="whatsapp",
            status="bot",
        )
        db.add(conv)
        await db.flush()
    return conv


def _persist_exchange(
    db: AsyncSession,
    conv: Conversation,
    msg: InboundMessage,
    inbound_text: str,
    assistant_text: str,
    assistant_metadata: dict,
) -> None:
    db.add(
        Message(
            conversation_id=conv.id,
            role="user",
            content=inbound_text,
            wamid=msg.message_id or None,
            metadata_={"message_id": msg.message_id, "message_type": msg.message_type},
        )
    )
    db.add(
        Message(
            conversation_id=conv.id,
            role="assistant",
            content=assistant_text,
            metadata_=assistant_metadata,
        )
    )
    conv.last_message_at = datetime.now(timezone.utc)


async def _send_reply(messaging: MessagingProvider, to_number: str, text: str) -> None:
    """Send the reply, swallowing MessagingProviderError (DT-004).

    A delivery failure (expired token, timeout, ...) must never propagate to the
    webhook router: Meta interprets a 500 as delivery failure and retries the
    webhook, which can end up marking the subscription unhealthy. The exchange is
    already persisted by this point, so we log and move on rather than raise.
    """
    try:
        await messaging.send_message(OutboundMessage(to_number=to_number, text=text))
    except MessagingProviderError:
        logger.error("messaging provider failed to=%s", to_number, exc_info=True)


async def _run_llm_loop(
    llm: LLMProvider,
    system_prompt: str,
    messages: list[LLMMessage],
    dispatcher: ToolDispatcher,
    ctx: ToolContext,
) -> LLMResponse:
    """Run the agentic tool-calling loop.

    Calls llm.complete() repeatedly while stop_reason == "tool_use", executing
    each requested tool via the dispatcher and injecting results back into the
    message list.  Caps at MAX_TOOL_ITERATIONS and forces a final text-only
    completion as a safety fallback.
    """
    for _ in range(MAX_TOOL_ITERATIONS):
        response = await llm.complete(
            system_prompt=system_prompt,
            messages=messages,
            tools=DENTAL_TOOLS,
        )
        if response.stop_reason != "tool_use" or not response.tool_calls:
            return response

        # Replay the assistant's tool_use turn back into the message list so
        # subsequent calls see a valid alternating user/assistant sequence.
        messages.append(LLMMessage(
            role="assistant",
            content=response.content,
            tool_calls=response.tool_calls,
        ))

        for call in response.tool_calls:
            result = await dispatcher.dispatch(call["name"], call["inputs"], ctx)
            messages.append(LLMMessage(
                role="tool_result",
                content=result,
                tool_call_id=call["id"],
            ))

    # Safety fallback: expose no tools so the model is forced to emit end_turn.
    logger.warning(
        "tool loop capped at MAX_TOOL_ITERATIONS=%d, forcing final completion",
        MAX_TOOL_ITERATIONS,
    )
    return await llm.complete(system_prompt=system_prompt, messages=messages, tools=None)


async def handle(
    msg: InboundMessage,
    db: AsyncSession,
    messaging: MessagingProvider,
    llm: LLMProvider,
    history_limit: int = HISTORY_LIMIT,
) -> None:
    """
    Process one inbound message end-to-end.

    Raises TenantNotFoundError if no clinic is registered for msg.tenant_phone_id.
    """
    # 1. Identify tenant
    clinic = (
        await db.execute(
            select(Clinic).where(Clinic.whatsapp_phone_id == msg.tenant_phone_id)
        )
    ).scalars().first()
    if clinic is None:
        raise TenantNotFoundError(msg.tenant_phone_id)

    # 2. Resolve lead (upsert by whatsapp_number within tenant)
    lead = await _get_or_create_lead(db, clinic.id, msg.from_number)

    # 3. Resolve active conversation
    conv = await _get_or_create_conversation(db, clinic.id, lead.id)

    # 4. Load the N most recent messages, then reverse to chronological ascending
    #    for the LLM context window. Query runs before the inbound is persisted,
    #    so the current message is not in history — it is added once via append below.
    history_rows = list(
        reversed(
            (
                await db.execute(
                    select(Message)
                    .where(Message.conversation_id == conv.id)
                    .order_by(Message.created_at.desc())
                    .limit(history_limit)
                )
            )
            .scalars()
            .all()
        )
    )

    llm_messages = [
        LLMMessage(role=row.role, content=row.content)
        for row in history_rows
        if row.role in ("user", "assistant")
    ]

    # 5. Build system prompt from clinic.config
    system_prompt = _build_system_prompt(clinic)

    # 6. Append the inbound turn — exactly once, independent of persist order
    inbound_text = msg.text or ""
    llm_messages.append(LLMMessage(role="user", content=inbound_text))

    # 7. Run the agentic tool-calling loop
    ctx = ToolContext(db=db, clinic=clinic, conv=conv, lead=lead)
    try:
        response = await _run_llm_loop(llm, system_prompt, llm_messages, DISPATCHER, ctx)
    except LLMProviderError:
        # The LLM provider is down (timeout, connection error, rate limit, ...). Degrade
        # gracefully instead of letting the exception reach the webhook router: Meta
        # interprets a 500 as delivery failure and retries the webhook, which can end up
        # marking the subscription unhealthy. Log, persist what happened, and reply with
        # a fallback so the patient isn't left without a response.
        logger.error(
            "LLM provider failed tenant=%s conv=%s from=%s",
            clinic.id, conv.id, msg.from_number,
            exc_info=True,
        )
        _persist_exchange(
            db, conv, msg, inbound_text,
            LLM_FALLBACK_MESSAGE,
            {"stop_reason": "llm_provider_error"},
        )
        await db.flush()
        await _send_reply(messaging, msg.from_number, LLM_FALLBACK_MESSAGE)
        return

    # TODO(deuda): los intercambios intermedios del loop (turnos assistant tool_use +
    # tool_results) no se persisten; solo se guarda la respuesta final de texto.
    # Post-MVP: persistir el rastro completo de tool calls en la tabla messages
    # (con role="tool_use" / "tool_result" y metadata_ con inputs/outputs) para
    # facilitar la depuración de conversaciones y la auditoría del comportamiento del bot.

    # 8. Persist inbound + assistant response together, then refresh conversation timestamp
    _persist_exchange(
        db, conv, msg, inbound_text,
        response.content,
        {"stop_reason": response.stop_reason, "usage": response.usage},
    )
    await db.flush()

    # 9. Send reply
    logger.info("reply tenant=%s conv=%s to=%s", clinic.id, conv.id, msg.from_number)
    await _send_reply(messaging, msg.from_number, response.content)
