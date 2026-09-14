"""Public-registration enums split from :mod:`ctf.enums` for file-size bounds."""

from enum import StrEnum


class PublicRegistrationDisposition(StrEnum):
    """Organizer-controlled lifecycle of one untrusted public intake row."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"

    def __str__(self) -> str:
        """Return the string value for database storage."""
        return self.value

    @classmethod
    def choices(cls) -> list[tuple[str, str]]:
        """Return choices for Django model fields and serializers."""
        return [(status.value, status.name.replace("_", " ").title()) for status in cls]
