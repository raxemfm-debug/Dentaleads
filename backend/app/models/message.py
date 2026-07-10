import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.core.types import JSONB, UUID
from app.models.base import TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.conversation import Conversation


class Message(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "messages"
    __table_args__ = (
        # Idempotency guard (DT-004): a WhatsApp message id (wamid) must be persisted
        # at most once, so a Meta webhook retry for a message we already processed
        # can be detected before re-running the LLM and duplicating the exchange.
        # Only inbound (role="user") rows carry a wamid; assistant rows leave it NULL,
        # and NULL is not compared equal to NULL by a unique index, so those never collide.
        Index("uq_messages_wamid", "wamid", unique=True),
        # DT-001: history reconstruction orders by `sequence`, not `created_at`. Postgres's
        # now()/CURRENT_TIMESTAMP is stable within a transaction, so every row persisted by
        # the same handle() call (tool_use + tool_result(s) + final assistant reply) shares
        # an identical created_at — ordering by it alone leaves same-call rows in undefined
        # relative order, which can send Anthropic a tool_result with no preceding tool_use.
        # DT-006: unique (not just indexed) as of migration a9b593a43546. sequence was
        # assigned in Python as max()+1 with no locking, so two webhooks racing on the same
        # conversation could both read the same max and both insert the same number — the
        # conversation.py fix (with_for_update in _get_or_create_conversation) is the primary
        # guard; this constraint is the DB-level backstop that vetoes it outright if that
        # guard is ever bypassed. Enforced on the SQLite test engine too, since tests build
        # schema via Base.metadata.create_all() rather than Alembic.
        Index("uq_messages_conversation_id_sequence", "conversation_id", "sequence", unique=True),
    )

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Monotonic per-conversation counter assigned in application code (not a DB identity/
    # autoincrement column) so it behaves identically on Postgres and the SQLite test engine.
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    # user | assistant | tool_use | tool_result
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    wamid: Mapped[str | None] = mapped_column(String(100), nullable=True)
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict, nullable=False)

    conversation: Mapped["Conversation"] = relationship("Conversation", back_populates="messages")
