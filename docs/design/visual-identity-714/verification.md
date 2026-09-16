# Design-study verification

Checked 2026-09-14. These results cover the standalone fakes, not the production
portal and not completion of UX-001 or UX-002.

## Browser review

Chromium through Playwright, with locally served assets:

- Desktop at 1440 pixels: visually inspected all three workspaces and the
  identity presentation. Screenshots are in `previews/`.
- Twelve axe scans: three studies × light/dark × workspace/identity, using
  WCAG 2 A/AA and WCAG 2.1 AA tags. Zero reported violations in the final pass.
- Range search, filtered-empty recovery, status filtering and selected-range
  inspection passed for A and C. Selecting a resource in B changes its inspector.
- Exercise-brief dialogs open and close with Escape. Native modal focus entry,
  Tab cycling and focus return were checked separately.
- Both themes were exercised using the actual toggle, not just CSS overrides.
- No page JavaScript errors in the interaction pass.
- At 390 pixels, the document does not overflow horizontally. Wide tables and
  the relationship diagram scroll within their own regions. Identity sheets
  also fit; the diagram retains legible node labels instead of shrinking them.
- C's inspector remains available at 1024 pixels below the main content.
- All requested page assets are local. The demos make no external requests.

This is automated checking plus a visual/keyboard review, not a comprehensive
accessibility audit, assistive-technology certification, or human usability
validation. Final production work needs the full semantic state/interaction
matrix, supported-language checks, and tests of real data and permissions.

## Measured contrast

Ratios below use the WCAG sRGB relative-luminance formula on computed CSS token
values. Text minimum: 4.5:1. Meaningful control outlines/focus minimum: 3:1.
Values are rounded to two decimal places; the checks used unrounded values.
“Surface” means the study's base panel surface; selected-row text and action
text are checked against their own fills.

| Study / mode | Main text / surface | Muted text / surface | Text / selection | Action text / fill | Success / surface | Warning / surface | Danger / surface | Focus / surface | Control outline / surface |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| A dark | 14.52 | 7.60 | 10.23 | 9.78 | 10.05 | 10.16 | 9.02 | 9.08 | 4.18 |
| A light | 15.14 | 6.24 | 12.77 | 7.15 | 6.73 | 6.65 | 6.54 | 7.15 | 3.56 |
| B dark | 13.59 | 7.11 | 9.50 | 8.57 | 9.41 | 9.51 | 8.44 | 8.88 | 3.91 |
| B light | 15.15 | 5.99 | 13.06 | 7.41 | 6.73 | 6.65 | 6.54 | 7.41 | 3.56 |
| C dark | 12.42 | 7.76 | 9.11 | 8.31 | 8.62 | 9.16 | 8.10 | 7.62 | 4.81 |
| C light | 12.83 | 5.91 | 10.78 | 7.12 | 6.52 | 6.44 | 6.33 | 6.44 | 3.52 |

The prototype CSS contains the exact candidate color values. “Info” uses the
accent role and “neutral” uses muted text in these studies. Danger is a measured
candidate token, not an exercised destructive workflow. Fine decorative
separators are not represented as control-boundary contrast claims.

The brand gradients were darkened during visual review so white text also
passes against the brightest stop: A `#4d788d` 4.79:1, B `#506dad` 5.08:1,
C `#82634c` 5.47:1. Intermediate stops are darker. C's campaign copy is
`#f0f3ea` and stays on the dark portion; the timing panel has an opaque dark
backing. The diagram and operational data have solid backgrounds. A final
brand guide must recheck any new crop, gradient direction, typography size or
text placement rather than treating this as unrestricted gradient approval.

## Repository checks and pending work

`node --check docs/design/visual-identity-714/studies.js` and `git diff --check`
passed. `python3 scripts/adr_guard/adr_guard.py --all --level ci` passed for the
design/preflight work. No production files, requirements, dependency manifests,
CI rules or runtime assets were edited. The font specimens live only inside
this design-review directory with their upstream licenses.

The full implementation completion/policy/review/publish gates have not run:
this is the user-requested selection point before implementation. No PR has
been opened or merged. Neither the issue nor its requirements is declared
complete.
