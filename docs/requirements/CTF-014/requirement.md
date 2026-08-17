---
id: CTF-014
title: "Customization & Extensibility"
status: ACTIVE
type: NON_FUNCTIONAL
priority: COULD
wave: 2
created_at: 2026-03-18T05:28:21.376085Z
updated_at: 2026-03-26T06:34:25.081480Z
---

# CTF-014: Customization & Extensibility

## Statement

The platform could support extensibility and customization for CTF functionality including: custom challenge types and scoring modes registered as Django apps, configurable event branding (logo, colors, description) via Django's template system, and multi-language support via Django's built-in i18n framework. The CTF layer shall not build a standalone plugin system, theming engine, or translation infrastructure.

## Rationale

Extensibility enables the platform to serve diverse use cases without core code changes. Django's app architecture, template system, and i18n framework already provide these extension points. Custom challenge types are Django apps registered with CTF. Event branding is template configuration. Translations use Django's standard gettext workflow.

## Traceability

- IMPLEMENTS → CODE_FILE `shifter/shifter_platform/ctf/extensions.py` (Django-app registration contracts for custom flag validators and scoring strategies)
- IMPLEMENTS → CODE_FILE `shifter/shifter_platform/ctf/models/event.py` (Event branding fields and extension-aware scoring-mode choices)
- IMPLEMENTS → CONFIG `shifter/shifter_platform/config/settings.py` (Django LocaleMiddleware, USE_I18N, and LOCALE_PATHS configuration)
- IMPLEMENTS → DOCUMENTATION `shifter/shifter_platform/locale/en/LC_MESSAGES/django.po` (Django gettext message catalog)
- TESTS → TEST `shifter/shifter_platform/tests/ctf/test_customization_api.py` (Extension registry dispatch and participant event-branding coverage)
- DOCUMENTS → GITHUB_ISSUE `Brad-Edwards/shifter#627` (CTF-014: Customization & Extensibility)
- IMPLEMENTS → ADR `ADR-051` (Communications reuse the accepted Django extension, branding, and i18n surfaces without a second plugin system)
- IMPLEMENTS → DOCUMENTATION `docs/architecture/ctf-communications-raes-inject-preflight-2047.md` (CTF-014 extension and customization constraints for communications)
- IMPLEMENTS → GITHUB_ISSUE `2047` (Issue #2047 - communications customization and extension boundary)
