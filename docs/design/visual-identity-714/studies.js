/* Standalone identity studies. All strings and data below are invented fixtures. */
"use strict";
const root = document.documentElement;
const params = new URLSearchParams(location.search);
const concept = ["a", "b", "c"].includes(params.get("concept")) ? params.get("concept") : "a";
root.dataset.concept = concept;
root.dataset.theme = params.get("theme") === "light" || (concept === "b" && params.get("theme") !== "dark") ? "light" : "dark";
const config = {
  a: { name: "Vector", pitch: "Precise. Composed. Ready for work.", type: "IBM Plex Sans + IBM Plex Mono", mark: '<path d="M4 5h20l-6 6H4zm7 10h20l-6 6H11zM4 25h20l-3 4H4z" fill="currentColor"/>', description: "An instrument-panel identity: offset cuts, cool graphite, and a clear blue action color. The queue carries the visual weight; brand expression lives in the mark and controlled tonal fields.", gradient: "Directional steel-blue field. Use on covers, sign-in artwork, and identity panels; keep working surfaces flat.", voice: "Direct and operational. Name the object, explain its state, offer the next action." },
  b: { name: "Clarity", pitch: "See the relationships. Find the cause.", type: "Source Sans 3 + IBM Plex Mono", mark: '<path d="M5 4h21v6H11v7H5zm22 11v13H6v-6h15v-7z" fill="currentColor"/>', description: "An open, editorial identity: porcelain surfaces, ink typography, a structural bracket mark, and ultramarine accents. An evidence canvas keeps the selected object beside its dependencies.", gradient: "A bounded ultramarine field for brand applications. No gradients behind topology labels or controls.", voice: "Explanatory and precise. Show what is connected and explain why it matters." },
  c: { name: "Meridian", pitch: "Set the course. Run the exercise.", type: "Barlow headings + IBM Plex Sans / Mono", mark: '<path d="M4 6h9l10 10-10 10H4l10-10zm14 0h6l8 10-8 10h-6l8-10z" fill="currentColor"/>', description: "A campaign identity: deep petrol, mineral neutrals, copper accents, and a forward-stepping mark. Broad horizontal rhythm and a clear exercise sequence support briefing and facilitation.", gradient: "A mineral field from petrol to muted copper, cut with directional geometry. Text sits only over the dark end or on an opaque backing.", voice: "Assured and concise. Give the exercise context, the next decision, and its consequence." }
}[concept];
const paths = {
  grid: '<rect x="3" y="3" width="6" height="6"/><rect x="13" y="3" width="6" height="6"/><rect x="3" y="13" width="6" height="6"/><rect x="13" y="13" width="6" height="6"/>',
  box: '<path d="m11 2 9 5v10l-9 5-9-5V7Zm-9 5 9 5 9-5M11 12v10"/>',
  flag: '<path d="M4 21V3h13l-3 5 3 5H4"/>',
  code: '<path d="m7 5-6 6 6 6m8-12 6 6-6 6m-2-14-4 20"/>',
  users: '<circle cx="9" cy="7" r="3"/><path d="M2 20v-3a7 7 0 0 1 14 0v3M16 5a3 3 0 0 1 0 6m2 3a6 6 0 0 1 3 6"/>',
  settings: '<circle cx="11" cy="11" r="4"/><path d="M11 0v4m0 14v4M0 11h4m14 0h4M3 3l3 3m10 10 3 3M3 19l3-3M16 6l3-3"/>',
  search: '<circle cx="9" cy="9" r="6"/><path d="m14 14 6 6"/>',
  clock: '<circle cx="11" cy="11" r="9"/><path d="M11 5v7l4 2"/>',
  book: '<path d="M3 3h7l2 2 2-2h7v16h-7l-2 2-2-2H3ZM12 5v16"/>'
};
const icon = name => `<svg class="icon" viewBox="0 0 24 24" aria-hidden="true">${paths[name] || paths.box}</svg>`;
const mark = extra => `<svg class="mark ${extra || ""}" viewBox="0 0 36 32" aria-hidden="true">${config.mark}</svg>`;
const wordmark = () => `<span class="wordmark">${mark()}<span>shifter</span></span>`;
const stateLabel = { ready: "Ready", degraded: "Degraded", provisioning: "Provisioning", stopped: "Stopped" };
const status = state => `<span class="status ${state}">${stateLabel[state]}</span>`;
const ranges = [
  { id:"polaris", name:"POLARIS / Team 04", scenario:"Lateral movement lab", state:"degraded", region:"eu-west-1", assets:"5 / 6", owner:"M. Chen", age:"42m", cause:"Telemetry delayed on workstation-02", detail:"The last heartbeat arrived 4 minutes ago. The other five assets are reporting normally." },
  { id:"polaris-01", name:"POLARIS / Team 01", scenario:"Lateral movement lab", state:"ready", region:"eu-west-1", assets:"6 / 6", owner:"M. Chen", age:"48m", cause:"All assets are reporting", detail:"Six assets have completed their readiness checks. The range is available to the team." },
  { id:"polaris-02", name:"POLARIS / Team 02", scenario:"Lateral movement lab", state:"ready", region:"eu-west-1", assets:"6 / 6", owner:"M. Chen", age:"47m", cause:"All assets are reporting", detail:"Six assets have completed their readiness checks. The range is available to the team." },
  { id:"polaris-03", name:"POLARIS / Team 03", scenario:"Lateral movement lab", state:"ready", region:"eu-west-1", assets:"6 / 6", owner:"M. Chen", age:"45m", cause:"All assets are reporting", detail:"Six assets have completed their readiness checks. The range is available to the team." },
  { id:"kepler", name:"KEPLER / Sandbox", scenario:"Cloud identity workshop", state:"provisioning", region:"us-east-1", assets:"3 / 5", owner:"A. Rivera", age:"6m", cause:"Waiting for two assets", detail:"Three assets are ready. Two are still being provisioned; no action is needed yet." },
  { id:"atlas", name:"ATLAS / Research", scenario:"Detection validation", state:"ready", region:"us-east-1", assets:"4 / 4", owner:"J. Park", age:"2h", cause:"All assets are reporting", detail:"The research range is available. All four assets passed readiness checks." }
];
let selected = ranges[0];
let currentView = "workspace";
let query = "";
let filter = "all";
let resource = "workstation-02";
document.querySelector(`[data-direction="${concept}"]`).setAttribute("aria-current", "page");
document.title = `Shifter — ${config.name} identity study`;
function sidebar() {
 return `<aside class="sidebar" aria-label="Shifter navigation specimen">${wordmark()}
 <div class="workspace-label"><span class="eyebrow">Workspace</span><strong>Northstar Lab</strong></div>
 <div class="side-group"><span class="eyebrow">Operate</span><span class="side-item">${icon("grid")}Overview</span><span class="side-item active">${icon("box")}Ranges</span><span class="side-item">${icon("flag")}Events</span></div>
 <div class="side-group"><span class="eyebrow">Author</span><span class="side-item">${icon("code")}Scenarios</span><span class="side-item">${icon("book")}Catalog</span></div>
 <div class="side-group"><span class="eyebrow">Administer</span><span class="side-item">${icon("users")}Members</span><span class="side-item">${icon("settings")}Settings</span></div>
 <div class="side-bottom"><div class="profile"><span class="avatar">MC</span><div><strong>Morgan Chen</strong><br><small>Operator</small></div></div></div></aside>`;
}
function shell() {
 document.querySelector("#app").innerHTML = `<div class="shell">${sidebar()}<div class="main-column"><div class="topbar"><strong>Shifter</strong><span class="divider"></span><span class="mode">Operate</span><span>Northstar Lab</span><span class="right timestamp">Snapshot <time>14:32 UTC</time></span>${icon("clock")}</div><main id="workspace" class="content" tabindex="-1"></main></div></div>`;
 render();
}
function tabs() { return `<nav class="page-tabs" aria-label="Study view"><button data-view="workspace" aria-pressed="${currentView === "workspace"}">Workspace</button><button data-view="identity" aria-pressed="${currentView === "identity"}">Identity & typography</button></nav>`; }
function heading(title, subtitle, eyebrow="MISSION CONTROL") {
 return `<div class="page-heading"><div><p class="eyebrow">${eyebrow}</p><h1>${title}</h1><p>${subtitle}</p></div><div class="page-actions"><button data-action="brief">Exercise brief</button><button class="primary" data-action="launch">+ Create range</button></div></div>`;
}
function footer() { return `<footer class="study-foot"><span>${concept.toUpperCase()} / ${config.name} · ${config.type}</span><span>Design study · navigation labels are specimens · <a href="research.md">Research & rationale</a></span></footer>`; }
function filters() {
 return `<div class="filters"><label class="searchbox">${icon("search")}<span class="sr-only">Search ranges</span><input id="range-search" type="search" placeholder="Search ranges or scenarios" autocomplete="off"></label><label><span class="sr-only">Range status</span><select id="status-filter"><option value="all">All statuses</option><option value="degraded">Degraded</option><option value="ready">Ready</option><option value="provisioning">Provisioning</option></select></label></div>`;
}
function table() {return `<div class="table-wrap"><table><caption class="sr-only">Fictional ranges. Select a range to inspect its state.</caption><thead><tr><th scope="col">Range / scenario</th><th scope="col">Status</th><th scope="col">Assets ready</th><th scope="col">Region</th></tr></thead><tbody id="range-rows"></tbody></table></div><div class="table-footer"><span id="result-count"></span><span>Snapshot · 14:32 UTC</span></div>`;}
function drawRows() {
 const target = document.querySelector("#range-rows"); if(!target) return;
 const matches = ranges.filter(r => (filter === "all" || r.state === filter) && `${r.name} ${r.scenario}`.toLowerCase().includes(query.toLowerCase()));
 target.innerHTML = matches.length ? matches.map(r => `<tr class="${r.id === selected.id ? "selected" : ""}"><td><button class="row-link" data-range="${r.id}" aria-pressed="${r.id === selected.id}">${r.name}</button><small>${r.scenario}</small></td><td>${status(r.state)}</td><td class="mono">${r.assets}</td><td class="mono">${r.region}</td></tr>`).join("") : '<tr><td colspan="4">No ranges match these filters. <button data-action="clear">Clear filters</button></td></tr>';
 document.querySelector("#result-count").textContent = `${matches.length} of ${ranges.length} ranges`;
}
function inspector() {
 return `<p class="eyebrow">Selected range</p><h2>${selected.name}</h2><p class="muted">${selected.scenario}</p>${status(selected.state)}
 <div class="notice state-${selected.state}"><strong>${selected.cause}</strong><p>${selected.detail}</p></div>
 <dl><div><dt>Scenario</dt><dd>${selected.id.startsWith("polaris") ? "POLARIS v2.4" : selected.id === "kepler" ? "KEPLER v1.2" : "ATLAS v3.0"}</dd></div><div><dt>Owner</dt><dd>${selected.owner}</dd></div><div><dt>Region</dt><dd class="mono">${selected.region}</dd></div><div><dt>Running for</dt><dd class="mono">${selected.age}</dd></div><div><dt>Assets ready</dt><dd class="mono">${selected.assets}</dd></div></dl>
 <h3>Readiness</h3><div class="mini-steps" aria-hidden="true">${Array.from({length:Number(selected.assets.split("/")[1])}, (_, i) => `<span class="${i < Number(selected.assets.split("/")[0]) ? "" : "pending"}"></span>`).join("")}</div><small>${selected.state === "ready" ? "All checks complete" : selected.state === "provisioning" ? "Resource creation in progress" : "Infrastructure ready · telemetry pending"}</small>
 <button class="primary" data-action="inspect">Inspect range →</button>`;
}
function activity() {return `<section class="activity" aria-label="Recent activity"><h2>Recent activity</h2><div class="activity-row"><time>14:28:06</time><span>Telemetry delayed on <strong>workstation-02</strong></span><small>POLARIS / Team 04</small></div><div class="activity-row"><time>14:26:12</time><span>Provisioning started for <strong>KEPLER / Sandbox</strong></span><small>A. Rivera</small></div><div class="activity-row"><time>14:24:40</time><span>Readiness checks passed for <strong>POLARIS / Team 03</strong></span><small>6 assets ready</small></div></section>`;}
function vector() {
 return `${heading("Range operations", "Six ranges. One needs attention.")}${tabs()}
 <section class="summary" aria-label="Range summary"><div><small>Total ranges</small><strong>06</strong></div><div><small>Ready</small><strong>04 <span>available to teams</span></strong></div><div><small>Needs attention</small><strong class="warning-number">01 <span>telemetry delayed</span></strong></div><div><small>Provisioning</small><strong>01 <span>started 6m ago</span></strong></div></section>
 <div class="split"><section><div class="section-top"><h2>Ranges</h2><small>Most urgent first</small></div>${filters()}${table()}</section><aside class="inspector" id="inspector" aria-label="Selected range">${inspector()}</aside></div>${activity()}`;
}
function topology() {
 return `<svg class="topology" viewBox="0 0 620 255" role="img" aria-labelledby="topology-title topology-desc"><title id="topology-title">POLARIS range dependencies</title><desc id="topology-desc">Access gateway connects to the domain controller. The domain controller connects to workstation-01 and workstation-02. Workstation-02 has delayed telemetry. Select a workstation in the resource list below to inspect it.</desc>
 <path class="edge" d="M148 126H238m145 0h34V65h31m-31 61v64h31"/><path class="edge warn" d="M383 126h34v64h31"/>
 <rect class="node" x="10" y="90" width="138" height="73" rx="4"/><path class="node-mark" d="M27 108h14v12H27zm7 12v5m-5 0h10"/><text x="26" y="143">Access gateway</text>
 <text class="sub" x="170" y="114">connects</text>
 <rect class="node" x="238" y="90" width="145" height="73" rx="4"/><path class="node-mark" d="M255 108h14v5h-14zm0 8h14v5h-14z"/><text x="254" y="143">Domain controller</text>
 <rect class="node ${resource === "workstation-01" ? "current" : ""}" x="448" y="30" width="160" height="72" rx="4"/><text x="462" y="59">workstation-01</text><text class="sub" x="462" y="82">READY · heartbeat 12s</text>
 <rect class="node ${resource === "workstation-02" ? "current" : ""}" x="448" y="153" width="160" height="72" rx="4"/><text x="462" y="182">workstation-02</text><text class="sub" x="462" y="205">DELAYED · heartbeat 4m</text>
 </svg>`;
}
function resourceInspector() {
 const delayed = resource === "workstation-02";
 return `<div class="inspector-nav">RESOURCE DETAILS</div><h2>${resource}</h2><p class="muted">Windows workstation</p>${status(delayed ? "degraded" : "ready")}<div class="notice ${delayed ? "" : "state-ready"}"><strong>${delayed ? "Telemetry is delayed" : "Telemetry is current"}</strong><p>${delayed ? "The machine is running, but its heartbeat is 4 minutes old. Check the agent connection before continuing." : "The latest heartbeat arrived 12 seconds ago. This workstation is ready for the exercise."}</p></div><dl><div><dt>Range</dt><dd>POLARIS / Team 04</dd></div><div><dt>Infrastructure</dt><dd>Running</dd></div><div><dt>Agent</dt><dd>${delayed ? "Last seen 14:28" : "Connected"}</dd></div><div><dt>Image</dt><dd class="mono">win-lab / 2026.09</dd></div><div><dt>Role</dt><dd>Participant workstation</dd></div></dl><button class="primary" data-action="connection">View connection checks</button><div class="brief-meta"><small>Selected resource stays in context while you inspect its dependencies.</small></div>`;
}
function clarity() {
 return `${heading("POLARIS / Team 04", "Lateral movement lab", "MISSION CONTROL / RANGES")}${tabs()}<div class="range-context"><span>${status("degraded")}</span><span><small>Assets ready</small><strong class="mono">5 of 6</strong></span><span><small>Region</small><strong class="mono">eu-west-1</strong></span><span><small>Owner</small><strong>Morgan Chen</strong></span><small class="mono">Range started 42m ago</small></div>
 <div class="investigation"><section><div class="section-top"><h2>Environment</h2><small>Dependency view · selected resources</small></div><div class="canvas"><div class="canvas-head"><span class="eyebrow">POLARIS / TRUST BOUNDARY</span><span class="status degraded">1 delayed connection</span></div><div id="topology">${topology()}</div><div class="canvas-legend"><span>— Connection</span><span>┄ Telemetry delayed</span></div></div><div class="section-top"><h2>Workstations</h2><small>Select to inspect</small></div><div class="resource-list"><div class="resource-row"><div><button data-resource="workstation-01" class="row-link">workstation-01</button><small>Windows · participant workstation</small></div>${status("ready")}</div><div class="resource-row"><div><button data-resource="workstation-02" class="row-link">workstation-02</button><small>Windows · participant workstation</small></div>${status("degraded")}</div></div></section><aside class="inspector" id="inspector" aria-label="Selected resource">${resourceInspector()}</aside></div>${activity()}`;
}
function meridian() {
 return `${tabs()}<section class="campaign" aria-label="Exercise context"><div><p class="eyebrow">EXERCISE 014 / NORTHSTAR LAB</p><h1>POLARIS</h1><p>Lateral movement lab · Four teams · Operator workspace</p></div><div class="campaign-aside"><small>Exercise window</small><strong>14:00—16:00</strong><small>14 September 2026 · UTC</small></div></section>
 <div class="page-heading"><div><h2>Exercise control</h2><p>Three teams ready. Resolve Team 04’s connection before the next phase.</p></div><button class="primary" data-action="brief">Open exercise brief →</button></div>
 <div class="command-grid"><section><div class="section-top"><h2>Run of show</h2><small class="mono">14:32 UTC · 88m remaining</small></div><div class="runline"><div class="phase done"><span class="eyebrow">01 / COMPLETE</span><h3>Provision</h3><small>13:45 · Resources created</small></div><div class="phase done"><span class="eyebrow">02 / COMPLETE</span><h3>Brief</h3><small>14:00 · Teams connected</small></div><div class="phase current"><span class="eyebrow">03 / IN PROGRESS</span><h3>Exercise</h3><small>14:15 · Lateral movement</small></div><div class="phase"><span class="eyebrow">04 / UPCOMING</span><h3>Debrief</h3><small>15:40 · Compare findings</small></div></div>
 <div class="command-table"><div class="section-top"><h2>Workspace ranges</h2><span class="status degraded">1 needs attention</span></div>${filters()}${table()}</div></section>
 <aside class="brief" id="inspector" aria-label="Selected range"><p class="eyebrow">OPERATOR NOTE / 01</p><h2>${selected.name}</h2><p>${selected.scenario}</p><div class="notice state-${selected.state}"><strong>${selected.cause}</strong><p>${selected.detail}</p></div><dl><div><dt>Ready assets</dt><dd class="mono">${selected.assets}</dd></div><div><dt>Owner</dt><dd>${selected.owner}</dd></div><div><dt>Region</dt><dd class="mono">${selected.region}</dd></div></dl><button class="primary" data-action="inspect">Review connection →</button><div class="brief-meta"><p>Next: compare findings at 15:40. Keep the range available through the debrief.</p></div></aside></div>`;
}
function identity() {
 return `${heading(`${config.name} / identity`, "A candidate direction for Shifter. Selection and final asset refinement are still open.", `STUDY ${concept.toUpperCase()}`)}${tabs()}<section class="brand-sheet"><div><div class="brand-hero"><span class="eyebrow">OPEN CYBER RANGE PLATFORM</span>${wordmark()}<p>${config.pitch}</p></div><div class="specimens" role="img" aria-label="Monochrome mark at 16, 32 and 64 pixels">${mark("tiny")}${mark("medium")}${mark("large")}<span class="muted">One color.<br>16 / 32 / 64 px.</span></div><div class="type-sample"><span class="eyebrow">${config.type}</span><h2>Read the situation.<br>Make the next move.</h2><p>POLARIS / Team 04 is ready for inspection.</p><p class="mono">rng-polaris-04 · eu-west-1 · 14:32:06 UTC</p></div></div><div class="brand-notes"><p class="eyebrow">DESIGN INTENT</p><h2>${config.pitch}</h2><p>${config.description}</p><div class="swatches" role="img" aria-label="Canvas, surface, accent, text and success colors"><span class="swatch bg"></span><span class="swatch surface"></span><span class="swatch accent"></span><span class="swatch ink"></span><span class="swatch state"></span></div><p class="mono">Canvas / Surface / Accent / Ink / Success</p><h3>Gradient discipline</h3><p>${config.gradient}</p><h3>Voice</h3><p>${config.voice}</p><div class="voice"><h3>Connection interrupted</h3><p>The range is still running. Reconnect to continue your session.</p><p><strong>Empty:</strong> No ranges yet. Create a range from a scenario to get started.</p></div><p><small>Original vector studies. Local fonts retain their SIL OFL 1.1 notices. Geometry icons are temporary study drawings; production keeps Lucide. See <a href="README.md">asset provenance</a>.</small></p></div></section>`;
}
function render() {
 document.querySelector("#workspace").innerHTML = (currentView === "identity" ? identity() : {a:vector,b:clarity,c:meridian}[concept]()) + footer();
 drawRows();
 const search = document.querySelector("#range-search"); if(search) search.value = query;
 const select = document.querySelector("#status-filter"); if(select) select.value = filter;
}
function announce(message) { document.querySelector("#announcement").textContent = message; }
function showDialog(title, content) {
 document.querySelector("#dialog-title").textContent = title;
 document.querySelector("#dialog-body").innerHTML = content;
 document.querySelector("#demo-dialog").showModal();
}
function demoAction(action) {
 const messages = {
  brief: ["POLARIS / exercise brief", '<p>Practice lateral movement across an isolated lab. Four teams have a two-hour exercise window.</p><dl><div><dt>Exercise</dt><dd>14:15–15:40 UTC</dd></div><div><dt>Debrief</dt><dd>15:40–16:00 UTC</dd></div></dl><p>Before the next phase, inspect Team 04’s delayed workstation connection. This brief is fictional; no exercise is running.</p>'],
  launch: ["Create a range", '<p>This is a design preview of the creation entry point.</p><dl><div><dt>Scenario</dt><dd>POLARIS v2.4</dd></div><div><dt>Resources</dt><dd>6 assets</dd></div><div><dt>Region</dt><dd>eu-west-1</dd></div></dl><p>No infrastructure will be created. A production flow would collect the range name and configuration, then confirm the request.</p>'],
  inspect: [selected.name, `<p>${selected.cause}. ${selected.detail}</p><pre>Infrastructure   running\nAssets ready     ${selected.assets}\nRegion           ${selected.region}\nObserved         14:32 UTC (fixture)</pre><p>Design preview only. No connection or remote command is executed.</p>`],
  connection: [`${resource} / connection checks`, `<pre>Virtual machine   running\nNetwork route     available\nAgent heartbeat   ${resource === "workstation-02" ? "delayed — last seen 14:28" : "current — last seen 14:31:48"}</pre><p>These are fictional observations. No checks are run against infrastructure.</p>`]
 };
 if(messages[action]) showDialog(...messages[action]);
 if(action === "clear") { query="";filter="all";render();document.querySelector("#range-search").focus();announce("Filters cleared. Six ranges."); }
}
document.addEventListener("click", event => {
 const button = event.target.closest("button"); if(!button) return;
 if(button.dataset.view) { currentView = button.dataset.view;render();document.querySelector(`[data-view="${currentView}"]`).focus(); }
 if(button.dataset.range) {
  selected = ranges.find(r => r.id === button.dataset.range);
  if(concept === "c") render(); else { drawRows();document.querySelector("#inspector").innerHTML=inspector(); }
  document.querySelector(`[data-range="${selected.id}"]`)?.focus();announce(`${selected.name} selected. ${selected.cause}.`);
 }
 if(button.dataset.resource) { resource=button.dataset.resource;document.querySelector("#topology").innerHTML=topology();document.querySelector("#inspector").innerHTML=resourceInspector();announce(`${resource} selected.`); }
 if(button.dataset.action) demoAction(button.dataset.action);
});
document.addEventListener("input", event => { if(event.target.id === "range-search") { query=event.target.value;drawRows();announce(document.querySelector("#result-count").textContent); } });
document.addEventListener("change", event => { if(event.target.id === "status-filter") { filter=event.target.value;drawRows();announce(document.querySelector("#result-count").textContent); } });
function themeLabel() { document.querySelector("#theme-toggle").textContent = root.dataset.theme === "dark" ? "Light mode" : "Dark mode"; }
document.querySelector("#theme-toggle").addEventListener("click", () => { root.dataset.theme=root.dataset.theme === "dark" ? "light" : "dark";themeLabel(); });
themeLabel();
shell();
