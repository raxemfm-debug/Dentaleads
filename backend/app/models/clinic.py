import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.core.types import JSONB, UUID
from app.models.base import TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.clinic_user import ClinicUser
    from app.models.treatment import Treatment
    from app.models.lead import Lead
    from app.models.appointment import Appointment


class Clinic(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "clinics"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    whatsapp_phone_id: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    timezone: Mapped[str] = mapped_column(String(50), default="UTC", nullable=False)
    address: Mapped[str | None] = mapped_column(Text)
    address_reference: Mapped[str | None] = mapped_column(Text)
    maps_url: Mapped[str | None] = mapped_column(String(500))
    contact_phone: Mapped[str | None] = mapped_column(String(50))
    business_hours: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    config: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    subscription_status: Mapped[str] = mapped_column(
        String(50), default="trial", nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    users: Mapped[list["ClinicUser"]] = relationship("ClinicUser", back_populates="clinic")
    treatments: Mapped[list["Treatment"]] = relationship("Treatment", back_populates="clinic")
    leads: Mapped[list["Lead"]] = relationship("Lead", back_populates="clinic")
    appointments: Mapped[list["Appointment"]] = relationship("Appointment", back_populates="clinic")
