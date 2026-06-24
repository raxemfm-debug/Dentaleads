import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.core.types import UUID
from app.models.base import TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.clinic import Clinic
    from app.models.treatment import Treatment
    from app.models.conversation import Conversation
    from app.models.appointment import Appointment


class Lead(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "leads"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clinics.id", ondelete="CASCADE"), nullable=False, index=True
    )
    whatsapp_number: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    name: Mapped[str | None] = mapped_column(String(255))
    interested_treatment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("treatments.id", ondelete="SET NULL")
    )
    source: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(50), default="new", nullable=False, index=True)
    notes: Mapped[str | None] = mapped_column(Text)
    consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    clinic: Mapped["Clinic"] = relationship("Clinic", back_populates="leads")
    interested_treatment: Mapped["Treatment | None"] = relationship("Treatment", back_populates="leads")
    conversations: Mapped[list["Conversation"]] = relationship("Conversation", back_populates="lead")
    appointments: Mapped[list["Appointment"]] = relationship("Appointment", back_populates="lead")
