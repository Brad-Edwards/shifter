# UX-001 Design System Single Source Preflight

Status: pre-implementation guidance

Issue: GitHub #714, "OSS Shifter visual identity design"

This note narrows the already-reviewed foundation in
[`docs/design/spa-design-system-foundation-1299.md`](../design/spa-design-system-foundation-1299.md)
and its [preflight](spa-design-system-foundation-preflight-1299.md). It is not
an implementation plan or a page-migration authorization.

## Decision and Boundary

The deployed portal has three competing visual vocabularies today:
`static/css/theme.css` (`--theme-*`), SPA `frontend/src/index.css`
(`--background`, Tailwind aliases), and the documented `--ds-*` catalog under
`docs/design/design-system/`. UX-001 is not satisfied by adding a fourth one.

The implementation must make the reviewed `primitive -> semantic -> component`
model in the foundation the one authored runtime token catalog. Its runtime
home must be under `shifter/shifter_platform/static/css/design-system/` so the
existing Django staticfiles/WhiteNoise pipeline can serve it to legacy templates
and Vite can consume the same authored CSS for the SPA. The documentation must
describe and link to that catalog; it must not retain a copied, independently
editable token file. Generated Vite output remains an artifact, never a source.

The existing `--theme-*` and Tailwind/shadcn variable names may be centrally
mapped to semantic tokens only as a migration adapter. They may not introduce
new literal visual values, and new application code must not consume the legacy
names. Remove adapters only when their last legacy consumer is migrated. HTML
email is outside this boundary: email-client-compatible inline styling is not a
portal design-system bypass.

Existing SPA primitives in `frontend/src/components/ui/`, built on Radix,
class-variance-authority, Lucide, and Tailwind, remain the component
implementation boundary. A feature may compose or domain-wrap a primitive, but
may not fork a generic button, input, dialog, table, badge, alert, tabs,
tooltip, empty/loading state, or shell pattern. The component inventory, state
matrix, accessibility requirements, and domain-status mapping remain owned by
the foundation document.

## Cross-Cutting Guardrails

- Keep the token catalog static and public: no user, tenant, environment,
  credential, signed URL, provider payload, or server-supplied value may become
  a token, CSS custom-property value, fixture, style-guide example, or bundle
  input. Never interpolate untrusted values into CSS, class names, IDs, or
  `url(...)`.
- The browser policy in ADR-036 permits same-origin styles and explicitly
  forbids inline style attributes. Continue using external static CSS; do not
  add CSP exceptions, `unsafe-inline`, template-local `<style>`, or `style=` to
  bridge the migration.
- The system is presentation-only. DRF serializers and domain schemas keep
  validation; `shared.api.errors` remains the sole API error envelope; backend
  permissions and services remain authoritative. Disabled/hidden UI and a
  status color do not validate, authorize, or model a workflow state.
- SPA network use stays behind `frontend/src/api/client.ts`: same-origin
  session/CSRF handling, request IDs, and typed errors are not component-level
  concerns. Do not create design-system fetch, exception, persistence,
  logging, or preference abstractions.
- Preserve Django `{% static %}` and `{% trans %}` / `{% blocktrans %}` for
  legacy templates and ADR-016. A component API cannot silently turn
  user-facing labels, error text, or accessible names into untranslated strings.
- Keep `CompressedManifestStaticFilesStorage`, the Vite manifest resolver in
  `shared/spa.py`, and the image-build `npm run build` then `collectstatic`
  sequence intact. Do not commit generated SPA assets or add runtime asset
  generation/configuration.

## Review Rule and Extensibility Seam

Review rejects raw visual literals and app-local generic component styling in
portal code unless the value is inside the central primitive catalog or a
documented, time-bounded legacy adapter. Raw values required for third-party
vendor CSS stay isolated in `static/css/vendor/`; they are not a design-system
precedent. Use the existing Stylelint/ESLint/SPA unit-and-axe/Playwright gates;
any automated new rejection rule is a guardrail change and must be documented
with the ADR registry rather than silently weakening or bypassing those gates.

The extension seam is semantic token aliases plus primitive variants. A new
theme, density mode, or status treatment changes token mappings or a primitive
variant; it must not require feature-by-feature edits. Domain-to-intent mapping
is a rendering mapping only and must not create a parallel domain enum or state
machine.

## Non-Goals

- No final logo/brand selection, tenant branding, theme-preference persistence,
  new font delivery mechanism, SPA framework change, feature-page migration,
  API change, or database change.
- No replacement of authentication, CSRF, authorization, validation, error,
  audit, logging, i18n, or static-asset mechanisms.
- No migration of transactional email CSS, vendor CSS, or deprecated material
  merely because it contains colors; deprecated material remains non-authoritative.
