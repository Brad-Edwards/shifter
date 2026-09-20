"""Management-owned principal lifecycle and fail-closed provider binding."""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.db import IntegrityError, transaction

from shared.audit import AuditAction, AuditEntityType, AuditEvent, audit_log
from shared.identity_scope import PrincipalRef

from .models import Principal, PrincipalConflictError, ProviderBinding, UserProfile

if TYPE_CHECKING:
    from django.contrib.auth.models import User


def _audit(entity_type: str, entity_id: int, action: str, state: dict[str, object]) -> None:
    audit_log(
        AuditEvent(
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            new_state=state,
            context="principal_identity",
        ),
        strict=True,
    )


def _principal_ref(principal: Principal) -> PrincipalRef:
    if principal.kind == Principal.Kind.HUMAN:
        return PrincipalRef(uuid=principal.uuid, kind="human")
    if principal.kind == Principal.Kind.SERVICE:
        return PrincipalRef(uuid=principal.uuid, kind="service")
    raise PrincipalConflictError("Principal identity conflict")


def ensure_human_principal(user: User) -> PrincipalRef:
    """Create one durable principal for a Django user, without any grant."""
    if user is None or user.pk is None:
        raise PrincipalConflictError("A persisted user is required")
    with transaction.atomic():
        principal, created = Principal.objects.get_or_create(user=user, defaults={"kind": Principal.Kind.HUMAN})
        if principal.kind != Principal.Kind.HUMAN:
            raise PrincipalConflictError("Principal identity conflict")
        if created:
            _audit(AuditEntityType.PRINCIPAL, principal.pk, AuditAction.CREATE, {"kind": principal.kind})
        return _principal_ref(principal)


def principal_for_user(user: User) -> PrincipalRef:
    """Resolve a pre-existing human identity without creating one on a read."""
    if user is None or user.pk is None:
        raise PrincipalConflictError("Principal unavailable")
    principal = Principal.objects.filter(user=user, kind=Principal.Kind.HUMAN).first()
    if principal is None:
        raise PrincipalConflictError("Principal unavailable")
    return _principal_ref(principal)


def create_service_principal(
    name: str, *, created_by: User | None = None, responsible_user: User | None = None
) -> PrincipalRef:
    """Create an independent service identity; contacts convey no authority."""
    if not isinstance(name, str) or not name.strip() or len(name) > 200:
        raise PrincipalConflictError("Invalid service principal")
    if any(user is not None and user.pk is None for user in (created_by, responsible_user)):
        raise PrincipalConflictError("Contact must be persisted")
    with transaction.atomic():
        principal = Principal.objects.create(
            kind=Principal.Kind.SERVICE,
            name=name.strip(),
            created_by=created_by,
            responsible_user=responsible_user,
        )
        _audit(AuditEntityType.PRINCIPAL, principal.pk, AuditAction.CREATE, {"kind": principal.kind})
        return _principal_ref(principal)


def set_service_contact(principal_ref: PrincipalRef, contact: User | None) -> None:
    """Change the responsible contact without re-keying or reauthorizing a service."""
    if not isinstance(principal_ref, PrincipalRef) or (contact is not None and contact.pk is None):
        raise PrincipalConflictError("Principal unavailable")
    with transaction.atomic():
        principal = Principal.objects.select_for_update().filter(uuid=principal_ref.uuid).first()
        if principal is None or principal.kind != Principal.Kind.SERVICE or principal_ref.kind != "service":
            raise PrincipalConflictError("Principal unavailable")
        if principal.responsible_user_id != (contact.pk if contact is not None else None):
            principal.responsible_user = contact
            principal.save(update_fields=["responsible_user", "updated_at"])
            _audit(AuditEntityType.PRINCIPAL, principal.pk, AuditAction.UPDATE, {"contact_changed": True})


def bind_principal_provider_identity(principal_ref: PrincipalRef, issuer: str, subject: str) -> None:
    """Bind an exact provider tuple once; never repair by email or subject alone."""
    if (
        not isinstance(principal_ref, PrincipalRef)
        or not isinstance(issuer, str)
        or not issuer.strip()
        or len(issuer) > 255
        or not isinstance(subject, str)
        or not subject.strip()
        or len(subject) > 255
    ):
        raise PrincipalConflictError("Provider identity unavailable")
    try:
        with transaction.atomic():
            principal = Principal.objects.select_for_update().filter(uuid=principal_ref.uuid).first()
            if principal is None or principal.kind != principal_ref.kind:
                raise PrincipalConflictError("Principal unavailable")
            legacy = UserProfile.objects.filter(cognito_sub=subject).first()
            if (
                legacy is not None
                and (legacy.issuer == "" or legacy.issuer == issuer)
                and legacy.user_id != principal.user_id
            ):
                raise PrincipalConflictError("Provider identity unavailable")
            existing = ProviderBinding.objects.filter(issuer=issuer, subject=subject).first()
            if existing is not None:
                if existing.principal_id != principal.id:
                    raise PrincipalConflictError("Provider identity unavailable")
                return
            binding = ProviderBinding.objects.create(principal=principal, issuer=issuer, subject=subject)
            _audit(AuditEntityType.PROVIDER_BINDING, binding.pk, AuditAction.CREATE, {"principal_id": principal.pk})
    except IntegrityError as exc:
        raise PrincipalConflictError("Provider identity unavailable") from exc


__all__ = [
    "PrincipalConflictError",
    "bind_principal_provider_identity",
    "create_service_principal",
    "ensure_human_principal",
    "principal_for_user",
    "set_service_contact",
]
