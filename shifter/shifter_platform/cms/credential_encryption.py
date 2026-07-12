"""Field-level encryption for sensitive values inside JSON model fields.

Defense in depth (#693): selectively encrypts known sensitive keys within
JSONField blobs at write time, transparently decrypts on read. Non-sensitive
operational keys (names, IDs, roles, regions) remain queryable and visible.

The base ``EncryptedJSONField`` is generic over a frozenset of sensitive keys.
Concrete subclasses bind a key set appropriate to the model they decorate.
``deconstruct()`` masquerades as plain ``JSONField`` so swapping in needs no
schema migration; existing rows are re-encrypted by a data migration that
re-saves each row.
"""

from __future__ import annotations

from shared.field_encryption import ENCRYPTED_VALUE_PREFIX, EncryptedJSONField  # noqa: F401
from shared.field_encryption import _transform_sensitive as _transform_sensitive

# Compatibility aliases for existing tests and migrations.
from shared.field_encryption import decrypt_value as _decrypt_value  # noqa: F401
from shared.field_encryption import encrypt_value as _encrypt_value  # noqa: F401


class EncryptedCredentialDataField(EncryptedJSONField):
    """Credential.data: encrypts secret values in credential payloads.

    Keys mirror the fields flagged secret on the Credential Pydantic specs
    (``cyberscript.schemas.credentials.SCMCredentialSpec.scm_pin_value``,
    ``DeploymentProfileSpec.authcode``). Operational fields (folder name,
    PIN ID, region, profile name) stay plaintext.
    """

    sensitive_keys = frozenset({"authcode", "scm_pin_value"})


class EncryptedInstanceDataField(EncryptedJSONField):
    """Instance.data: encrypts secret values bled into the JSON blob.

    For non-NGFW instances this is effectively a no-op because the spec
    contains no secret-shaped keys. For NGFW instances,
    ``cms.services.create_ngfw`` persists the hydrated
    ``cyberscript.schemas.app.NGFWAppSpec`` directly into ``instance.data``,
    which can include ``authcode``, ``scm_pin_value``, and ``otp_value``.
    Those three keys are encrypted at rest here.
    """

    sensitive_keys = frozenset({"authcode", "scm_pin_value", "otp_value"})
