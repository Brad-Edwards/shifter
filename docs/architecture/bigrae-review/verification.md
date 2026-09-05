# Review evidence and verification

## Baseline and collection

Reviewed `dev` commit
[`b9b82681818fed7da26fcedaa93d37586bc14c74`](https://github.com/Brad-Edwards/shifter/commit/b9b82681818fed7da26fcedaa93d37586bc14c74)
on 2026-09-05. The issue branch changes review documentation only. It neither
deploys the recommendations nor changes operative ADR rules.

The inventory includes 4,121 tracked files, 52 ADR records, 278 rules and 41
exceptions. All 282 open issue bodies, 134 comments and 39 baseline native
dependency edges were collected through the GitHub API, along with all 34
milestones and the three open PRs. The complete issue disposition is in
[the backlog](backlog.md). Counts are a dated baseline, not current dashboard totals.

Source review traced admission, auth, ingestion, lifecycle persistence, worker
effects, remote access, cleanup, cloud identity/network configuration, image and
release workflows, frontend journeys, MCP authority and contributor checks.
Historical preflights and issue claims were compared with current implementation.
This is coverage of every major subsystem, not an assertion that all 4,121 files
received equally detailed line-by-line analysis.

## Local results

| Verification | Observed result | Meaning / limit |
| --- | --- | --- |
| `make test` | Exit 0 | All lanes actually included in this target passed; it omits several separate lanes |
| Platform SQLite lane | 6,946 passed, four warnings | Local behavior coverage; no live provider or Redis proof |
| Provisioner lane | 1,885 passed, eight skipped | Provider seams tested locally; skipped cases remain unverified |
| Packer helpers | 241 passed | Does not bake or boot a cloud image |
| Installation CLI | 582 passed | Does not replace fresh-project rehearsal |
| Bootstrap scripts | 474 passed | Does not establish effective cloud permissions |
| Layer-import checker tests | 67 passed | Repository policy behavior |
| Helm contract tests | 14 passed, 30 subtests passed | Rendered profile contracts |
| Legacy platform JavaScript | 344 passed across 14 suites | This lane is distinct from the SPA |
| ADR guard tests | 474 passed | Policy runner behavior, not all runtime architectural properties |
| PostgreSQL-marked platform tests | 122 passed with two workers | Real PostgreSQL concurrency/locking subset; not the full platform PostgreSQL lane |
| SPA lint and typecheck | Passed | Static frontend checks |
| SPA test/coverage rerun | 497 passed across 79 files | Two-worker rerun; initial run had one five-second timeout |
| SPA coverage | 78.72% statements, 70.57% branches, 73.77% functions, 80.15% lines | Collection exists; these are measurements, not configured thresholds |
| SPA production builds | Passed | Bundle-size warnings remain; no live authenticated browser journey run |
| MCP lint | Passed in shared, NGFW, ops and planner | Privileged tools were not invoked against live infrastructure |
| MCP tests | NGFW 31, ops 325, planner 39, shared schema 2 passed | Shared tests invoked directly because that package has no test script |
| Import Linter | Eight contracts kept | Current service-boundary graph |
| Ruff lint / format check | Passed; 1,178 files already formatted | Existing platform static baseline |
| `actionlint` | Passed | Workflow syntax/static semantics |
| Recursive TFLint | Passed | Terraform lint, not plan/apply/effective IAM |
| KubeLinter | No lint errors | Configured policy scope |
| Kubeconform | 37 resources: 36 valid, one skipped, zero invalid/errors | Strict Kubernetes 1.31 validation with missing schemas ignored as configured |
| Architecture guard, baseline | All 30 selected checks passed | Required `--all --level ci` baseline |

The first SPA run failed only
`CredentialsPage.test.tsx`'s “creates an SCM credential with exact serializer
fields” test on its five-second timeout while other suites ran concurrently.
The complete rerun with `--maxWorkers=2` passed; this suggests load sensitivity,
not a proven root cause. Both runs emitted jsdom canvas/color-contrast warnings.
They do not establish browser accessibility. #1526 owns repeatability and real
browser coverage. The initial failure is not hidden by the rerun.

## Reproduction details

Use `make test` for the listed aggregate lanes. The additional PostgreSQL run
used an owned, loopback-only PostgreSQL 16 test container on port 55480 and
disposable test credentials, then removed the container and its volume.
The image digest was
`sha256:f1c3376c26f2609ab9f29f71f824103fe2fcd8ee0346485cb6122a4f93df6f94`.
From `shifter/shifter_platform`, the selected command was:

```bash
TESTING=1 DJANGO_DEBUG=true TEST_DB_BACKEND=postgres \
DJANGO_SECRET_KEY=shifter-review-test-key DB_HOST=127.0.0.1 DB_PORT=55480 \
DB_NAME=shifter DB_USER=test DB_PASSWORD=test \
uv run pytest tests/ -m postgres -n 2 --create-db
```

The SPA used `npm ci`, its lint/typecheck scripts,
`npm run test:coverage -- --maxWorkers=2`, and its production build script.
Each MCP package used `npm ci` and `npm run lint`; the three packages with test
scripts used `npm test`. Shared used
`node --test mcp/shared/tool-schema-dialect.test.js` from repository root.

Native checks followed `AGENTS.md`, including the repository TFLint config and
strict Kubeconform options. Final `adr_guard --all --level ci` passed all 30
selected checks. Vale reported no errors, warnings or suggestions in the six
changed ADR files. Local Markdown targets resolve and `git diff --check` passed.
The complete `make policy` target also passed, including all eight import
contracts and prose checks across the eleven changed Markdown files.
The review added 46 native blocking relationships, checked the combined graph
for cycles and confirmed every planned edge through API readback. It clarified
23 existing issues and moved focused prerequisites into the three new milestones.

## Remote evidence and limitations

The reviewed commit's
[Sonar dashboard](https://sonarcloud.io/dashboard?id=Brad-Edwards_shifter&branch=dev)
reported a failed analysis gate: eight new issues and an E new-code security
rating. The scanner workflow job itself succeeded. Repository security API
collection reported 95 dependency alerts (50 high, 35 medium, 10 low) and eight
medium code-scanning alerts. Default-branch, manifest, tool-only and duplicate
effects require artifact-specific triage in #2084. Counts are not an exploit tally.

No live GCP deployment, real escape exercise, Terraform apply, image bake,
full PostgreSQL platform run, Redis integration run or authenticated Playwright
end-to-end run was performed for this assessment. No production security stack
was installed. Public upstream sources support the sandbox comparison; no runtime
candidate was benchmarked locally. #2091 and #2092 explicitly retain the missing
release evidence as required work.

Review source artifacts and command logs were kept locally during collection;
only sanitized findings, reproducible commands and dependency metadata are
committed. Existing issue/PR closure and release approval remain separate actions.
