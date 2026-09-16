# Shifter identity research — #714

Date: 2026-09-14. Status: three unselected studies; product implementation has
not started. This is desk research and a heuristic design evaluation, not a
usability study with Shifter operators. References demonstrate useful patterns;
they do not establish that a particular style improves task completion here.

## What we are designing for

The existing personas and navigation contract describe operators running
ranges, trainers managing exercises, participants solving challenges, and
self-hosters maintaining the platform. The recent SPA work improved consistency
and established reusable controls. Its present expression is near-black,
Geist, blue actions, rounded cards, translucent chrome, and a small chevron mark.
The next identity needs a more deliberate hierarchy and recognizable visual
language while retaining this shared implementation foundation.

Use the fictional POLARIS exercise to assess three questions: which range needs
attention, what is wrong, and what can the operator do next? A visually striking
dashboard that cannot answer these is unsuccessful. These demos emphasize the
operator surface; the chosen identity still needs a participant and small-screen
product review before broad application.

## Professional references examined

These are first-party examples. Public screenshots show particular releases,
not necessarily the current authenticated UI. The external product imagery is
research material only and is not bundled or traced into Shifter assets.

| Reference | Evidence examined | Observation and implication for Shifter |
| --- | --- | --- |
| [SentinelOne: unified triage](https://www.sentinelone.com/blog/singularity-operations-center-unified-security-operations-for-rapid-triage/) | Article and the actual unified-alerts screenshot, including filters, selected rows, and actions | A compact table, persistent filters and grouped actions support work across multiple objects. The screenshot has a light work area with a narrow dark header and restrained purple selection. Borrow the queue/detail relationship and explicit selection, not its branded palette or mark. Vector tests a queue with an adjacent inspector. |
| [Wiz platform](https://www.wiz.io/platform) | Live marketing page, its blue/pink graph illustration, and the first-party description of Security Graph and attack-path prioritization | The public brand is open and graphic, with a bold blue accent. The graph connects risk to resource relationships. The illustration is marketing artwork, **not** evidence of actual console geometry. Clarity tests the relationship-oriented paradigm with original resource nodes and a persistent inspector. |
| [CrowdStrike Next-Gen SIEM](https://www.crowdstrike.com/en-us/platform/next-gen-siem/) | Live page and visible embedded dark dashboard image; [2024 data sheet](https://www.crowdstrike.com/wp-content/uploads/2024/05/falcon-next-gen-siem-datasheet.pdf) for workbench context | Strong red brand framing and directional teal/red light surround a much quieter dark console. The console has neutral surfaces and purposeful data color. Separate expressive identity surfaces from working UI. Meridian explores a bounded directional field; it does not copy the vendor's light artwork. Some live-page text did not render in the research browser, so typography conclusions do not rely on it. |
| [Cloudflare: thinking about color](https://blog.cloudflare.com/thinking-about-color/) | First-party design-system rationale, palette and contrast examples | Their designers evaluate color in composed interfaces and visualizations, not just swatches. Apply candidate colors to actual operational content and keep brand, action and status roles distinct. |
| [Cloudflare: dark mode](https://blog.cloudflare.com/dark-mode/) | First-party implementation and visual evaluation account | Off-black replaced harsh black; modes required auditing controls and charts beyond reversing a scale. Give every study a designed light and dark mode and check both. |
| [Elastic UI data grid](https://eui.elastic.co/docs/components/tabular-content/data-grid/) and [display controls](https://eui.elastic.co/v113.1.0/docs/components/data-grid/style-and-display/index.html) | Official component guidance | Grids fit comparable, schema-driven information. Density is a user concern, not permission to shrink every label. Use aligned tabular data, readable row targets, and monospace only where it improves technical comparison. |

## UX paradigms to carry forward

[NN/g's complex-application guidance](https://www.nngroup.com/articles/complex-application-design/)
supports keeping secondary context close and removing irrelevant graphics to
make important information stand out. Its
[progressive-disclosure guidance](https://www.nngroup.com/articles/progressive-disclosure/)
supports showing common decisions first and deferring secondary detail.

For Shifter, my design inference is:

- **Overview → selection → inspection:** keep the queue in view while reading
  the cause and next step. Avoid forcing an operator to navigate away for every
  resource inspection. This is Vector's strongest proposition.
- **Relationship view + inspector:** show connections when the task is to
  understand a dependency. Label nodes and retain a textual resource list. A
  network diagram is not an all-purpose dashboard decoration. This is Clarity.
- **Exercise sequence + exception handling:** give a facilitator the phase,
  timing and blocked team before secondary telemetry. This is Meridian.
- **Semantic restraint:** warnings belong to actual problems, actions receive
  a distinct treatment, and neutral structure carries most of the screen.
  Status always has a text label. A gradient never signifies range health.

These are complementary interaction patterns. Choosing a visual identity does
not force every Shifter page into that study's single layout. The selection
will establish visual character; the existing page-template/IA contract still
determines where tables, detail pages, editors and participant flows belong.

## Common tells of generated frontend design

[Anthropic's first-party account](https://claude.com/blog/improving-frontend-design-through-skills)
describes convergence toward familiar fonts, purple gradients and generic
compositions. Its later
[evaluation account](https://www.anthropic.com/engineering/harness-design-long-running-apps)
explicitly penalizes stock components and generic aesthetic patterns. The
community-maintained [Signs of AI Design](https://github.com/febbhav/signs-of-ai-design)
catalog adds examples of stacked defaults. Treat the community catalog as an
opinionated field guide, not validated authorship detection or a prevalence
study; its numerical prevalence claims are not used here.

The review checklist below is my synthesis of these sources and the Shifter
brief. A font, hue or library by itself is not evidence of AI authorship.

| Pattern to reject | Why it hurts this product | Design response |
| --- | --- | --- |
| Purple/blue glow spread across every surface | Brand color competes with operational meaning | Bounded, directional brand fields; flat working surfaces |
| Identical rounded cards for unrelated content | Removes hierarchy and makes all information equally important | Tables, rules, contextual inspectors and a phase sequence |
| Oversized centered greeting and slogan on an operator page | Delays the operational question | Object/state/action leads the workspace |
| Huge KPI tiles with decorative icons and meaningless sparklines | Looks substantial without helping a decision | Small, reconciled fixture counts; no invented trend chart |
| Glass panels, diffuse shadows and excessive pills | Weakens surface hierarchy and text clarity | Opaque surfaces, deliberate borders and mostly square geometry |
| Stock component defaults used as the entire identity | The product has little recognizable character | Chosen type hierarchy, spacing rhythm, original marks and role-specific layouts |
| Changing to beige/serif solely to avoid the earlier clichés | Replaces one template with another | Evaluate against actual cyber-range tasks and screen content |
| Dead controls, implausible data and happy-path-only scenes | The impression of quality collapses on interaction | Fictional but consistent data, visible degraded state, working filters, inspectors, dialogs and empty results |

No tool can certify a screen as having “no LLM feel.” The practical bar here is
a considered design with task-specific hierarchy, credible details, and a
user-reviewed visual direction, not a detector score.

## The three candidate identities

| Study | Character | Gradient and typography | Main tradeoff |
| --- | --- | --- | --- |
| A — Vector | Graphite operations instrument; offset slab mark; cool blue actions | Tonal steel-blue brand field; IBM Plex Sans and Mono; compact headings and three-pixel control corners | Strongest daily-operator scan efficiency. Less dramatic as a brand presentation. |
| B — Clarity | Porcelain investigation workspace; bracket mark; ultramarine emphasis | Bounded ultramarine brand field; Source Sans 3 with Plex Mono; larger entity heading | Most approachable and explanatory. The relationship canvas is useful only when dependencies matter and occupies more space. |
| C — Meridian | Petrol and mineral campaign identity; stepping mark; copper action accent | Controlled petrol-to-copper field; Barlow headings with Plex Sans and Mono | Strongest distinctive presentation and trainer context. The banner must be reduced on dense routine pages. |

My initial recommendation is **Vector for an operator-first product**, with
Meridian worth choosing if brand distinction and live exercise presentation are
the priority. Clarity is the strongest alternative for a more open, analytical
feel. The user chooses; none is selected by this document.

## Relationship to the repository

The user expressly requested three fakes before implementation. These files are
isolated review artifacts, not a new production component library or fourth
runtime palette. Alternative locally hosted fonts are specimens, not production
dependency changes. The product retains its existing Geist packages, Lucide,
shared UI primitives, API client, navigation taxonomy and static pipeline.

After selection, the final guide must reconcile the chosen values with the
UX-001 single-source token boundary and the UX-002 asset provenance rules.
Historical instructions to preserve the Apple-dark visual direction described
the earlier cutover; the current brief reopens identity. It does not reopen
backend behavior, authentication, authorization, theme persistence or IA.

The two preflight notes remain binding for eventual runtime integration:
[UX-001](../../architecture/design-system-single-source-preflight-ux-001.md) and
[UX-002](../ux-002-oss-visual-identity-preflight.md). Completing these demos does
not complete either requirement or the issue. Final vector refinement,
derivative inventory, full semantic token/state contrast matrix, brand guidance
and normal reviewed delivery remain pending selection.
