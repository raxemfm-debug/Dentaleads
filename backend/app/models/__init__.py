# Import all models so SQLAlchemy registers them before Alembic autogenerate runs
from app.models.clinic import Clinic
from app.models.clinic_user import ClinicUser
from app.models.treatment import Treatment
from app.models.lead import Lead
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.appointment import Appointment

__all__ = [
    "Clinic",
    "ClinicUser",
    "Treatment",
    "Lead",
    "Conversation",
    "Message",
    "Appointment",
]
