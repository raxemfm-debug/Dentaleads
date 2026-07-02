import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.core.types import UUID
from app.models.base import TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.clinic import Clinic
    from app.models.lead import Lead
    from app.models.treatment import Treatment


# Estados que LIBERAN el slot. Fuente de verdad única: la usan
# availability.py y el índice parcial uq_appointments_tenant_slot.
SLOT_FREEING_STATUSES = ("cancelled", "no_show")

_SLOT_WHERE = text(
    "status NOT IN ({})".format(
        ", ".join(f"'{s}'" for s in SLOT_FREEING_STATUSES)
    )
)


class Appointment(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "appointments"
    __table_args__ = (
        Index(
            "uq_appointments_tenant_slot",
            "tenant_id",
            "scheduled_at",
            unique=True,
            postgresql_where=_SLOT_WHERE,
            sqlite_where=_SLOT_WHERE,
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clinics.id", ondelete="CASCADE"), nullable=False, index=True
    )
    lead_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("leads.id", ondelete="CASCADE"), nullable=False, index=True
    )
    treatment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("treatments.id", ondelete="SET NULL")
    )
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String(50), default="confirmed", nullable=False, index=True
    )  # confirmed | reminded | completed | cancelled | no_show
    reminder_24h_sent: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    reminder_2h_sent: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    clinic: Mapped["Clinic"] = relationship("Clinic", back_populates="appointments")
    lead: Mapped["Lead"] = relationship("Lead", back_populates="appointments")
    treatment: Mapped["Treatment | None"] = relationship("Treatment", back_populates="appointments")
