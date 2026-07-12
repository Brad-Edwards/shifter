"""Shared field-level encryption primitives for application secrets."""

from __future__ import annotations

from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import models

ENCRYPTED_VALUE_PREFIX = "enc:v1:"


def _fernet() -> Fernet:
    key = settings.FIELD_ENCRYPTION_KEY
    if not key:
        raise ImproperlyConfigured("FIELD_ENCRYPTION_KEY is not set")
    return Fernet(key.encode("utf-8"))


def encrypt_value(value: str) -> str:
    """Encrypt a non-empty plaintext value idempotently."""
    if not value or value.startswith(ENCRYPTED_VALUE_PREFIX):
        return value
    encrypted = _fernet().encrypt(value.encode("utf-8")).decode("utf-8")
    return f"{ENCRYPTED_VALUE_PREFIX}{encrypted}"


def decrypt_value(value: str) -> str:
    """Decrypt a prefixed value idempotently."""
    if not value.startswith(ENCRYPTED_VALUE_PREFIX):
        return value
    token = value.removeprefix(ENCRYPTED_VALUE_PREFIX)
    try:
        return _fernet().decrypt(token.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("Encrypted field contains an invalid encrypted value") from exc


def _transform_sensitive(data: dict[str, Any], keys: frozenset[str], *, encrypt: bool) -> dict[str, Any]:
    transformed = data.copy()
    for key in keys:
        value = transformed.get(key)
        if not isinstance(value, str) or value == "":
            continue
        transformed[key] = encrypt_value(value) if encrypt else decrypt_value(value)
    return transformed


class EncryptedStringField(models.TextField):
    """Text field encrypted at rest with the configured Fernet key."""

    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        return encrypt_value(value) if isinstance(value, str) else value

    def from_db_value(self, value, expression, connection):
        return decrypt_value(value) if isinstance(value, str) else value

    def to_python(self, value):
        value = super().to_python(value)
        return decrypt_value(value) if isinstance(value, str) else value

    def deconstruct(self):
        name, _path, args, kwargs = super().deconstruct()
        return name, "django.db.models.TextField", args, kwargs


class EncryptedJSONField(models.JSONField):
    """JSONField encrypting string values under declared sensitive keys."""

    sensitive_keys: frozenset[str] = frozenset()

    def get_prep_value(self, value):
        if isinstance(value, dict):
            value = _transform_sensitive(value, self.sensitive_keys, encrypt=True)
        return super().get_prep_value(value)

    def from_db_value(self, value, expression, connection):
        value = super().from_db_value(value, expression, connection)
        return _transform_sensitive(value, self.sensitive_keys, encrypt=False) if isinstance(value, dict) else value

    def to_python(self, value):
        value = super().to_python(value)
        return _transform_sensitive(value, self.sensitive_keys, encrypt=False) if isinstance(value, dict) else value

    def deconstruct(self):
        name, _path, args, kwargs = super().deconstruct()
        return name, "django.db.models.JSONField", args, kwargs
