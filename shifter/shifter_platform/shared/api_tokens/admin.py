"""Django-admin management surface for platform API tokens (PLAT-102).

Generation and revocation of API tokens is a browser-session admin operation
(staff/superuser, CSRF-protected). The raw token is shown exactly once at
creation, rendered server-side in the add-success response, and is never stored,
logged, listed, searchable, or routed through the messages/cookie framework.
"""

from __future__ import annotations

from datetime import timedelta

from django import forms
from django.conf import settings
from django.contrib import admin, messages
from django.http import HttpResponse
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escape

from shared.api_tokens.audit import TokenEvent, record_token_event
from shared.api_tokens.models import ApiToken
from shared.api_tokens.scopes import InvalidScopeError, validate_scopes

_RAW_TOKEN_REQUEST_ATTR = "_api_tokens_created_raw"  # nosec B105 - request attribute name, not a credential
_DEFAULT_MAX_TTL_DAYS = 365


def _max_ttl_days() -> int:
    return getattr(settings, "API_TOKEN_MAX_TTL_DAYS", _DEFAULT_MAX_TTL_DAYS)


class ApiTokenForm(forms.ModelForm):
    """Create/edit form for API tokens.

    Used for BOTH add and change so the scope and bounded-lifetime validation
    runs on every admin mutation path (not just creation). ``token_id`` and
    ``verifier_hash`` are server-generated and never entered by hand.
    """

    class Meta:
        model = ApiToken
        fields = ["name", "scopes", "expires_at"]

    def clean_scopes(self):
        try:
            return validate_scopes(self.cleaned_data["scopes"] or [])
        except InvalidScopeError as exc:
            raise forms.ValidationError(str(exc)) from exc

    def clean_expires_at(self):
        """Bound token lifetime to the configured maximum on every path.

        Tokens default to expiring at the ceiling rather than never, so every
        token has a finite lifetime; an explicit expiry beyond the ceiling is
        rejected. Enforced on add AND change so a later edit cannot blank or
        extend ``expires_at`` past the cap.
        """
        max_days = _max_ttl_days()
        ceiling = timezone.now() + timedelta(days=max_days)
        expires_at = self.cleaned_data.get("expires_at")
        if expires_at is None:
            return ceiling
        if expires_at > ceiling:
            raise forms.ValidationError(f"Expiry may be at most {max_days} days from now.")
        return expires_at


@admin.register(ApiToken)
class ApiTokenAdmin(admin.ModelAdmin):
    form = ApiTokenForm
    list_display = (
        "name",
        "display_id",
        "scopes",
        "created_by",
        "created_at",
        "last_used_at",
        "expires_at",
        "revoked_at",
        "is_active",
    )
    search_fields = ("name", "token_id")
    list_filter = ("revoked_at", "expires_at")
    ordering = ("-created_at",)
    actions = ("revoke_tokens",)
    # token_id / verifier_hash are server-generated and immutable once set.
    readonly_fields = ("token_id", "verifier_hash", "created_by", "created_at", "last_used_at", "revoked_at")

    @admin.display(boolean=True, description="Active")
    def is_active(self, obj: ApiToken) -> bool:
        return obj.is_active

    def has_delete_permission(self, request, obj=None):
        """Disable hard delete for a credential principal.

        Tokens are retired via the audited ``revoke_tokens`` action (which sets
        ``revoked_at`` and writes an audit row), never hard-deleted. Returning
        False also removes Django's default "delete selected" bulk action, so
        there is no unaudited path that erases lifecycle/revocation state.
        """
        return False

    def save_model(self, request, obj, form, change):
        if change:
            super().save_model(request, obj, form, change)
            return
        token, raw = ApiToken.create_token(
            name=obj.name,
            created_by=request.user,
            scopes=obj.scopes,
            expires_at=obj.expires_at,
        )
        # Reflect the persisted instance back so the admin redirect/log are correct.
        obj.pk = token.pk
        obj.token_id = token.token_id
        obj.verifier_hash = token.verifier_hash
        obj.created_by = token.created_by
        obj.created_at = token.created_at
        setattr(request, _RAW_TOKEN_REQUEST_ATTR, raw)
        record_token_event(
            TokenEvent.CREATED,
            request=request,
            token_id=token.token_id,
            token_pk=token.pk,
            actor_id=request.user.id,
        )

    def response_add(self, request, obj, post_url_continue=None):
        raw = getattr(request, _RAW_TOKEN_REQUEST_ATTR, None)
        if not raw:
            return super().response_add(request, obj, post_url_continue)
        # Render the raw bearer token once, server-side. Deliberately NOT via the
        # messages framework: with cookie-backed message storage that would
        # serialize the secret into a client cookie and round-trip it on the next
        # request, exposing it to proxy/APM/access-log tooling.
        changelist = reverse("admin:shared_apitoken_changelist")
        body = (
            "<!DOCTYPE html><html><head><meta charset='utf-8'>"
            "<title>API token created</title></head><body>"
            "<h1>API token created</h1>"
            "<p>Copy this token now. It will not be shown again.</p>"
            f"<pre style='font-size:1.1em;white-space:pre-wrap'>{escape(raw)}</pre>"
            f"<p><a href='{escape(changelist)}'>Return to API tokens</a></p>"
            "</body></html>"
        )
        return HttpResponse(body)

    @admin.action(description="Revoke selected API tokens")
    def revoke_tokens(self, request, queryset):
        revoked = 0
        for token in queryset:
            if token.revoked_at is not None:
                continue
            token.revoked_at = timezone.now()
            token.save(update_fields=["revoked_at"])
            record_token_event(
                TokenEvent.REVOKED,
                request=request,
                token_id=token.token_id,
                token_pk=token.pk,
                actor_id=request.user.id,
            )
            revoked += 1
        self.message_user(request, f"Revoked {revoked} token(s).", messages.SUCCESS)
