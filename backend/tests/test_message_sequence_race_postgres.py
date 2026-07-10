"""
Real-Postgres concurrency test for uq_messages_conversation_id_sequence (DT-006).

sequence is assigned in application code (Python max()+1), not a DB identity column,
so the only way to prove two webhooks racing the same conversation can no longer
collide — and that their turns don't interleave in the reply sent back to the patient
— is a genuine two-connection race. SQLite has no real concurrent-writer story, so
this is only observable against a real Postgres server (same rationale as
test_appointment_race_postgres.py for uq_appointments_tenant_slot).

Requires TEST_POSTGRES_URL (async DSN, e.g.
postgresql+asyncpg://dentalbot:dentalbot@localhost:5432/dentalbot_test)
pointing at a database with migrations already applied (`alembic upgrade
head`, which must include a9b593a43546). Skipped entirely when unset, so the
regular suite (SQLite, no real Postgres needed) is unaffected.

Point TEST_POSTGRES_URL at a dedicated test database, not the dev one — this
test also cleans up every row it creates (messages, conversation, lead, clinic)
in a `finally` block regardless, so it never leaves residue behind either way.
"""
import asyncio
import os
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.providers import InboundMessage, LLMProvider, LLMResponse
from app.models.clinic import Clinic
from app.models.conversation import Conversation
from app.models.lead import Lead
from app.models.message import Message
from app.services.conversation import handle
from tests.fakes import FakeMessagingProvider

TEST_POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL")

pytestmark = pytest.mark.skipif(
    not TEST_POSTGRES_URL,
    reason=(
        "set TEST_POSTGRES_URL (async DSN, migrations already applied) to run "
        "the real Postgres message-sequence race test"
    ),
)

GATHER_TIMEOUT_SECONDS = 10  # a deadlock (lock-ordering bug) would hang past this instead of failing fast


class _FixedReplyLLMProvider(LLMProvider):
    """Returns a fixed, caller-chosen reply with no tool calls — lets the test tell the
    two racers' turns apart in the persisted history without depending on timing.

    complete() sleeps briefly before returning to stand in for real Anthropic API
    latency (typically ~1-3s in production — see DT-006's actual incident, where two
    inbound messages 5s apart still overlapped). Without this, a zero-latency fake
    LLM lets the first racer sail through its entire handle() call — DB reads, fake
    completion, persistence, commit — before the second racer even starts reading
    conversation history, so the two calls never actually overlap and the test would
    pass even with the with_for_update() lock removed (verified while writing this
    test). The sleep restores the real window during which the race — and the fix —
    are actually exercised.
    """

    def __init__(self, reply: str) -> None:
        self._reply = reply

    async def complete(self, system_prompt, messages, tools=None, max_tokens=1024) -> LLMResponse:
        await asyncio.sleep(0.15)
        return LLMResponse(content=self._reply, tool_calls=[], stop_reason="end_turn", usage={})

    async def classify(self, text, categories, system_prompt=None) -> str:
        return categories[0]


@pytest_asyncio.fixture
async def pg_session_factory():
    engine = create_async_engine(TEST_POSTGRES_URL, pool_size=5)
    yield async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    await engine.dispose()


async def _handle_one(session_factory, tenant_phone_id, from_number, wamid, text, reply) -> None:
    """Opens its own session (and thus its own DB connection) and runs one full
    handle() call, mirroring what a separate webhook request does via get_db()."""
    async with session_factory() as session:
        msg = InboundMessage(
            tenant_phone_id=tenant_phone_id,
            from_number=from_number,
            message_id=wamid,
            text=text,
            message_type="text",
            raw_payload={},
        )
        await handle(msg, session, FakeMessagingProvider(), _FixedReplyLLMProvider(reply))
        await session.commit()


async def test_concurrent_messages_same_conversation_no_duplicate_sequence(pg_session_factory):
    phone_id = f"race-{uuid.uuid4()}"
    from_number = "+2000000001"

    async with pg_session_factory() as setup:
        clinic = Clinic(
            name="Race Test Clinic",
            whatsapp_phone_id=phone_id,
            timezone="UTC",
            business_hours={},
            config={},
        )
        setup.add(clinic)
        await setup.flush()
        lead = Lead(tenant_id=clinic.id, whatsapp_number=from_number, status="new")
        setup.add(lead)
        await setup.flush()
        # Pre-existing "bot" conversation so both racers hit _get_or_create_conversation's
        # find branch (the one with_for_update() guards) — matches the real incident,
        # where the conversation already existed before the two colliding webhooks arrived.
        conv = Conversation(tenant_id=clinic.id, lead_id=lead.id, channel="whatsapp", status="bot")
        setup.add(conv)
        await setup.flush()
        clinic_id, lead_id, conv_id = clinic.id, lead.id, conv.id
        await setup.commit()

    try:
        # Two independent sessions/connections, two distinct inbound messages for the
        # same conversation, racing concurrently instead of sequentially.
        await asyncio.wait_for(
            asyncio.gather(
                _handle_one(pg_session_factory, phone_id, from_number, "wamid-race-a", "Mensaje A", "Respuesta A"),
                _handle_one(pg_session_factory, phone_id, from_number, "wamid-race-b", "Mensaje B", "Respuesta B"),
            ),
            timeout=GATHER_TIMEOUT_SECONDS,
        )

        async with pg_session_factory() as check:
            rows = (
                await check.execute(
                    select(Message)
                    .where(Message.conversation_id == conv_id)
                    .order_by(Message.sequence.asc())
                )
            ).scalars().all()

            sequences = [r.sequence for r in rows]
            assert sequences == sorted(set(sequences)), f"duplicate sequence(s) found: {sequences}"
            assert len(rows) == 4, f"expected 2 user + 2 assistant rows, got {len(rows)}"

            # Non-interleaving: each turn's (user, assistant) pair must be contiguous —
            # a user row immediately followed by *its own* assistant reply, not spliced
            # with the other racer's rows.
            expected_reply = {"wamid-race-a": "Respuesta A", "wamid-race-b": "Respuesta B"}
            for i in (0, 2):
                user_row, assistant_row = rows[i], rows[i + 1]
                assert user_row.role == "user"
                assert assistant_row.role == "assistant"
                assert assistant_row.content == expected_reply[user_row.wamid]
            assert rows[0].wamid != rows[2].wamid
    finally:
        async with pg_session_factory() as cleanup:
            await cleanup.execute(delete(Message).where(Message.conversation_id == conv_id))
            await cleanup.execute(delete(Conversation).where(Conversation.id == conv_id))
            await cleanup.execute(delete(Lead).where(Lead.tenant_id == clinic_id))
            await cleanup.execute(delete(Clinic).where(Clinic.id == clinic_id))
            await cleanup.commit()
