# Platform API Development

Shifter's non-public HTTP/JSON API is a Django REST Framework surface mounted
under `/api/v1/`. New API endpoints should join that surface instead of adding
ad-hoc `JsonResponse` function views.

## Endpoint Shape

- Put request and response validation in DRF serializers.
- Put application behavior in the existing service layer (`cms.services`,
  `ctf.services`, `engine.services`, `risk_register.services`, or the owning
  app's public facade).
- Use `APIView` or `ViewSet` for HTTP concerns only: authentication,
  permissions, serializer selection, response status, pagination, filtering, and
  OpenAPI metadata.
- Mount app routers through `config/api_urls.py` so the public path remains
  `/api/v1/...` and the OpenAPI schema sees every migrated route.

## Authentication And Scopes

The platform DRF defaults authenticate in this order:

1. `shared.api_tokens.authentication.ApiTokenAuthentication`
2. `rest_framework.authentication.SessionAuthentication`

New endpoints should use platform bearer tokens and session auth. Do not accept
new API key formats outside the shared token model. Legacy app-local
authenticators are declared only on their owner views; the risk-register
`X-API-Key` path is deprecated compatibility for current consumers and retires
under #1124.

Token scopes come from `shared.api_tokens.scopes`. Compose
`shared.api_tokens.permissions.require_scope(read_scope, write_scope)` with the
endpoint's session/domain permission. Scopes admit a token to an endpoint class;
they do not replace object ownership, event membership, staff checks, CMS
authoring checks, or service-layer state validation.

## Errors

DRF exceptions use the platform envelope:

```json
{
  "error": {
    "code": "invalid",
    "message": "Invalid request",
    "details": {
      "field": ["This field is required."]
    },
    "request_id": "req-123"
  }
}
```

Use `shared.api.errors.api_error_response(...)` for explicit DRF error
responses. Do not return raw exception text, provider payloads, bearer tokens,
cookies, CSRF tokens, presigned URLs, or signed Guacamole URLs.

## Schema And Docs

OpenAPI is served from `/api/v1/schema/`; Swagger UI is served from
`/api/v1/docs/`. These endpoints use the same platform authentication posture
as the non-public API: an authenticated session user or a valid platform API
token can read the contract. Swagger UI assets are served from the pinned local
`drf-spectacular-sidecar` package, not from a third-party CDN.

Keep examples secret-free. Show header names and placeholder values, not real
tokens or signed URLs.

## Pagination And Filtering

The default list behavior is page-number pagination with page size 50. The
platform enables DRF's search and ordering filter backends globally, but each
endpoint must declare its supported `search_fields` and `ordering_fields`.
Prefer serializer- or filterset-shaped query validation for new filters instead
of parsing unbounded query parameters in `get_queryset()`.
