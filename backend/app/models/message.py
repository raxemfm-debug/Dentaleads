import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Index, String, Text
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
    )

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # user | assistant | system
    content: Mapped[str] = mapped_column(Text, nullable=False)
    wamid: Mapped[str | None] = mapped_column(String(100), nullable=True)
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict, nullable=False)

    conversation: Mapped["Conversation"] = relationship("Conversation", back_populates="messages")
