import uuid
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.core.types import UUID
from app.models.base import TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.clinic import Clinic
    from app.models.lead import Lead
    from app.models.appointment import Appointment


class Treatment(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "treatments"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clinics.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    duration_minutes: Mapped[int | None] = mapped_column(Integer)
    price_from: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    requires_consult: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    clinic: Mapped["Clinic"] = relationship("Clinic", back_populates="treatments")
    leads: Mapped[list["Lead"]] = relationship("Lead", back_populates="interested_treatment")
    appointments: Mapped[list["Appointment"]] = relationship("Appointment", back_populates="treatment")
