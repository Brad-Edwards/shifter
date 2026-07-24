# Optional Caldera Range Setup Preflight (#393)

Status: pre-implementation guidance

Date: 2026-07-24

Tracking issue: <https://github.com/Brad-Edwards/shifter/issues/393>

## Scope Boundary

Issue #393 is requirement-free; the GitHub issue is the shipping contract:
optionally start the baked Caldera C2 server on the attacker Kali instance and
deploy sandcat agents to the other VM instances during range provisioning.

This note does not implement the feature and is not an implementation plan. It
records the repository boundaries the later implementation must preserve.

## Architecture Decisions

- Model Caldera as optional range runtime setup, default off. The toggle belongs
  in the canonical validated range/scenario launch contract and must be
  persisted with the range request so retries, GCP job dispatch, local
  provisioner runs, and teardown/recovery see the same intent.
- Do not add a new provisioner command, user-supplied CLI flag, task env switch,
  Terraform-only variable, or EC2/GCE user-data branch for this behavior. The
  privileged launch shape remains `range provision --request-id`; the
  provisioner reads the persisted range artifact after infrastructure exists.
- Keep the concept separate from Cortex XDR "agents". In this repository,
  `AgentConfig`, `AgentDetails`, `agents_by_os`, `agent_presigned_url`, and
  `plans/*xdr_agent_install.py` mean the user-uploaded XDR installer flow.
  Sandcat is Caldera runtime setup, not an asset upload or XDR agent.
- Start the existing Kali image asset. `shifter/packer/scripts/kali/caldera.sh`
  installs Caldera under `/opt/caldera` and creates
  `/usr/local/bin/start-caldera`; provisioning must not clone Caldera, install
  packages, run `pip`, or depend on public internet at range runtime.
- Use the existing guest setup framework: `SetupStep`, `SetupPlan` or
  `DynamicPlan`, `SetupOrchestrator`, `GuestExecutionContext`, and the provider
  executors. Do not create a second SSH/SSM/PowerShell runner, retry loop,
  exception hierarchy, or command-template renderer.
- Enabling Caldera must require exactly one reachable Kali attacker target for
  the callback endpoint. Agents call the attacker's private IP on the configured
  callback port, default `8888`. Do not expose Caldera to the portal, public
  internet, or participant access plane as part of this issue.
- Preserve scenario topology as the network authority. AWS security groups and
  GCE firewalls already allow intra-subnet traffic and declared
  `connected_to` peer subnet traffic. Do not add a global port-8888 ingress
  rule to make an incompatible scenario work; fail closed or require the
  scenario to declare the needed connectivity.
- Caldera startup and sandcat deployment are strict when enabled. If the server
  cannot start, the API cannot produce agent binaries, a target cannot download
  or execute sandcat, or verification fails, the range should fail through the
  existing setup/provisioning error path unless a future issue explicitly adds
  a best-effort mode.
- Windows Defender posture is an explicit security choice, not an incidental
  script side effect. Prefer a path-scoped exclusion for
  `C:\Users\Public\sandcat.exe`; allow disabling real-time monitoring only as a
  named policy value with clear logging and tests. Do not silently weaken
  Defender for every Windows range.

## Canonical Incumbents

| Concern | Canonical incumbent | Guardrail for #393 |
| --- | --- | --- |
| Product entry points | Mission Control/CMS `cms.services.create_range`; CTF `ctf.bridges.cms_create_range` | Add any caller option through the existing CMS service/bridge boundary; CTF must not call Engine or provisioner internals. |
| Scenario authoring schema | `cms.scenarios.schema.ScenarioTemplate`, `cms.scenarios.hydrator.hydrate_scenario` | Validate authored opt-in fields here if scenario YAML controls the feature. Do not accept untyped extension bags. |
| Persisted range contract | `cyberscript.schemas.range.RangeSpec`, `shared.schemas.range`, `wrap_persisted_spec`, `build_scenario_artifact` | Add any runtime setup field to the canonical schema, then let CMS/Engine/GCE artifact validation normalize it. |
| Wire-key canaries | `cyberscript/wire_spec_keys.py`, `cyberscript/tests/test_wire_contract.py` | If provisioner dict-walks a new key, register it so schema/key drift fails tests. |
| Privileged task launch | `engine.launch_intents`, `engine.ecs.start_range_provisioning`, `provisioner main.py` | Keep the canonical `range provision --request-id` shape secret-free and state-authorized. |
| Guest command transport | `executors.factory.build_guest_execution_context`, `SSMExecutor`, `GuestSSHExecutor`, `RangePodSSHExecutor` | Reuse provider-routed transports, shell family selection, readiness waits, and close semantics. |
| Setup orchestration | `plans.base`, `orchestrators.setup_orchestrator`, `instance_setup.py`, `dc_setup.py`, `instance_orchestrator.py` | Implement Caldera as setup phases with existing retries, verification, timeout, and `SetupError` behavior. |
| Kali Caldera asset | `shifter/packer/scripts/kali/caldera.sh`, Kali Packer template | Start and verify the baked `/opt/caldera` install; do not perform runtime installation. |
| Network topology | AWS range module SG/route rules; `gcp_range_cell_firewall.py` | Use attacker private IP and declared east-west reachability; no public exposure or global 8888 opening. |
| XDR installer flow | `cms.models.AgentConfig`, `agent_assets.py`, `plans/linux_xdr_agent_install.py`, `plans/xdr_agent_install.py` | Use only as style reference for cross-OS plans. Do not reuse its schema, S3 presigned URL fields, or best-effort GDC policy for sandcat. |
| Logging and redaction | `SetupOrchestrator._mask_sensitive_output`, `log_redact.safe_log_value`, `safe_log_fingerprint` | Log counts, step names, and sanitized IDs. Do not log Caldera API keys, generated tokens, full URLs with credentials, or Defender scripts containing secrets. |
| IAM/management scope | Engine provisioner SSM IAM policy and `scripts/check_tf_iam_ssm_scope` | Guest setup uses existing Run Command documents and tag-scoped instance access; do not broaden provisioner IAM for this feature. |

## Cross-Cutting Layers

- Auth surface: no participant auth, Guacamole access, API-token, or direct CTF
  auth change is required. If an HTTP/UI option is added, it must pass through
  the existing Django view/form/serializer authorization, then CMS service
  validation, then the CTF bridge for CTF events. The option must never be a
  client-supplied value passed directly to Engine or the provisioner.
- Config and schema validation: authored scenario fields pass
  `cms.scenarios.schema`; hydrated runtime intent passes
  `RangeSpec.model_validate` and `wrap_persisted_spec`; Engine binds it through
  `build_scenario_artifact`; GCE validates the digest-bound artifact with
  `validate_gcp_vm_range_cell_request`. Unknown fields must fail or normalize
  consistently rather than being dropped on one provider and honored on
  another.
- Launch-intent validation: `engine.launch_intents.validate_provisioner_command`
  continues to accept only canonical, secret-free lifecycle commands. Caldera
  configuration is not command-line state and must not add argv exposure for
  API keys, callback URLs, Defender policy, or target lists.
- Secret-handling surface: the documented Caldera default key `ADMIN123` is a
  lab default, not a platform secret contract. If the implementation needs an
  API key or generated token, keep it out of Terraform state, DB range specs,
  process argv, environment dumps, SSM command history where possible, and
  logs. Context keys containing `token`, `secret`, or `password` get masked,
  but masking is only defense in depth.
- Env-binding surface: avoid new env if the value is per-range intent. If a
  deployment-level non-secret default is truly needed, add it through the
  provisioner config loader and GCP Job env projection in `engine/ecs/_env.py`
  rather than reading `os.environ` ad hoc from setup scripts.
- OS/runtime exposure: Linux sandcat should be left at `/tmp/sandcat.go-linux`
  and Windows sandcat at `C:\Users\Public\sandcat.exe` per the issue. Any
  launch mechanism must be idempotent, avoid shell tracing, avoid secrets in
  command argv/process listings, and avoid world-writable restart scripts that
  contain credentials.
- Windows security posture: Defender exclusion or real-time disablement touches
  host security state. It must be opt-in with Caldera, scoped to the sandcat
  path when possible, visible in provisioning logs without leaking secrets, and
  verified before trying to execute sandcat.
- Network/security policy: AWS SGs, route tables, NGFW attachment routes, GCE
  firewall rules, zero-egress posture, and OpenVPN rules all see the artifact.
  Caldera traffic is in-range east-west traffic to the attacker private IP; it
  must not alter public ingress, portal management ingress, egress posture, or
  NGFW semantics.
- Error envelope: failures use `SetupError`, executor exceptions,
  `SetupResult`, range failed status publication, and existing Terraform
  cleanup paths. User-visible or event payload errors should name the failed
  phase generically and avoid raw command output, full scripts, API responses,
  API keys, or host security details.
- Observability: useful logs are enabled/disabled state, attacker fingerprint,
  target counts by OS/role, step success/failure, and sanitized instance
  fingerprints. Do not log full rendered scripts, Caldera responses containing
  credentials, generated URLs with tokens, or participant-visible secrets.
- Persistence: no new table or repository is needed for this issue. Persist
  the requested runtime setup intent with the range spec and leave runtime
  artifacts on the guests as required. Do not persist sandcat runtime status as
  a parallel range lifecycle unless a later issue designs that projection.

## Extensibility Seam

The seam is a small validated Caldera runtime setup profile on the range
contract. It should be parameterized for the next obvious variation without
editing every provisioning artifact:

- `enabled`, default `false`;
- callback port, default `8888`;
- target role set, default victims and DCs, explicitly excluding the attacker;
- Windows Defender mode, for example path exclusion versus real-time disable;
- server start path or profile name if future Kali images change layout.

Keep secrets, generated tokens, and opaque API credentials out of this profile.
Future C2 variants, a different Caldera port, Linux-only or Windows-only
deployment, a safer Defender policy, or an operator-visible scenario default
should extend this profile and the existing setup-plan phase rather than adding
new provisioner commands, duplicated schemas, or provider-specific toggles.

## Whole-Repository Scope

- CMS/CTF launch paths: `cms.services._range_create`, `ctf.bridges`,
  `ctf.services.range.*`, CTF event `range_config` if the event form exposes
  the option.
- Scenario and Range DSL: `cms.scenarios.schema`, `cms.scenarios.hydrator`,
  `cyberscript.schemas.range`, `shared.schemas.range`, persisted-spec tests,
  and wire-key canaries.
- Engine dispatch and persisted artifacts: `engine.services._range`,
  `engine.launch_intents`, `engine.ecs`, `shared.range_cells`.
- Provisioner lifecycle: `main.py`, `terraform_ops.py`, `terraform_vars.py`,
  `instance_orchestrator.py`, `instance_setup.py`, `dc_setup.py`, setup plans,
  executors, and provisioner tests.
- Range networking: AWS runtime range Terraform, stable range host IAM/SSM
  policy, GCE range-cell firewall planning, GDC pod-vs-VM partitioning.
- Image/runtime asset ownership: Kali Packer scripts and tests for the baked
  Caldera install.
- Guardrails: ADR guard, import-linter for `shifter_platform`, SSM IAM scope
  checks if IAM changes, Terraform linters if network/IAM changes, and
  actionlint if workflows change.

## Gotchas And Anti-Patterns

- Do not conflate sandcat with XDR agents or reuse the XDR upload/S3 URL schema.
- Do not hardcode Caldera setup as always-on. The issue requires opt-in.
- Do not install Caldera, Go, Python packages, or Caldera plugins at range
  runtime; the Kali AMI bake owns that dependency.
- Do not assume every range has one attacker. Enabled Caldera must reject zero,
  multiple, non-Kali, or unreachable attacker targets unless the schema later
  gains an explicit target selector.
- Do not deploy sandcat to the attacker itself. The requirement says all other
  instances.
- Do not target GDC scenario pods through the VM guest setup path. Pod-backed
  assets need a separate lifecycle design if they enter scope.
- Do not add broad port-8888 public ingress, portal ingress, or new egress
  routes. Scenario topology remains authoritative.
- Do not disable Windows Defender globally as a hidden side effect or leave the
  range ready after Defender policy application failed.
- Do not put API keys, tokens, or full callback URLs with credentials in argv,
  Terraform values, user data, DB specs, stdout/stderr, or logs.
- Do not make enabled Caldera best-effort by copying the GDC XDR exception.
  Sandcat is the requested feature, so failure should fail the range unless a
  future contract says otherwise.
- Do not duplicate validation in CMS, Engine, and provisioner with divergent
  rules. The schema owns shape; provider/setup boundaries re-check only runtime
  facts such as attacker private IP and reachability.
- Do not introduce a new exception hierarchy, persistence table, task queue, or
  provisioner subcommand for this runtime setup phase.

## Non-Goals

- No implementation is performed by this preflight.
- No public Caldera operator portal, NAT rule, Guacamole endpoint, VPN routing
  change, or internet exposure for the Caldera UI/API.
- No replacement of the Cortex XDR agent pipeline.
- No new C2 framework abstraction beyond a minimal Caldera runtime profile.
- No redesign of range lifecycle, Terraform ownership, GCP range-cell
  contracts, CTF scheduling, or participant access.
- No change to NGFW behavior, zero-egress posture, stable range VPC service
  endpoints, or host IAM scope unless the implementation discovers an explicit
  compatibility bug.

## Validation Expectations

For implementation touching architecture, workflows, hooks, or
`shifter/shifter_platform`, run:

```bash
python3 scripts/adr_guard/adr_guard.py --all --level ci
```

Also run targeted schema/hydrator, CMS/CTF create-range, provisioner setup-plan,
wire-contract, and provider network tests for the touched surfaces. If IAM,
Terraform, workflow, or Packer files change, run the repo-native linters and
tests for those surfaces rather than relying on this note as validation.
