"""Tests for conversation.handle()."""
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.core.exceptions import TenantNotFoundError
from app.core.providers import InboundMessage, LLMResponse
from app.models.clinic import Clinic
from app.models.conversation import Conversation
from app.models.lead import Lead
from app.models.message import Message
from app.services.conversation import (
    HISTORY_LIMIT,
    LLM_FALLBACK_MESSAGE,
    _build_system_prompt,
    _rows_to_llm_messages,
    _trim_to_user_boundary,
    handle,
    is_wamid_processed,
)
from tests.fakes import (
    FailingLLMProvider,
    FailingMessagingProvider,
    FakeLLMProvider,
    FakeMessagingProvider,
    SequencedFakeLLMProvider,
    SpyFakeLLMProvider,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _msg(
    phone_id: str = "phone-test",
    from_number: str = "5491155551234",
    text: str = "Hola",
    msg_id: str = "wamid-001",
) -> InboundMessage:
    return InboundMessage(
        tenant_phone_id=phone_id,
        from_number=from_number,
        message_id=msg_id,
        text=text,
        message_type="text",
        raw_payload={},
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def clinic(db_session):
    c = Clinic(
        name="Clínica Test",
        whatsapp_phone_id="phone-test",
        config={},
    )
    db_session.add(c)
    await db_session.flush()
    return c


# ---------------------------------------------------------------------------
# Tenant resolution
# ---------------------------------------------------------------------------

async def test_unknown_tenant_raises(db_session):
    with pytest.raises(TenantNotFoundError) as exc_info:
        await handle(_msg(phone_id="ghost"), db_session, FakeMessagingProvider(), FakeLLMProvider())
    assert exc_info.value.phone_id == "ghost"


# ---------------------------------------------------------------------------
# Lead lifecycle
# ---------------------------------------------------------------------------

async def test_creates_lead_on_first_message(db_session, clinic):
    await handle(_msg(), db_session, FakeMessagingProvider(), FakeLLMProvider())

    lead = (await db_session.execute(
        select(Lead).where(Lead.tenant_id == clinic.id)
    )).scalars().first()

    assert lead is not None
    assert lead.whatsapp_number == "5491155551234"
    assert lead.source == "whatsapp"
    assert lead.status == "new"


async def test_reuses_existing_lead(db_session, clinic):
    await handle(_msg(msg_id="wamid-001"), db_session, FakeMessagingProvider(), FakeLLMProvider())
    await handle(_msg(msg_id="wamid-002"), db_session, FakeMessagingProvider(), FakeLLMProvider())

    leads = (await db_session.execute(
        select(Lead).where(Lead.tenant_id == clinic.id)
    )).scalars().all()
    assert len(leads) == 1


# ---------------------------------------------------------------------------
# Conversation lifecycle
# ---------------------------------------------------------------------------

async def test_creates_conversation_on_first_message(db_session, clinic):
    await handle(_msg(), db_session, FakeMessagingProvider(), FakeLLMProvider())

    conv = (await db_session.execute(
        select(Conversation).where(Conversation.tenant_id == clinic.id)
    )).scalars().first()

    assert conv is not None
    assert conv.channel == "whatsapp"
    assert conv.status == "bot"


async def test_reuses_active_conversation(db_session, clinic):
    await handle(_msg(msg_id="wamid-001"), db_session, FakeMessagingProvider(), FakeLLMProvider())
    await handle(_msg(msg_id="wamid-002"), db_session, FakeMessagingProvider(), FakeLLMProvider())

    convs = (await db_session.execute(
        select(Conversation).where(Conversation.tenant_id == clinic.id)
    )).scalars().all()
    assert len(convs) == 1


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

async def test_persists_user_and_assistant_messages(db_session, clinic):
    await handle(_msg(text="Necesito una limpieza"), db_session, FakeMessagingProvider(), FakeLLMProvider())

    conv = (await db_session.execute(
        select(Conversation).where(Conversation.tenant_id == clinic.id)
    )).scalars().first()
    msgs = (await db_session.execute(
        select(Message)
        .where(Message.conversation_id == conv.id)
        .order_by(Message.created_at.asc())
    )).scalars().all()

    assert len(msgs) == 2
    assert msgs[0].role == "user"
    assert msgs[0].content == "Necesito una limpieza"
    assert msgs[1].role == "assistant"
    assert msgs[1].content == FakeLLMProvider.FIXED_RESPONSE


# ---------------------------------------------------------------------------
# Reply
# ---------------------------------------------------------------------------

async def test_sends_reply_to_sender(db_session, clinic):
    messaging = FakeMessagingProvider()
    await handle(_msg(from_number="5491188887777"), db_session, messaging, FakeLLMProvider())

    assert len(messaging.sent) == 1
    assert messaging.sent[0].to_number == "5491188887777"
    assert messaging.sent[0].text == FakeLLMProvider.FIXED_RESPONSE


# ---------------------------------------------------------------------------
# LLM context window
# ---------------------------------------------------------------------------

async def test_inbound_appears_exactly_once_in_llm_context(db_session, clinic):
    spy = SpyFakeLLMProvider()
    await handle(_msg(text="¿Cuánto cuesta un implante?"), db_session, FakeMessagingProvider(), spy)

    assert len(spy.calls) == 1
    user_contents = [m.content for m in spy.calls[0] if m.role == "user"]
    assert user_contents == ["¿Cuánto cuesta un implante?"]


async def test_history_sent_to_llm_in_chronological_order(db_session, clinic):
    """History rows reach the LLM oldest-first (by sequence); inbound is last.

    Messages inserted in the same flush share an identical SQLite/Postgres timestamp
    (DT-001), so ordering is by the explicit `sequence` we seed here, not created_at.
    """
    spy = SpyFakeLLMProvider()
    messaging = FakeMessagingProvider()

    # Bootstrap to create lead + conversation (consumes sequence 0 and 1), then seed
    # more history explicitly continuing the sequence.
    await handle(_msg(text="bootstrap", msg_id="wamid-bootstrap"), db_session, messaging, FakeLLMProvider())
    conv = (await db_session.execute(
        select(Conversation).where(Conversation.tenant_id == clinic.id)
    )).scalars().first()

    t1 = datetime(2020, 1, 1, 0, 0, tzinfo=timezone.utc)
    t2 = datetime(2020, 1, 1, 1, 0, tzinfo=timezone.utc)
    db_session.add(Message(conversation_id=conv.id, role="user", content="primera pregunta",
                           metadata_={}, sequence=2, created_at=t1, updated_at=t1))
    db_session.add(Message(conversation_id=conv.id, role="assistant", content="primera respuesta",
                           metadata_={}, sequence=3, created_at=t2, updated_at=t2))
    await db_session.flush()

    await handle(_msg(text="segunda pregunta", msg_id="wamid-segunda"), db_session, messaging, spy)

    # Filter to only the seeded messages + new inbound, ignoring the bootstrap pair
    # whose same-flush timestamps have undefined relative order.
    tracked = {"primera pregunta", "primera respuesta", "segunda pregunta"}
    ordered = [m.content for m in spy.calls[0] if m.content in tracked]
    assert ordered == ["primera pregunta", "primera respuesta", "segunda pregunta"]


# ---------------------------------------------------------------------------
# LLM provider failure (DT-003)
# ---------------------------------------------------------------------------

async def test_llm_provider_error_does_not_propagate(db_session, clinic):
    """handle() must swallow LLMProviderError, not let it reach the webhook router."""
    await handle(_msg(), db_session, FakeMessagingProvider(), FailingLLMProvider())


async def test_llm_provider_error_sends_fallback_to_sender(db_session, clinic):
    messaging = FakeMessagingProvider()
    await handle(_msg(from_number="5491188887777"), db_session, messaging, FailingLLMProvider())

    assert len(messaging.sent) == 1
    assert messaging.sent[0].to_number == "5491188887777"
    assert messaging.sent[0].text == LLM_FALLBACK_MESSAGE


async def test_llm_provider_error_persists_inbound_and_fallback(db_session, clinic):
    messaging = FakeMessagingProvider()
    await handle(_msg(text="Hola"), db_session, messaging, FailingLLMProvider())

    conv = (await db_session.execute(
        select(Conversation).where(Conversation.tenant_id == clinic.id)
    )).scalars().first()
    msgs = (await db_session.execute(
        select(Message)
        .where(Message.conversation_id == conv.id)
        .order_by(Message.created_at.asc())
    )).scalars().all()

    assert len(msgs) == 2
    assert msgs[0].role == "user"
    assert msgs[0].content == "Hola"
    assert msgs[1].role == "assistant"
    assert msgs[1].content == LLM_FALLBACK_MESSAGE
    assert msgs[1].metadata_ == {"stop_reason": "llm_provider_error"}


async def test_normal_flow_intact_when_llm_succeeds(db_session, clinic):
    """Regression check: a healthy LLMProvider is unaffected by the new error handling."""
    messaging = FakeMessagingProvider()
    await handle(_msg(from_number="5491199990000"), db_session, messaging, FakeLLMProvider())

    assert messaging.sent[0].text == FakeLLMProvider.FIXED_RESPONSE
    conv = (await db_session.execute(
        select(Conversation).where(Conversation.tenant_id == clinic.id)
    )).scalars().first()
    msgs = (await db_session.execute(
        select(Message)
        .where(Message.conversation_id == conv.id)
        .order_by(Message.created_at.asc())
    )).scalars().all()
    assert len(msgs) == 2
    assert msgs[1].content == FakeLLMProvider.FIXED_RESPONSE


# ---------------------------------------------------------------------------
# Messaging provider failure (DT-004)
# ---------------------------------------------------------------------------

async def test_messaging_provider_error_does_not_propagate(db_session, clinic):
    """handle() must swallow MessagingProviderError, not let it reach the webhook router."""
    await handle(_msg(), db_session, FailingMessagingProvider(), FakeLLMProvider())


async def test_messaging_provider_error_still_persists_exchange(db_session, clinic):
    """A send failure must not stop the user+assistant pair from being persisted —
    the reply was generated fine, only delivery failed."""
    await handle(_msg(text="Hola"), db_session, FailingMessagingProvider(), FakeLLMProvider())

    conv = (await db_session.execute(
        select(Conversation).where(Conversation.tenant_id == clinic.id)
    )).scalars().first()
    msgs = (await db_session.execute(
        select(Message)
        .where(Message.conversation_id == conv.id)
        .order_by(Message.created_at.asc())
    )).scalars().all()

    assert len(msgs) == 2
    assert msgs[0].role == "user"
    assert msgs[1].role == "assistant"
    assert msgs[1].content == FakeLLMProvider.FIXED_RESPONSE


async def test_messaging_provider_error_in_fallback_path_does_not_propagate(db_session, clinic):
    """Both the LLM and the send fail: handle() must still swallow both, not raise."""
    await handle(_msg(), db_session, FailingMessagingProvider(), FailingLLMProvider())


async def test_messaging_provider_error_in_fallback_path_persists_fallback(db_session, clinic):
    await handle(_msg(text="Hola"), db_session, FailingMessagingProvider(), FailingLLMProvider())

    conv = (await db_session.execute(
        select(Conversation).where(Conversation.tenant_id == clinic.id)
    )).scalars().first()
    msgs = (await db_session.execute(
        select(Message)
        .where(Message.conversation_id == conv.id)
        .order_by(Message.created_at.asc())
    )).scalars().all()

    assert len(msgs) == 2
    assert msgs[1].content == LLM_FALLBACK_MESSAGE


# ---------------------------------------------------------------------------
# Idempotency by wamid (DT-004)
# ---------------------------------------------------------------------------

async def test_is_wamid_processed_false_when_unseen(db_session, clinic):
    assert await is_wamid_processed(db_session, "wamid-unseen") is False


async def test_is_wamid_processed_false_for_empty_string(db_session, clinic):
    assert await is_wamid_processed(db_session, "") is False


async def test_is_wamid_processed_true_after_handle(db_session, clinic):
    await handle(_msg(msg_id="wamid-seen"), db_session, FakeMessagingProvider(), FakeLLMProvider())
    assert await is_wamid_processed(db_session, "wamid-seen") is True


async def test_persisted_user_message_stores_wamid(db_session, clinic):
    await handle(_msg(msg_id="wamid-store-test"), db_session, FakeMessagingProvider(), FakeLLMProvider())

    conv = (await db_session.execute(
        select(Conversation).where(Conversation.tenant_id == clinic.id)
    )).scalars().first()
    user_msg = (await db_session.execute(
        select(Message).where(Message.conversation_id == conv.id, Message.role == "user")
    )).scalars().first()

    assert user_msg.wamid == "wamid-store-test"


async def test_history_limited_to_history_limit(db_session, clinic):
    """The LLM receives at most HISTORY_LIMIT historical rows, plus the new inbound."""
    spy = SpyFakeLLMProvider()
    messaging = FakeMessagingProvider()

    # Bootstrap lead + conversation via a normal call
    await handle(_msg(text="bootstrap", msg_id="wamid-bootstrap"), db_session, messaging, FakeLLMProvider())
    conv = (await db_session.execute(
        select(Conversation).where(Conversation.tenant_id == clinic.id)
    )).scalars().first()

    # Seed more than HISTORY_LIMIT messages with explicit sequence numbers (continuing
    # after the bootstrap pair's 0/1) so ordering is deterministic regardless of
    # same-flush timestamp ties (DT-001).
    base = datetime(2030, 1, 1, tzinfo=timezone.utc)
    for i in range(HISTORY_LIMIT + 5):
        db_session.add(Message(
            conversation_id=conv.id,
            role="user",
            content=f"old msg {i}",
            metadata_={},
            sequence=2 + i,
            created_at=base + timedelta(minutes=i),
            updated_at=base + timedelta(minutes=i),
        ))
    await db_session.flush()

    await handle(_msg(text="nuevo", msg_id="wamid-nuevo"), db_session, messaging, spy)

    # HISTORY_LIMIT historical rows fetched + 1 inbound appended
    assert len(spy.calls[0]) == HISTORY_LIMIT + 1
    assert spy.calls[0][-1].content == "nuevo"


# ---------------------------------------------------------------------------
# Tool turn persistence and memory (DT-001)
# ---------------------------------------------------------------------------

def _tool_use_response(tool_id: str = "toolu_abc", tool_name: str = "herramienta_test") -> LLMResponse:
    return LLMResponse(
        content="Voy a revisar.",
        tool_calls=[{"id": tool_id, "name": tool_name, "inputs": {"x": 1}}],
        stop_reason="tool_use",
        usage={"input_tokens": 10, "output_tokens": 5},
    )


def _end_turn_response(content: str) -> LLMResponse:
    return LLMResponse(
        content=content,
        tool_calls=[],
        stop_reason="end_turn",
        usage={"input_tokens": 5, "output_tokens": 5},
    )


async def test_persists_tool_use_and_tool_result_rows(db_session, clinic):
    """A tool-calling turn persists 4 rows in order: user, tool_use, tool_result, assistant.

    herramienta_test is not registered in the production dispatcher, so dispatch()
    returns a contained error tool_result (same behavior test_tools.py exercises for
    an unknown tool) — irrelevant here, since this test only cares about how the turn
    gets persisted, not about any specific tool's business logic.
    """
    llm = SequencedFakeLLMProvider([
        _tool_use_response(),
        _end_turn_response("Listo."),
    ])
    await handle(_msg(text="Necesito ayuda", msg_id="wamid-tool-1"), db_session, FakeMessagingProvider(), llm)

    conv = (await db_session.execute(
        select(Conversation).where(Conversation.tenant_id == clinic.id)
    )).scalars().first()
    msgs = (await db_session.execute(
        select(Message).where(Message.conversation_id == conv.id).order_by(Message.sequence.asc())
    )).scalars().all()

    assert [m.role for m in msgs] == ["user", "tool_use", "tool_result", "assistant"]
    assert [m.sequence for m in msgs] == [0, 1, 2, 3]
    assert msgs[1].metadata_["tool_calls"] == [
        {"id": "toolu_abc", "name": "herramienta_test", "inputs": {"x": 1}}
    ]
    assert msgs[2].metadata_["tool_call_id"] == "toolu_abc"
    assert msgs[2].metadata_["tool_name"] == "herramienta_test"
    assert msgs[3].content == "Listo."


async def test_tool_turns_visible_in_next_turn_context(db_session, clinic):
    """DT-001 live finding: after a tool-calling turn, the next turn's LLM context must
    include the assistant's tool_use turn and its tool_result — the model can no longer
    be blind to a tool call it already made, which is what let a "gracias" re-trigger
    agendar_cita on an already-confirmed slot."""
    first_llm = SequencedFakeLLMProvider([
        _tool_use_response(),
        _end_turn_response("Listo, tienes turno el viernes."),
    ])
    messaging = FakeMessagingProvider()
    await handle(_msg(text="Quiero agendar", msg_id="wamid-1"), db_session, messaging, first_llm)

    spy = SpyFakeLLMProvider()
    await handle(_msg(text="gracias", msg_id="wamid-2"), db_session, messaging, spy)

    sent = spy.calls[0]
    tool_use_msgs = [m for m in sent if m.role == "assistant" and m.tool_calls]
    tool_result_msgs = [m for m in sent if m.role == "tool_result"]
    assert len(tool_use_msgs) == 1
    assert tool_use_msgs[0].tool_calls == [
        {"id": "toolu_abc", "name": "herramienta_test", "inputs": {"x": 1}}
    ]
    assert len(tool_result_msgs) == 1
    assert tool_result_msgs[0].tool_call_id == "toolu_abc"


# ---------------------------------------------------------------------------
# HISTORY_LIMIT window trimming — no dangling tool_result (DT-001)
#
# _trim_to_user_boundary and _rows_to_llm_messages are pure functions of a row list,
# so these construct Message objects directly (never persisted) rather than going
# through db_session — no DB round-trip needed to exercise the trimming logic.
# ---------------------------------------------------------------------------

def _row(role: str, content: str = "", metadata: dict | None = None, sequence: int = 0) -> Message:
    return Message(
        conversation_id=uuid.uuid4(),
        role=role,
        content=content,
        metadata_=metadata or {},
        sequence=sequence,
    )


def test_trim_drops_dangling_tool_result_at_window_start():
    """A window cut mid-turn can start with a tool_result whose tool_use fell outside
    it (or even a lone tool_use with no result yet inside the window) — both are
    invalid as the first message Anthropic receives. Trimming to the next user row
    must drop them."""
    rows = [
        _row("tool_result", content="{}", metadata={"tool_call_id": "t0"}, sequence=5),
        _row("assistant", content="ya usó una tool", sequence=6),
        _row("user", content="segunda pregunta", sequence=7),
        _row("tool_use", content="", metadata={
            "tool_calls": [{"id": "t1", "name": "verificar_disponibilidad", "inputs": {}}]
        }, sequence=8),
        _row("tool_result", content="{}", metadata={"tool_call_id": "t1"}, sequence=9),
        _row("assistant", content="respuesta", sequence=10),
    ]

    trimmed = _trim_to_user_boundary(rows)

    assert trimmed[0].role == "user"
    assert trimmed[0].content == "segunda pregunta"

    llm_msgs = _rows_to_llm_messages(trimmed)
    assert llm_msgs[0].role == "user"
    # No dangling tool_result: every tool_result's tool_call_id has a preceding
    # assistant turn whose tool_calls contains a matching id.
    seen_call_ids: set[str] = set()
    for m in llm_msgs:
        if m.role == "assistant" and m.tool_calls:
            seen_call_ids.update(tc["id"] for tc in m.tool_calls)
        elif m.role == "tool_result":
            assert m.tool_call_id in seen_call_ids


def test_trim_returns_empty_when_no_user_row_in_window():
    rows = [
        _row("tool_use", metadata={"tool_calls": [{"id": "t0", "name": "x", "inputs": {}}]}, sequence=1),
        _row("tool_result", metadata={"tool_call_id": "t0"}, sequence=2),
    ]

    assert _trim_to_user_boundary(rows) == []


# ---------------------------------------------------------------------------
# System prompt — fecha actual inyectada (DT-005)
#
# _build_system_prompt is a pure function of (clinic, now), so these tests
# construct Clinic objects directly rather than going through db_session —
# no persistence needed to exercise the date-formatting logic.
# ---------------------------------------------------------------------------

def test_system_prompt_contains_frozen_current_date():
    clinic = Clinic(
        name="Clínica Test",
        whatsapp_phone_id="phone-fecha",
        timezone="America/Lima",
        config={},
    )
    frozen_now = datetime(2026, 7, 5, 9, 53, tzinfo=ZoneInfo("America/Lima"))

    prompt = _build_system_prompt(clinic, now=frozen_now)

    assert "Hoy es domingo 5 de julio de 2026, 09:53 (America/Lima)." in prompt
    assert 'nunca propongas fechas pasadas' in prompt


def test_system_prompt_date_changes_by_tenant_timezone():
    """Same instant, different clinic timezone -> different calendar date/time in the prompt."""
    clinic = Clinic(
        name="Clínica Madrid",
        whatsapp_phone_id="phone-madrid",
        timezone="Europe/Madrid",
        config={},
    )
    frozen_now = datetime(2026, 7, 5, 15, 30, tzinfo=ZoneInfo("Europe/Madrid"))

    prompt = _build_system_prompt(clinic, now=frozen_now)

    assert "Hoy es domingo 5 de julio de 2026, 15:30 (Europe/Madrid)." in prompt


def test_system_prompt_falls_back_to_utc_on_invalid_timezone():
    clinic = Clinic(
        name="Clínica Rota",
        whatsapp_phone_id="phone-rota",
        timezone="Nowhere/Fake",
        config={},
    )
    frozen_now = datetime(2026, 7, 5, 12, 0, tzinfo=timezone.utc)

    prompt = _build_system_prompt(clinic, now=frozen_now)

    assert "Hoy es domingo 5 de julio de 2026, 12:00 (Nowhere/Fake)." in prompt


# ---------------------------------------------------------------------------
# System prompt — ficha de la clínica (ubicación/contacto)
# ---------------------------------------------------------------------------

def test_system_prompt_contains_ficha_clinica_for_correct_tenant():
    clinic = Clinic(
        name="Clínica Test",
        whatsapp_phone_id="phone-ficha",
        timezone="America/Lima",
        address="Av. Siempre Viva 742, Lima",
        address_reference="Frente a la plaza principal",
        maps_url="https://maps.app.goo.gl/ejemplo",
        contact_phone="+51 1 111 2222",
        config={},
    )
    frozen_now = datetime(2026, 7, 5, 9, 53, tzinfo=ZoneInfo("America/Lima"))

    prompt = _build_system_prompt(clinic, now=frozen_now)

    assert "Ficha de la clínica:" in prompt
    assert "- Dirección: Av. Siempre Viva 742, Lima" in prompt
    assert "- Referencia: Frente a la plaza principal" in prompt
    assert "- Cómo llegar (Google Maps): https://maps.app.goo.gl/ejemplo" in prompt
    assert "- Teléfono de contacto: +51 1 111 2222" in prompt
    assert "Nunca inventes una dirección, un link de Maps, un teléfono o un horario" in prompt


def test_system_prompt_ficha_clinica_isolated_per_tenant():
    """Otro tenant con su propia dirección no debe filtrar datos de otra clínica."""
    clinic_a = Clinic(
        name="Clínica A", whatsapp_phone_id="phone-a",
        address="Calle A 123", config={},
    )
    clinic_b = Clinic(
        name="Clínica B", whatsapp_phone_id="phone-b",
        address="Calle B 456", config={},
    )

    prompt_a = _build_system_prompt(clinic_a)
    prompt_b = _build_system_prompt(clinic_b)

    assert "Calle A 123" in prompt_a
    assert "Calle B 456" not in prompt_a
    assert "Calle B 456" in prompt_b
    assert "Calle A 123" not in prompt_b


def test_system_prompt_ficha_clinica_sin_datos_de_ubicacion():
    """Sin ninguna dirección/telefono configurados, el bot debe admitirlo, no inventar."""
    clinic = Clinic(
        name="Clínica Sin Ubicación",
        whatsapp_phone_id="phone-sin-ubicacion",
        config={},
    )

    prompt = _build_system_prompt(clinic)

    assert "Ficha de la clínica:" in prompt
    assert "(Sin datos de ubicación configurados todavía.)" in prompt
    assert "Nunca inventes una dirección, un link de Maps, un teléfono o un horario" in prompt
    assert "dilo honestamente y ofrece derivar a un humano" in prompt
    assert "Horario de atención" not in prompt


# ---------------------------------------------------------------------------
# System prompt — ficha de la clínica: horario de atención (DT: "¿atienden
# domingos?" respondía "no tengo ese dato" con business_hours ya configurado)
# ---------------------------------------------------------------------------

def test_system_prompt_ficha_clinica_incluye_horario_agrupado_simple():
    clinic = Clinic(
        name="Clínica Test",
        whatsapp_phone_id="phone-horario-simple",
        config={},
        business_hours={
            "slot_duration_minutes": 30,
            "days": {
                "monday": [{"from": "09:00", "to": "19:00"}],
                "tuesday": [{"from": "09:00", "to": "19:00"}],
                "wednesday": [{"from": "09:00", "to": "19:00"}],
                "thursday": [{"from": "09:00", "to": "19:00"}],
                "friday": [{"from": "09:00", "to": "19:00"}],
                "saturday": [{"from": "09:00", "to": "13:00"}],
                "sunday": [{"from": "09:00", "to": "13:00"}],
            },
        },
    )

    prompt = _build_system_prompt(clinic)

    assert (
        "- Horario de atención: Lun-Vie 9:00 a.m.-7:00 p.m., Sáb-Dom 9:00 a.m.-1:00 p.m."
        in prompt
    )


def test_system_prompt_ficha_clinica_horario_con_franja_partida_y_dia_cerrado():
    """Misma forma real que la clínica de producción: franja partida por almuerzo
    de lunes a viernes, sábado solo mañana, domingo cerrado."""
    clinic = Clinic(
        name="Clínica Dental Demo",
        whatsapp_phone_id="phone-horario-real",
        config={},
        business_hours={
            "slot_duration_minutes": 30,
            "days": {
                "monday": [{"from": "09:00", "to": "13:00"}, {"from": "15:00", "to": "19:00"}],
                "tuesday": [{"from": "09:00", "to": "13:00"}, {"from": "15:00", "to": "19:00"}],
                "wednesday": [{"from": "09:00", "to": "13:00"}, {"from": "15:00", "to": "19:00"}],
                "thursday": [{"from": "09:00", "to": "13:00"}, {"from": "15:00", "to": "19:00"}],
                "friday": [{"from": "09:00", "to": "13:00"}, {"from": "15:00", "to": "19:00"}],
                "saturday": [{"from": "09:00", "to": "13:00"}],
                "sunday": [],
            },
        },
    )

    prompt = _build_system_prompt(clinic)

    assert "Lun-Vie 9:00 a.m.-1:00 p.m. y 3:00 p.m.-7:00 p.m." in prompt
    assert "Sáb 9:00 a.m.-1:00 p.m." in prompt
    assert "Dom Cerrado" in prompt


def test_system_prompt_ficha_clinica_sin_business_hours_horario_omitido():
    """Sin business_hours configurado, el bot debe admitirlo, no inventar un horario."""
    clinic = Clinic(
        name="Clínica Sin Horario",
        whatsapp_phone_id="phone-sin-horario",
        address="Av. Siempre Viva 742",
        config={},
    )

    prompt = _build_system_prompt(clinic)

    assert "Horario de atención" not in prompt
    assert "un teléfono o un horario que no esté aquí" in prompt


# ---------------------------------------------------------------------------
# System prompt — ficha del paciente (evita re-pedir datos ya conocidos)
# ---------------------------------------------------------------------------

def test_system_prompt_contains_ficha_paciente_when_lead_known():
    clinic = Clinic(name="Clínica Test", whatsapp_phone_id="phone-ficha-pac", config={})
    lead = Lead(whatsapp_number="5491100000000", name="Juan Pérez", source="whatsapp", status="new")

    prompt = _build_system_prompt(clinic, lead=lead, treatment_name="Limpieza dental")

    assert "Ficha del paciente:" in prompt
    assert "- Nombre: Juan Pérez" in prompt
    assert "- Tratamiento de interés: Limpieza dental" in prompt
    assert "no los vuelvas a pedir" in prompt


def test_system_prompt_ficha_paciente_sin_datos_todavia():
    clinic = Clinic(name="Clínica Test", whatsapp_phone_id="phone-ficha-pac-2", config={})
    lead = Lead(whatsapp_number="5491100000001", source="whatsapp", status="new")

    prompt = _build_system_prompt(clinic, lead=lead)

    assert "Ficha del paciente:" in prompt
    assert "(Sin datos guardados todavía para este número.)" in prompt


def test_system_prompt_omits_ficha_paciente_without_lead():
    """Backward-compat: llamadas sin lead (p.ej. las de ficha_clinica arriba) no cambian."""
    clinic = Clinic(name="Clínica Test", whatsapp_phone_id="phone-sin-lead", config={})

    prompt = _build_system_prompt(clinic)

    assert "Ficha del paciente:" not in prompt


# ---------------------------------------------------------------------------
# handle() end-to-end — la ficha del paciente evita que el bot re-pida el
# nombre cuando ya lo conoce para este número de WhatsApp.
# ---------------------------------------------------------------------------

async def test_handle_does_not_re_ask_name_when_lead_already_known(db_session, clinic):
    lead = Lead(
        tenant_id=clinic.id,
        whatsapp_number="5491155551234",
        name="Juan Pérez",
        source="whatsapp",
        status="new",
    )
    db_session.add(lead)
    await db_session.flush()

    provider = SequencedFakeLLMProvider([_end_turn_response("¿Para qué fecha te gustaría la cita?")])

    await handle(_msg(text="Quisiera agendar una cita"), db_session, FakeMessagingProvider(), provider)

    system_prompt = provider.calls[0]["system_prompt"]
    assert "Ficha del paciente:" in system_prompt
    assert "- Nombre: Juan Pérez" in system_prompt


async def test_handle_ficha_paciente_sin_datos_para_lead_nuevo(db_session, clinic):
    provider = SequencedFakeLLMProvider([_end_turn_response("¿Cuál es tu nombre completo?")])

    await handle(_msg(text="Quisiera agendar una cita"), db_session, FakeMessagingProvider(), provider)

    system_prompt = provider.calls[0]["system_prompt"]
    assert "Ficha del paciente:" in system_prompt
    assert "(Sin datos guardados todavía para este número.)" in system_prompt


# ---------------------------------------------------------------------------
# handle() end-to-end — horario de atención respondido desde la ficha
# ---------------------------------------------------------------------------

async def test_handle_pregunta_de_horario_llega_a_la_ficha_sin_tool(db_session):
    clinic_con_horario = Clinic(
        name="Clínica Dental Demo",
        whatsapp_phone_id="phone-horario-e2e",
        config={},
        business_hours={
            "slot_duration_minutes": 30,
            "days": {
                "monday": [{"from": "09:00", "to": "13:00"}, {"from": "15:00", "to": "19:00"}],
                "tuesday": [{"from": "09:00", "to": "13:00"}, {"from": "15:00", "to": "19:00"}],
                "wednesday": [{"from": "09:00", "to": "13:00"}, {"from": "15:00", "to": "19:00"}],
                "thursday": [{"from": "09:00", "to": "13:00"}, {"from": "15:00", "to": "19:00"}],
                "friday": [{"from": "09:00", "to": "13:00"}, {"from": "15:00", "to": "19:00"}],
                "saturday": [{"from": "09:00", "to": "13:00"}],
                "sunday": [],
            },
        },
    )
    db_session.add(clinic_con_horario)
    await db_session.flush()

    provider = SequencedFakeLLMProvider(
        [_end_turn_response("No, los domingos no atendemos. ¡Pero sí de lunes a sábado!")]
    )

    await handle(
        _msg(phone_id="phone-horario-e2e", text="¿atienden los domingos?"),
        db_session, FakeMessagingProvider(), provider,
    )

    system_prompt = provider.calls[0]["system_prompt"]
    assert "- Horario de atención:" in system_prompt
    assert "Dom Cerrado" in system_prompt
    assert "sin usar herramientas" in system_prompt
