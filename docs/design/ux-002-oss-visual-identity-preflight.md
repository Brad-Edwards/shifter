# UX-002 OSS Visual Identity Preflight

This note sets architecture guardrails for UX-002, its visual-identity design
issue #714, and the partial debranding follow-up #718. It is intentionally not
an implementation plan and does not select the final Shifter visual identity.

## Boundary

The debranding pass must remove proprietary brand surface area from shipped UI
and static surfaces while preserving domain-accurate product references that
users need to operate ranges.

Remove branding contamination from:

- Django base templates, partials, login/logout pages, and app templates.
- Static CSS, JavaScript, images, favicons, social-card assets, and generated
  static output.
- In-app documentation pages that present Shifter itself.
- Decorative copy, titles, logo marks, color tokens, class names, filenames, and
  UI behavior names derived from proprietary product branding.

Keep legitimate product references in:

- Scenario templates, range configuration, provisioning code, packer scripts,
  Terraform/Kubernetes assets, MCP packages, and tests that describe or validate
  supported product integrations.
- User-facing instructional copy where the product name is required to complete
  a range task, configure an integration, upload an agent, or interpret exercise
  telemetry.
- Existing schema fields and persisted domain concepts such as agent type values
  when renaming would become a data migration or API compatibility problem.

When a string match is ambiguous, classify it by purpose. If the term identifies
Shifter's own look, navigation, marketing voice, or decorative chrome, remove it.
If it identifies an external product that a range deploys, configures, or teaches,
keep it.

Copyright, licence, dependency-notice, and required contact attributions are a
third category. They are legal provenance, not Shifter visual identity, and
must not be removed or rewritten as part of debranding without maintainer/legal
direction. This matters for the root `LICENSE`, SPDX notices, and third-party
package licences. It also means acceptance cannot be a blind zero-hit search:
every remaining protected-name hit needs a recorded purpose classification.

## Existing Patterns To Reuse

- Base layouts already centralize most global styling and script imports in
  `shifter/shifter_platform/templates/mission_control/base.html`,
  `shifter/shifter_platform/templates/ctf/base.html`, and
  `shifter/shifter_platform/templates/documentation/base.html`. Replace shared
  theme entrypoints there rather than duplicating per-app CSS imports.
- Navigation chrome is centralized in
  `shifter/shifter_platform/templates/partials/icon_sidebar.html` and
  `shifter/shifter_platform/templates/partials/ctf_participant_sidebar.html`.
  Keep sidebar behavior in the existing sidebar/dropdown JavaScript modules
  instead of creating a second navigation system.
- Static JavaScript uses Jest with jsdom under
  `shifter/shifter_platform/static/js/*.test.js`. Reuse those tests for any
  behavior-preserving file renames or DOM contract changes.
- CSS quality tooling already exists through Stylelint in
  `shifter/shifter_platform/.stylelintrc.json`; JavaScript quality uses
  `eslint.config.js`.
- Template context shared across apps flows through existing Django context
  processors in `config/settings.py`. Do not introduce a new global branding
  context unless a real runtime setting is required.
- Cross-layer Python changes, if any become necessary, must follow existing app
  service boundaries and shared contracts under `shifter/shifter_platform/shared`.
  This debranding pass should normally stay in templates, static assets, tests,
  and documentation.
- The reviewed UX-001 boundary in
  `docs/architecture/design-system-single-source-preflight-ux-001.md` and the
  design-system foundation own runtime visual tokens and SPA primitives. The
  identity supplies values and assets to that single catalog; it does not create
  a parallel palette, component library, or SPA-only stylesheet.
- `config/settings.py` staticfiles settings, `shared/spa.py`'s Vite-manifest
  resolver, the portal `Dockerfile` build then `collectstatic` sequence, and
  `scripts/stack-smoke/page_smoke.py` are the static-artifact pipeline. Use
  them for every portal logo, favicon, font, and social-card asset; do not add a
  CDN, runtime asset generator, or ad hoc static URL resolver.
- The existing SPA stack already ships `@fontsource-variable/geist`,
  `@fontsource-variable/geist-mono`, and `lucide-react`. Their lockfile licence
  metadata and the existing component boundary are the starting point for
  typography/icon decisions; do not vendor copies or introduce a second icon
  set merely for the identity pass.

## Asset Ownership And Release Boundary

The final guideline must name a single source asset for the primary mark, a
small-context mark, monochrome variants, favicon, and social-card artwork; it
must record creator/source, licence, modification status, and the generated
derivatives. A vector source is preferred for Shifter-owned marks; generated
raster sizes are release artifacts, not independently edited brand sources.
The asset manifest is documentation/provenance, not a runtime model, database
record, or tenant setting.

Audit the entire release path, not only templates: `static/` inputs (including
icons and favicons), Vite imports and `static/spa` output, Django templates,
email templates, root `assets/`, public `docs/` images and HTML, repository
metadata used by documentation or social previews, and container-collected
static output. `assets/styles/login.css` and its README explicitly identify
extracted Cortex material; they are not a neutral token incumbent and may not
remain as reusable reference or shipped asset. Existing raster files in
`assets/images/` require provenance review before reuse. A filename, SVG
metadata/title, CSS custom property, source map, or generated bundle can retain
the prohibited identity even after a visible page changes.

CTF event `logo_url` and `theme_color` are separately persisted,
organizer-authored event content (`ctf.models.event.CTFEvent`, its organizer
serializer, and `EventHomePage`), not the platform mark or token catalog. Do
not reuse that remote-URL/inline-style rendering path for Shifter identity and
do not change its API, model, validation, or authorization in this work. Its
current URL/colour shape validation is not provenance validation and is not a
safe platform-asset delivery mechanism; any future change to event branding is
a distinct security and CTF-domain decision.

The identity's extensibility seam is the existing semantic-token catalog plus a
small documented asset-variant inventory: a later light/dark treatment,
monochrome context, or new output size maps to a semantic token or a declared
derivative. It must not require feature-local color edits, duplicated SVG
paths, or a persisted per-tenant branding preference.

## Guardrails

- Use a neutral interim palette with WCAG AA contrast. Do not approximate,
  sample, recolor, or rename proprietary palette values.
- Rename brand-derived CSS classes, custom properties, filenames, and JS module
  names only where the rename removes Shifter-owned UI contamination. Do not
  rename persisted product fields or scenario schema keys as part of this pass.
- Avoid introducing a design system, token package, theme engine, logo system, or
  runtime tenant branding abstraction. UX-002's final identity and follow-up
  design-system work are separate.
- Keep visual assets original or plain placeholders. Do not trace, simplify,
  recolor, or otherwise derive replacement logos from proprietary marks.
- Treat the existing `ShifterMark` SVG as an implementation input that needs
  provenance and accessibility review, not as evidence that a final logo has
  been approved. Its gradient stops must ultimately consume the canonical
  semantic accent tokens (or be a documented temporary adapter), not become a
  new palette authority.
- Scrub source references and generated/static references together. A template
  that no longer imports an asset is not enough if the branded file still ships.
- Do not use remote image/font/icon URLs, `data:` branding images, user-provided
  CSS values, or runtime-configured asset paths to shortcut an asset swap. They
  weaken the existing CSP/static-artifact boundary and make provenance and
  cacheability unverifiable.
- Review grep hits manually by boundary, not by blanket deletion. Product names
  in provisioning, scenarios, tests, and product setup docs can be correct.
- Preserve authentication, authorization, logging, upload validation, scenario
  validation, and provisioning behavior. This pass is a presentation and shipped
  artifact cleanup, not a domain model change.

## Cross-Cutting Constraints

| Layer | Existing authority | UX-002 requirement |
| --- | --- | --- |
| Browser/CSP | `config/_browser_security.py`, ADR-036 | Assets remain same-origin static files under `img-src`, `font-src`, and `style-src`; no inline styles, new remote source, CSP exception, or policy duplicate. SVG must be inert presentation data, with no script, event handler, foreign object, external reference, or user interpolation. |
| Templates/i18n | Django autoescaping, `{% static %}`, ADR-016 | Keep names, alt text, titles, and voice copy translatable. Map any dynamic state to trusted finite classes/labels; never interpolate user data into SVG, CSS, `url(...)`, metadata, or generated class names. |
| SPA/API/auth | `frontend/src/api/client.ts`, `/api/v1/`, `shared.api.errors`, ADR-029 | A visual change creates no fetch client, DTO/schema, browser token, permission check, validation copy, error hierarchy, or client-side authorization rule. Existing error envelopes remain unchanged. |
| Static build/runtime | Vite config, `shared/spa.py`, WhiteNoise manifest storage, Dockerfile | Build assets once, content-hash and resolve them through the existing manifest/static pipeline; do not commit generated SPA output or generate assets per request. The non-root image build must be able to collect every referenced asset. |
| Configuration/secrets/OS | `config/settings.py`, runtime-env validation, Dockerfile | No brand setting or secret env binding is needed. Do not pass source artwork, credentials, signed URLs, cookies, or raw generated pages in process arguments or diagnostic output. |
| Logging/persistence/audit | `shared.log_sanitize`, existing audit services | No model, migration, preference, audit event, or new logger is warranted. If an inventory check emits diagnostics, report only repo-relative paths and classifications, never raw user content or credentials. |

These constraints deliberately leave domain schemas, serializers, controllers,
services, repositories, workflow state, exception handling, and observability
ownership where they are. Styling a status is not a status model; hiding or
disabling a control is not authorization or validation.

## Verification Expectations

Before completion, the implementation should run the repo architecture gate:

```bash
python3 scripts/adr_guard/adr_guard.py --all --level ci
```

For frontend/static edits under `shifter/shifter_platform`, also run the relevant
stack-native checks from that directory:

```bash
uv run ruff check .
uv run ruff format --check .
npm test -- --runInBand
npx eslint static/js
npx stylelint "static/css/**/*.css"
```

Use a scoped case-insensitive search over templates, static assets, JavaScript,
documentation, root assets, and generated build output to produce a
reviewer-readable list of remaining hits. Inspect binary/SVG metadata as well
as text. Each remaining hit should be classified as a legal attribution or a
required product-integration reference; none may be Shifter branding or an
unlicensed/proprietary visual asset. Validate the chosen palette's documented
text, non-text UI, focus, and status treatments in both supported themes using
the existing unit/axe/Playwright accessibility lanes.

## Non-Goals

- No tenant branding, database-backed theme preference, image upload, CMS brand
  editor, network fetch, CDN, or runtime asset-transformation service.
- No change to API contracts, serializers, forms, permissions, auth/CSRF,
  exception envelopes, audit records, logging policy, scenario/provisioner
  schemas, or product-integration terminology.
- No blanket deletion of legally required notices or operational XDR/Cortex/Palo
  Alto references. Their classification is required precisely to avoid conflating
  an OSS visual identity with the platform's supported integrations.
