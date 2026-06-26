"""Application-level exceptions."""


class TenantNotFoundError(Exception):
    """Raised when no clinic is registered for the given WhatsApp phone_number_id."""

    def __init__(self, phone_id: str) -> None:
        super().__init__(f"No tenant found for phone_number_id={phone_id!r}")
        self.phone_id = phone_id
