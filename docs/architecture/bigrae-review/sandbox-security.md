# Range and agent security engineering

This is the proposed GCP/GCE security profile for [review #2080](README.md),
researched on 2026-09-05. It is a design and qualification plan, not an assertion
that these controls are deployed. ADR-056 records the decision proposed for adoption.

## Trust boundaries and attainable claims

A range participant, scenario package, uploaded file, browser terminal and model
output can all be hostile. A scenario may intentionally give a participant guest
root or Windows administrator. Treat that entire guest as potentially compromised.
Separate a tool escaping its container, a guest crossing into another range or
the management network, and an escape through the provider's VM boundary. Each
has different preventions and observability. No guest sensor proves the absence
of the last event; provider isolation and incident handling remain dependencies.

The control plane, provisioning workers, CI, identity service, secret broker,
telemetry destination and operator credentials are outside participant authority.
One deployment belongs to one customer. Workspaces and events still require
authorization inside it. A public lifecycle API can support other clients while
the deployment remains authoritative for admission, generations and effects.

| Boundary / failure | Prevention required for the declared profile | Observation and qualification |
| --- | --- | --- |
| Participant to management or another range | GCP firewall/routes/IAM scoped to exact range generation; no shared participant management credentials; test IPv4, supported IPv6, TCP/UDP and alternate paths | Outside-boundary policy inventory, selected network logs, bounded probes with positive controls; quarantine drill |
| Guest/container to metadata or cloud API | Explicit metadata policy, no privileged worker identity in guests, narrow effective guest identity; broker model access where possible | Cloud audit and broker decisions outside the guest; test effective permissions and revocation, not only local environment variables |
| Untrusted agent tools to guest host | Dedicated guest trust boundary; least privilege/container constraints where compatible; no management sockets or host mounts | Qualified process/file/network sensor, mount and credential-boundary canaries; identify whether guest root is an intended capability |
| Compromised guest disables sensor | Collector authority outside the range; external heartbeat deadline and drop counters; guest cannot delete/read collected evidence | Stop and overload the sensor; raise degraded-security state externally; do not accept a guest-reported all-clear |
| Agent consumes resources or money | Model/tool/destination allowlists, per-range budgets and concurrency, cgroups/quotas/deadlines, teardown TTL, bounded logs | Broker usage and denied requests, resource saturation, budget and lease alerts; demonstrate abort/revocation |
| Package, image or dependency compromise | Released immutable artifacts, provenance/digests, SBOMs, reviewed acquisition and scenario capability admission | Scan deployed artifacts, altered-artifact tests and reproducible qualification records |
| Control-plane/worker compromise | Separate service identities, narrow provider roles, private management access, patching, secrets outside logs, reviewed privileged jobs | Administrative changes, unusual credential use, deployment audit and independent recovery evidence |
| Lost control-plane or dependency | Durable operations, leases, external resource inventory, documented outage mode | Restart/restore and duplicate-result tests; never claim an unobserved destroy succeeded |

Existing escape validation in `docs/ops/range-escape-validation.md` is a useful
starting point. Its finite probes and UDP limitation must remain explicit.
Success for a test with no working positive control is not containment evidence.
Keep exercises bounded to owned test ranges and synthetic canaries; production
qualification does not require releasing real malware or attempting a provider
hypervisor exploit.

## Recommended first implementation

Use the existing GCE range boundary. Retain the GKE control plane's own workload
hardening; a runtime setting on GKE does not automatically protect a nested
container running inside a GCE scenario VM. Deny boundary crossings by default
and open scenario-required paths through reviewed capability profiles. Permit
intended attacks *inside* that envelope, with sufficient resource limits to
protect shared operations.

Resolve #1295 as a security engineering task with an explicit threat model,
effective policy and tests. A service mesh is one possible authenticated transport
implementation, not a prerequisite for all GCE isolation. Establish whether
workload identity, TLS at the actual service boundary and firewall rules already
address the specific threat before adding a mesh and its operator burden.

Qualify **one** Linux sensor initially: Falco is a reasonable first candidate for
rules and process/syscall events; Tetragon is a reasonable alternative when its
process/file/network events or targeted enforcement fit better. Install the
chosen sensor on the actual supported GCE image/kernel, measure overhead under
the scenario, and test dropped events and silence. Do not run both by default.
[Falco's kernel event collection](https://falco.org/docs/),
[its dropped-event guidance](https://falco.org/docs/concepts/event-sources/kernel/dropped-events/)
and [Tetragon's documentation](https://tetragon.io/docs/) describe mechanisms;
their presence alone does not provide complete escape detection.

Keep cloud audit, selected firewall/network observations and broker audit outside
the guest. Sensor events carry deployment, range ID, execution generation, host
identity, sensor/rule version and observation time. Ingestion authenticates the
source and bounds payload size/rate; attribution from a compromised host remains
untrusted evidence. The writer can append bounded records but cannot query,
overwrite or delete historical evidence. Separate retention administration.
Reuse #1019's journal and existing audit ownership rather than creating another
general-purpose event database.

For Windows, select and qualify an appropriate Windows event collection path
for the cohort; Linux eBPF coverage does not transfer to Windows. Until that is
done, publish the narrower host coverage and use outside-boundary evidence.
Do not advertise uniformly monitored agent workloads across both operating systems.

One candidate is Windows event forwarding with
[Sysmon](https://learn.microsoft.com/en-us/sysinternals/downloads/sysmon) process,
driver and selected network events sent outside the guest. Sysmon collects events;
it does not analyze them or provide complete compromise detection. It is a
separately licensed Microsoft component, not the OSS collector itself. Configure
command-line and payload collection to avoid exporting credentials.

Default rules should identify boundary changes, access to protected canaries,
unexpected metadata/cloud authority, management reachability and monitoring
loss. A shell, compiler, scanner or local privilege escalation can be expected
scenario activity. Avoid paging on those in isolation. Use scenario-aware
allowances bound to the immutable profile; never globally silence a noisy rule
without recording what detection is lost.

## Agent authority and credentials

Model text and retrieved content are untrusted inputs. Prompt instructions,
interactive approval flags, tool descriptions and the operator MCP policy engine
are not execution boundaries. Keep operator MCP identities and infrastructure
tools out of participant contexts. Admit only the scenario's required tools,
filesystem roots and network destinations. Never expose control-plane Docker,
Kubernetes or provisioning credentials as an agent convenience.

Use #681 to bound model access per range/generation, with allowed models,
request/spend/concurrency budgets, deadlines, revocation and audit that excludes
prompt bodies and secrets by default. Prefer a narrow broker holding provider
identity outside the participant boundary. It must authenticate the caller and
enforce its range policy on each request; an unrestricted proxy merely moves the
credential. Resolve #1586's secret-authority design with this work.

Current per-range keys on a shared service account do not establish separate
IAM principals. A key's deletion and the invalidation of previously issued
credentials are different guarantees. Measure revocation time, prevent new
requests immediately at the broker, and document any remaining provider-token
window. Prefer keyless workload identity on trusted services where supported.
[Google's service-account guidance](https://docs.cloud.google.com/iam/docs/best-practices-service-accounts)
supports reducing long-lived keys; the exact participant authentication design
still needs compatibility tests.

## Detection and response contract

Start with a small set of observable outcomes: an unauthorized boundary probe,
protected credential canary use, unexpected identity/policy change, lost sensor,
overloaded collector and overdue cleanup. Give each a severity, evidence query,
deduplication key, owner and response target. Measure detection and containment
latency in the rehearsal; publish those measurements rather than inventing an SLA.

The initial high-confidence response is operator-confirmed quarantine through
the existing lifecycle service: restrict range network access, revoke new model
and remote-access sessions, cancel pending privileged work, preserve external
evidence, and retain a cleanup obligation. Verify effective revocation. Do not
automatically destroy the evidence-bearing workload as the first action.
Low-confidence guest events should not let one hostile range disable unrelated
ranges. Automatic containment can follow after false-positive and failure tests.

Test sensor stop, queue loss, delayed/duplicate observations, stale generation,
credential rotation, inability to apply quarantine, lost provider response and
collector outage. If security evidence required for a new launch is unavailable,
admission fails closed or uses an explicitly narrower supported profile. Existing
ranges follow a documented timeout/containment policy; silent downgrade is not
an acceptable availability feature.

## Optional sandbox runtimes

| Option | Useful scope | Constraints before adoption | Recommendation |
| --- | --- | --- | --- |
| GCE VM plus constrained containers | Today's VM-oriented Linux/Windows ranges; guest-level attacks | Guest root can defeat guest-local controls; monitor externally; containers share a guest kernel | First release baseline, qualified per scenario |
| gVisor | Linux agent/tool workloads needing fewer direct host-kernel interactions | Syscall, networking, debugger and performance compatibility; resource/network controls still needed | Run a small optional compatibility pilot |
| Kata Containers | Kubernetes-shaped Linux workloads that benefit from a VM boundary | Hypervisor/nested virtualization support, guest images, host resources, storage/virtio interfaces and operations | Evaluate only with a demonstrated scenario need |
| Firecracker | Purpose-built Linux microVM service with a deliberately small device model | KVM availability, jailer or equivalent, patched host/microcode, per-VM resources and a lifecycle/image/network operator | Future option, not an incidental container replacement |
| Kubernetes Agent Sandbox controller | Sandbox lifecycle, claims/templates and warm pools | Isolation depends on the selected runtime and policies, not the controller object | Reuse if a Kubernetes agent-runtime product is actually selected; avoid a second lifecycle authority |

[gVisor](https://gvisor.dev/docs/architecture_guide/security/) interposes a
user-space kernel; its [networking model](https://gvisor.dev/docs/architecture_guide/networking/)
and [production guidance](https://gvisor.dev/docs/user_guide/production/) require
workload qualification. [Kata's threat model](https://github.com/kata-containers/documentation/blob/master/design/threat-model/threat-model.md)
describes a VM boundary with trusted host components and remaining shared
interfaces. [Firecracker's production host requirements](https://github.com/firecracker-microvm/firecracker/blob/main/docs/prod-host-setup.md)
make the host and jailer part of the security system.
[Agent Sandbox's quickstart](https://github.com/kubernetes-sigs/agent-sandbox/blob/main/examples/quickstart/README.md)
explicitly distinguishes its default runtime from stronger isolation choices.
These mechanisms cannot be ranked by a universal “secure” label.

Check [GCE nested virtualization support](https://docs.cloud.google.com/compute/docs/instances/nested-virtualization/overview)
against the actual machine family and guest hypervisor before choosing a nested
VM runtime; availability cannot be inferred from an ordinary GCE VM launch.

For any pilot, compare one real scenario and one deliberately unsupported case:
admission, file/tool/network behavior, startup/reset/teardown, privileged operations,
sensor visibility, escape-boundary probes, memory/CPU overhead and operator recovery.
Publish a go/no-go decision and runtime-specific limits. Do not promise an extra
runtime before those results justify its long-term maintenance.

## Modest-budget operating options

The mandatory part is the security outcome and operator response, not a vendor
stack. Start with existing GCP logging/monitoring, a small external journal, one
host sensor, selected network logs and a few useful alerts. Enable only relevant
Data Access logs after identifying what otherwise remains invisible; they are
distinct from default administrative audit.
[Cloud Audit Logs](https://docs.cloud.google.com/logging/docs/audit)
documents that distinction. [VPC Flow Logs](https://docs.cloud.google.com/vpc/docs/flow-logs)
are sampled flow observations, not packet capture or proof that every denied
packet was observed.

| Option | When it earns its cost | Costs and limits to record |
| --- | --- | --- |
| GCP native baseline | Lowest additional operational burden for a GCP-first release | Ingestion, network-log generation, retention, alerting and operator time; tune high-volume sources |
| Falco **or** Tetragon plus existing collector | Host events needed for the selected Linux profile | Sensor CPU/memory, image/kernel maintenance, rule tuning, collector operation; root-compromised host limitations |
| Optional Suricata | Scenario requires traffic-level signatures or protocol evidence | Mirroring/sensor compute, packet volume and storage; encrypted-traffic visibility; keep out of first gate absent demand |
| Optional Wazuh deployment | Operator needs an OSS investigation UI and managed Linux/Windows endpoint collection | Persistent index storage, upgrades, backups, access control, tuning and staffing; keep outside participant networks |
| Security Command Center | Native posture aggregation helps the operator | Standard is free; Premium and Enterprise features have separate pricing; verify each required detector's tier |

[Suricata](https://docs.suricata.io/en/latest/what-is-suricata.html) is an optional
network detection component, not a replacement for isolation. The
[Security Command Center pricing](https://cloud.google.com/security-command-center/pricing)
distinguishes tiers; do not assume advanced threat detectors are included in
the free tier.

If a fuller OSS option is wanted, pilot Wazuh as a separately operated sink and
endpoint-management stack, adding Suricata only for a scenario's traffic-analysis
need. [Wazuh's quickstart](https://documentation.wazuh.com/current/quickstart.html)
recommends 4 vCPU, 8 GiB RAM and 50 GB for 1–25 agents with its stated 90-day alert
storage assumptions. That is a useful starting estimate, not a sizing promise for
attack-heavy ranges. Measure the actual event rate and retain external boundary
evidence even if an endpoint agent is compromised. Do not install Wazuh, Falco,
Tetragon and a packet-inspection stack together as the default small deployment.

At review time [Cloud Observability pricing](https://cloud.google.com/products/observability/pricing)
lists general log storage at $0.50/GiB after 50 GiB/project/month, with 30-day
retention included. Listed vended network logs are $0.25/GiB without that free
allocation; network log generation can add separate charges. Retention beyond
30 days is $0.01/GiB-month. Required audit storage has different treatment.
For illustration only, 100 GiB general logs plus 100 GiB vended network logs is
$50 of these ingestion charges before generation, additional retention, compute,
metrics, traffic and tax. Prices are not a deployment quote.

Measure GiB per scenario-hour, sensor overhead, false positives, and minutes of
operator work in qualification. Choose short bounded high-volume retention,
longer compact security/audit evidence, export quotas and explicit monthly cost
alerts. Budget alerts are notifications, not spend caps. A safe loss policy must
preserve high-priority security events and report telemetry degradation when
volume limits are reached. An optional OSS stack is free of license fees, not
free to operate. Offer one documented default and interchangeable sinks only
where the existing evidence contract supports them.
