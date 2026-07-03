"""Tests for conversation.handle()."""
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.core.exceptions import TenantNotFoundError
from app.core.providers import InboundMessage
from app.models.clinic import Clinic
from app.models.conversation import Conversation
from app.models.lead import Lead
from app.models.message import Message
from app.services.conversation import HISTORY_LIMIT, LLM_FALLBACK_MESSAGE, handle
from tests.fakes import FailingLLMProvider, FakeLLMProvider, FakeMessagingProvider, SpyFakeLLMProvider


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
    await handle(_msg(), db_session, FakeMessagingProvider(), FakeLLMProvider())
    await handle(_msg(), db_session, FakeMessagingProvider(), FakeLLMProvider())

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
    await handle(_msg(), db_session, FakeMessagingProvider(), FakeLLMProvider())
    await handle(_msg(), db_session, FakeMessagingProvider(), FakeLLMProvider())

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
    """History rows with distinct timestamps reach the LLM oldest-first; inbound is last.

    Messages inserted in the same flush share an identical SQLite timestamp, making
    same-flush ordering undefined. We seed prior messages with explicit timestamps
    so the ordering assertion is deterministic.
    """
    spy = SpyFakeLLMProvider()
    messaging = FakeMessagingProvider()

    # Bootstrap to create lead + conversation, then seed history with explicit timestamps.
    await handle(_msg(text="bootstrap"), db_session, messaging, FakeLLMProvider())
    conv = (await db_session.execute(
        select(Conversation).where(Conversation.tenant_id == clinic.id)
    )).scalars().first()

    t1 = datetime(2020, 1, 1, 0, 0, tzinfo=timezone.utc)
    t2 = datetime(2020, 1, 1, 1, 0, tzinfo=timezone.utc)
    db_session.add(Message(conversation_id=conv.id, role="user", content="primera pregunta",
                           metadata_={}, created_at=t1, updated_at=t1))
    db_session.add(Message(conversation_id=conv.id, role="assistant", content="primera respuesta",
                           metadata_={}, created_at=t2, updated_at=t2))
    await db_session.flush()

    await handle(_msg(text="segunda pregunta"), db_session, messaging, spy)

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


async def test_history_limited_to_history_limit(db_session, clinic):
    """The LLM receives at most HISTORY_LIMIT historical rows, plus the new inbound."""
    spy = SpyFakeLLMProvider()
    messaging = FakeMessagingProvider()

    # Bootstrap lead + conversation via a normal call
    await handle(_msg(text="bootstrap"), db_session, messaging, FakeLLMProvider())
    conv = (await db_session.execute(
        select(Conversation).where(Conversation.tenant_id == clinic.id)
    )).scalars().first()

    # Seed more than HISTORY_LIMIT messages with explicit future timestamps so their
    # ordering is deterministic and they rank as the most recent rows in the query.
    base = datetime(2030, 1, 1, tzinfo=timezone.utc)
    for i in range(HISTORY_LIMIT + 5):
        db_session.add(Message(
            conversation_id=conv.id,
            role="user",
            content=f"old msg {i}",
            metadata_={},
            created_at=base + timedelta(minutes=i),
            updated_at=base + timedelta(minutes=i),
        ))
    await db_session.flush()

    await handle(_msg(text="nuevo"), db_session, messaging, spy)

    # HISTORY_LIMIT historical rows fetched + 1 inbound appended
    assert len(spy.calls[0]) == HISTORY_LIMIT + 1
    assert spy.calls[0][-1].content == "nuevo"
