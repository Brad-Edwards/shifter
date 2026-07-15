# ACES Polaris Scenario-Smoketest Coverage Preflight

Issue: GitHub #1293, "ACES migration: expand Polaris scenario-smoketest
coverage for cutover universe."

Status: pre-implementation architecture guidance. This note does not add
adapters, tests, challenge metadata, or a cutover workflow. The issue is
requirement-free; its GitHub contract and the parent #1237 acceptance note are
authoritative.

## Decision And Boundary

Extend the existing `scenario-dev/polaris/tests/scenario_smoketest` harness.
Do not create a second smoke framework, challenge schema, flag parser, CTFd
client, range executor, or evidence format.

The cutover universe is the merged set of challenges from the manifest inputs
declared for the acceptance invocation: the core `ctfd-challenges.json` plus
each explicitly selected onboarding or campaign manifest. Record those input
paths, content digests, and logical manifest ids with the result. Do not add a
separate universe manifest. `--only` remains a diagnostic rerun filter and
cannot produce cutover evidence; a qualifying run must evaluate the complete
declared input set.

Adapter keys are logical source-manifest ids. They are not live CTFd ids.
Live ids remain confined to read-only CTFd API reconciliation because CTFd can
assign different ids during sync. Challenge names, flags, categories, hints,
points, and prerequisites remain owned by the source manifests.

Keep one result/report contract. Extend `ChallengeResult`, `summarize`, the
aggregate exit decision, stdout, and `--json-report` together if a `blocked`
classification is needed. `blocked` means the selected board or range requires
a content/ACES capability the harness cannot represent safely; it is a failing
terminal result, not a skip. `uncovered` continues to mean a supported
challenge shape has no adapter. A cutover-grade run has only `pass` results.

## Canonical Incumbents To Reuse

| Concern | Canonical incumbent | Required reuse |
| --- | --- | --- |
| Parent acceptance | `docs/architecture/aces-polaris-acceptance-parity-gate-preflight-1237.md` and ADR-024 | This report is one blocking artifact in the larger parity gate; it does not independently authorize cutover. |
| Harness contracts | `scenario_smoketest.board`, `adapters`, `runner`, `compare`, `run`, and `report` | Extend these seams in place; do not add parallel DTOs, registries, result enums, or serializers. |
| Board validation | `scripts/ctfd-workshop/polaris_manifest.py::validate_manifest` and `SUPPORTED_FLAG_TYPES` | Run the canonical merged-manifest validation before coverage classification. The smoketest may add execution-specific shape checks but must not reimplement board policy. |
| Static-value semantics | `scenario_smoketest.board._static_flag` and the bake verifier contract cited by #617 | Factor/reuse one extraction rule. Do not let sync, bake, and range checks drift into three flag parsers. |
| Challenge metadata and prerequisite graph | `ctfd-challenges.json`, optional `ctfd-onboarding.json`, and their `requirements.prerequisites` | Preserve required fields in the derived board DTO; do not restate dependency order in adapter modules. |
| Participant solution material | `tests/walkthroughs/*` plus `tests/smoketests/*` | Walkthroughs describe intent; reviewed Python adapters execute it. Factor extraction/protocol behavior from the per-asset tests without invoking their raw-output reporting. Never execute Markdown. |
| Pivot topology | `scenario-dev/polaris/README.md` asset pivot map, `tests/run-all-smoketests.sh`, `tests/reset.sh`, and `tests/isolation-smoketest.sh` | Use the existing names, reset behavior, isolation contract, and participant routes. Do not attach networks or docker-exec directly into a target to bypass a pivot. |
| Command boundary | `scenario_smoketest.runner.Runner` / `ExecResult` | Preserve argv-array execution, container-name validation, bounded timeouts, and captured output. Add behavior here only when it is common to adapters. |
| Comparison/reporting | `scenario_smoketest.compare` and `scenario_smoketest.report` | Keep expected/produced values in memory only and render verdicts or approved fingerprints. No adapter-specific printing. |
| CTFd readback | `scripts/ctfd-workshop/common.py::CtfdClient`, `polaris_manifest.verify_challenge_rows`, and `scenario_smoketest.ctfd_check` | Reuse headers, timeout, pagination, and readback behavior. Translate its payload-bearing exceptions before they reach stderr; never mutate CTFd. |
| Log/terminal field safety | `shared.log_sanitize.safe_log_value` semantics and its tests | Any manifest-controlled text retained in human output must be bounded and single-line/control-safe. Do not treat this as secret redaction; secret-bearing values must never enter result details. |
| Architecture gates | `.ground-control.yaml`, `.gc/plan-rules.md`, `AGENTS.md`, and `scripts/adr_guard/adr_guard.py` | Keep existing enforcement. No workflow or guardrail weakening is part of this issue. |

## Topology And Stateful Execution Contract

The host may orchestrate with Docker, but challenge discovery begins at the
participant's A14 boundary and follows the same pivots used during range time:

| Challenge surface | Required participant route |
| --- | --- |
| Shared/corporate, A0-A4 | A14 to the addressed service |
| A15 compromise and SCADA | A14 to A15, then A15 to A5; reaching A5 by host-side `docker exec` is not participant-path evidence |
| A16 compromise and Lab | A14 to A16, then A16 to A6/A7/A8; direct A14-to-Lab or host-to-target access is a failure |
| Bunker | A14 over the range's post-meltdown splice to A9, then A9 to A10-A13; direct A14-to-controller or host-to-controller access is a failure |
| Isolation/orchestration | Range host, using the existing isolation sweep only |

The adapter registry's `runner` is executable policy, not documentation. The
runner selected by the registry must be the container used by `Runner.exec` or
the start of an explicit remote-pivot chain. Do not repeat a runner constant in
both registration metadata and adapter bodies without an equality gate. Tests
must prove the selected container and the required remote hops, not merely that
the registry string looks correct.

Run the existing reset once at the acceptance boundary, then preserve the
board's prerequisite order through the full scenario sweep so stateful paths
such as the A5 meltdown and splice opening remain observable. Do not reset
between dependent challenges. A diagnostic subset must either include its
prerequisite closure or report a blocked prerequisite; it must not claim
cutover evidence from pre-seeded state.

## Value And Capability Contract

Keep these concepts distinct:

- discovered artifact: the data obtained by following the participant path;
- submission answer: the value a participant would submit;
- configured board flag: the source-manifest flag row used by CTFd;
- diagnostic evidence: a non-secret reason code and bounded identifiers.

`Adapter.value_kind` owns comparison selection. `Produced.kind` currently
duplicates that decision; the implementation must either remove it or validate
exact equality before comparison. An adapter may carry an `expected_answer`
only when the participant answer is intentionally different from the board's
static flag and no canonical manifest field owns it, as with the existing
device-model concatenation. That value must stay derived from the existing
walkthrough/per-asset contract rather than becoming general challenge
metadata.

Reject or classify as `blocked` before execution when a selected challenge
uses an unsupported flag type, ambiguous flag set, dynamic value, interactive
content, missing prerequisite semantics, or ACES/Polaris content feature for
which no safe comparator and participant-path adapter exists. Do not coerce a
regex to static equality, convert a missing target to an empty string, or turn
unsupported content into `uncovered`, `pass`, or an omitted row.

## Cross-Cutting Layers The Design Must Pass

- **Auth surface:** this remains an operator-run CLI against an authorized
  staged range. It adds no Django/DRF, participant, CTF, or Mission Control API
  and no new authorization model. Optional live-board access remains read-only
  through `CtfdClient`.
- **Manifest/parser shape:** parse JSON normally, merge the explicitly declared
  inputs, then reuse `polaris_manifest.validate_manifest`. Preserve logical id,
  name, category, supported flag shape, and prerequisites in one derived board
  contract. Duplicate ids/names, malformed fields, unknown prerequisite ids,
  or unsupported capabilities fail closed before range execution.
- **Secret-handling surface:** raw flags, submission answers, credentials,
  tokens, private keys, prompt bodies, commands containing secrets, terminal
  streams, and provider/CTFd payloads may exist transiently inside an adapter
  but must not enter `Produced.note`, `ChallengeResult.detail`, stdout, stderr,
  JSON, exceptions, logs, or tracked artifacts. Hash/fingerprint output is
  allowed only for flag/answer comparison; do not hash low-entropy credentials.
- **Token/config surface:** accept no CTFd token option. Continue to read
  `CTFD_TOKEN` or a restrictive token file. Validate token-file type,
  ownership/permissions as appropriate for the operator environment, and fail
  closed when a requested readback lacks a token. The current successful
  "skipping readback" behavior is not cutover-grade evidence.
- **OS/process exposure:** keep subprocess calls as argv arrays with
  `shell=False`. Host argv and Docker exec argv must carry ids, paths, hosts,
  and non-secret options only. Do not place credentials, private-key bodies,
  flags, tokens, prompts, or shell fragments in argv or environment overrides.
  Use participant-visible credential files, stdin, or restrictive temporary
  files inside the runner and clean them up.
- **Range/config shape:** reuse `RANGE_DIR`, `COMPOSE_PROJECT_NAME`,
  `SMOKETESTS_DIR`, existing container/host names, DNS defaults, reset, and
  isolation behavior. Explicit CLI overrides are the edge seam; do not add
  adapter-local environment reads or a second topology config.
- **Error envelope:** adapter failures become closed, bounded reason codes plus
  approved context such as logical challenge id, adapter id, runner id, and
  exit class. Never concatenate `stdout`, `stderr`, exception text, remote API
  bodies, command text, or free-form `Produced.note` into a report. Catch and
  translate `CtfdClient` errors because its incumbent exception includes raw
  response bodies.
- **Observability:** report manifest digests, logical ids, adapter ids, runner
  ids, status counts, durations, comparison verdicts/fingerprints, and report
  version. Manifest-controlled names, if retained, use bounded control-safe
  rendering. No new logger, audit store, database, event, or telemetry pipeline
  is needed.
- **Persistence/artifacts:** the optional JSON report is the only new durable
  output in this scope. Write it with restrictive permissions and atomic
  replacement, and never archive raw command output. Do not persist results in
  CMS/engine models, CTFd, an ACES sidecar, `AuditLog`, or the parity inventory.
- **Validation/workflow:** unit tests cover manifest/universe classification,
  runner selection and remote hops, comparison modes, prerequisite/blocker
  behavior, aggregate failure, and adversarial redaction. Live validation uses
  the existing reset, scenario sweep, asset sweep, and isolation checks. This
  remains operator-run and is not added to push/PR CI by this issue.

## Extensibility Seam

Keep variation at three existing edges:

- manifest inputs and their digests define the declared universe;
- the minimal adapter registry selects logical challenge id, participant
  runner, comparison kind, and callable;
- `AdapterContext` carries topology/timeout inputs and the runner service.

The next mission, onboarding set, or scenario-content capability should add a
manifest input and adapters/comparators behind those seams. It must not require
editing the report schema per mission, copying challenge metadata into Python,
or adding scenario branches to CTFd sync, CMS, engine, Mission Control, ACES
contracts, or provisioner code. If ACES itself cannot express the needed
content capability, stop with a blocker and file that upstream contract gap;
do not encode a private Shifter extension in the smoke harness.

## Gotchas And Anti-Patterns

- The current adapter registry declares a runner, while adapter functions also
  hardcode runner constants. Treating the declaration as decorative can make
  runner-selection tests pass while execution uses the wrong topology.
- Current `Produced.note` is free-form and is appended directly to reports.
  New adapters must not place observed permissions, remote values, exception
  text, output excerpts, or credentials there; prefer closed reason codes.
- `CtfdClient` includes HTTP response bodies in raised errors. An uncaught
  traceback can violate the redacted-output contract even though normal result
  rendering is safe.
- Existing per-asset smoketests often hardcode/print flags, credentials, raw
  registers, or remote output. They are solution sources, not safe report
  subprocesses. Do not invoke them wholesale and capture their transcript.
- A host-side `docker exec` into A15, A16, A9, or a target proves service health
  but can bypass the participant compromise/pivot. Do not count that as
  scenario-path coverage.
- Do not add networks, pre-open the splice, weaken isolation, pre-copy loot, or
  patch range files to make adapters pass. A topology defect is a failed gate.
- Do not silently skip a missing token, missing tool, unsupported flag type,
  unavailable ACES capability, failed prerequisite, or absent range service.
- Do not confuse a zero-`uncovered` report with acceptance if it contains
  `fail`, `error`, or `blocked`, or was produced with a subset filter.
- Do not add a generic workflow engine, shell DSL, plugin system, new exception
  hierarchy, Pydantic duplicate of the CTFd manifest, or database repository
  for this finite Polaris coverage expansion.

## Whole-Repo Scope And Non-Goals

Future implementation must evaluate the harness against
`scenario-dev/polaris/tests/scenario_smoketest/**`, its unit tests,
`tests/smoketests/**`, `tests/walkthroughs/**`, `run-all-smoketests.sh`,
`reset.sh`, `isolation-smoketest.sh`, `scenario-dev/polaris/README.md`, the
runtime build manifests, `scripts/ctfd-workshop/{common,polaris_manifest,
sync_polaris_ctfd,sync_polaris_ctfd_onboarding}.py`, the three cited parity
inventory rows, and the #617/#1237 architecture notes.

This issue does not change challenge content, flags, hints, credentials,
prerequisites, scoring, CTFd sync behavior, ACES schemas/profiles, provisioning,
network policy, range bootstrap, CMS/engine/Mission Control/CTF behavior,
cutover selectors, evidence-bundle assembly, CI workflows, or ADR enforcement.
It does not launch or mutate a live range or board. No new Ground Control
requirement is created for this requirement-free run.

For this documentation-only preflight, run:

```bash
python3 scripts/adr_guard/adr_guard.py --all --level ci
```
