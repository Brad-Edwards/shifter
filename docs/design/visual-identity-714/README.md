# Shifter identity studies — choose a direction

Three interactive design fakes for #714. Not production UI. Every range,
resource, person, workspace, timestamp and observation is fictional. Navigation
labels are visual specimens; the available interactions are listed below.

Open `index.html` directly in a browser, or serve only this directory:

```bash
python3 -m http.server 8714 --bind 127.0.0.1 --directory docs/design/visual-identity-714
```

- [A — Vector](http://127.0.0.1:8714/?concept=a): graphite queue and inspector.
- [B — Clarity](http://127.0.0.1:8714/?concept=b): light relationship workspace.
- [C — Meridian](http://127.0.0.1:8714/?concept=c): petrol exercise command view.

The top bar switches studies and light/dark modes. “Identity & typography” shows
the mark, monochrome/small-size variants, palette, type and sample voice.
In A and C, search/filter ranges and select rows to change the inspector.
In B, select a workstation to inspect its connection. Exercise-brief and
inspection actions open dismissible demo dialogs. No API calls, provisioning,
remote connections, analytics, persistence or browser storage are used.

Static previews: [Vector](previews/vector.png), [Clarity](previews/clarity.png),
[Meridian](previews/meridian.png). These are browser captures of the local
studies, with the same repository license as their source; they are not imported
into the application.

Compare the visual character, readability and sense of product quality first.
Then try to identify the degraded range, explain its problem and find the next
action. Pick A, B or C, or name specific elements to combine. No direction is
selected yet. See [research and rationale](research.md) and
[verification](verification.md).

## Asset provenance

| Asset | Source / creator | License | Modifications / derivatives |
| --- | --- | --- | --- |
| Three geometric mark studies in `studies.js` | Original Shifter study artwork authored for #714; not traced from reference products | Repository MIT license; legal notices remain unchanged | Inline inert SVG geometry; monochrome and size specimens reuse the same paths. `marks.svg` is a provisional A favicon. These are studies, not finalized identity masters. |
| Navigation geometry in `studies.js` | Original basic study drawings | Repository MIT license | Temporary presentation drawings only; production keeps existing Lucide. |
| Gradient fields and layout code | Original Shifter study work | Repository MIT license | CSS only; no third-party image or texture |
| IBM Plex Sans 400 / 600 Latin WOFF2 | IBM; `@fontsource/ibm-plex-sans@5.3.0` | SIL OFL 1.1; `fonts/ibm-plex-sans-LICENSE.txt` | Unmodified package files; no font derivatives |
| IBM Plex Mono 400 Latin WOFF2 | IBM; `@fontsource/ibm-plex-mono@5.3.0` | SIL OFL 1.1; `fonts/ibm-plex-mono-LICENSE.txt` | Unmodified package file |
| Source Sans 3 400 / 600 Latin WOFF2 | Adobe; `@fontsource/source-sans-3@5.3.0` | SIL OFL 1.1; `fonts/source-sans-3-LICENSE.txt` | Unmodified package files |
| Barlow 600 Latin WOFF2 | Jeremy Tribby / Barlow Project; `@fontsource/barlow@5.3.0` | SIL OFL 1.1; `fonts/barlow-LICENSE.txt` | Unmodified package file |

Only Latin subsets are bundled for these English review fixtures. Choosing a
font for production requires evaluating all supported scripts and retaining
appropriate fallback and license coverage. Font choice does not require a
second runtime delivery mechanism.

External product screenshots and brand assets are **not redistributed** in this
directory. They were viewed for research and are linked from the research note.
The cited products do not endorse these designs. No trademark clearance claim
is made for the provisional original marks.

## Continuation boundary

Both architecture preflights and the design-stage plan are recorded on #714.
The user requested a selection gate before implementation; stop here for that
choice. After selection, refine one identity and its source/derivative inventory,
finish the palette/state accessibility evidence and brand guide, then continue
the implementation workflow within the agreed visual-design scope. Requirement
statuses and production assets remain unchanged at this stage.
