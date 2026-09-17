# GCP event capacity profiles

Use a versioned shared-service capacity profile before a scheduled GCP event.
The profile is selected once in `shifter.yaml`; the installation renderer then
projects the same identity into Terraform, Helm, the event gate, and drift
inspection. Do not maintain an event-only tfvars or Helm override.

The supported profiles are `gcp-shared-v1-p10`, `gcp-shared-v1-p30`,
`gcp-shared-v1-p50`, and `gcp-shared-v1-p100`. The strict automated
qualification gate currently exists for p30. A larger profile is deployable,
but it is not qualified until it has its own strict gate budget and evidence.

## Preconditions

- The target is a deployed GCP tenant with the real Identity Platform/TOTP,
  range, Guacamole, GKE, Cloud SQL, Memorystore, and external load-balancer
  paths healthy.
- The event range image and example range pass the range functional smoke. This
  keeps the p30 gate compatible with the real GCP range path established by
  issue #1346.
- Thirty distinct participant accounts have a ready RDP target. Do not reuse an
  administrator session or one shared account.
- The operator can read Cloud Monitoring, GKE deployments, and backend-service
  health. The gate makes no provider or cluster mutations.

## Scale up

1. Set the profile in the tenant's installation config:

   ```yaml
   backend: gcp
   settings:
     shared_service_capacity_profile: gcp-shared-v1-p30
   ```

2. Validate and render the generated inputs:

   ```sh
   uv run --project shifter/installation shifter-config validate shifter.yaml
   uv run --project shifter/installation shifter-config render shifter.yaml \
     --output /tmp/shifter-event.auto.tfvars
   uv run --project shifter/installation shifter-config render-capacity shifter.yaml \
     --projection desired-state --output /tmp/shifter-event-capacity.json
   ```

3. Run the normal reviewed GCP deployment workflow with the rendered Terraform
   bridge. Do not edit a node pool, Deployment, HPA, BackendConfig, Cloud SQL
   instance, or Redis instance by hand. The p30 minimums themselves carry the
   event; autoscaling is supplemental headroom.

4. Wait for Terraform, GKE rollouts, HPAs, BackendConfigs, Cloud SQL, and Redis
   to stabilize. Guacamole client replicas must remain exactly one; portal and
   guacd are the horizontally scaled workloads.

5. Prove the effective state matches the selected profile:

   ```sh
   uv run python scripts/gcp/check_event_capacity_drift.py \
     --desired /tmp/shifter-event-capacity.json \
     --project <project> --region <region> --cluster <cluster> \
     --sql-instance <sql-instance> --redis-instance <redis-instance>
   ```

   Exit 0 and `capacity drift: none` are required. Exit 1 is real drift. Exit 2
   means evidence was missing or malformed and is also a stop condition.

## Run the p30 public-path gate

Create a gitignored mode-0600 TOML file with exactly 30 entries. Each entry must
contain the real participant email, password, TOTP seed, and Identity Platform
API key. These values never belong on argv or in the report.

```toml
[[actor]]
email = "participant1@example.invalid"
password = "..."
totp_secret = "..."
api_key = "..."
user_type = "ctf_participant"
```

```sh
chmod 600 actors-p30.toml
cd uat/event-load-harness
uv sync --extra gcp
uv run event-load-harness \
  --target-url https://<tenant-host> --confirm-host <tenant-host> \
  --environment <environment> --profile guacamole-event-gate \
  --capacity-profile-id gcp-shared-v1-p30 \
  --concurrency 30 --ramp-seconds 30 --duration-seconds 120 \
  --actor-source manifest --actor-manifest actors-p30.toml \
  --metric-source gcp --region <region> \
  --gcp-project <project> --gcp-cluster <cluster> \
  --gcp-namespace shifter-platform --gcp-sql-instance <sql-instance> \
  --gcp-redis-instance <redis-instance> --gcp-backend-name <backend-service> \
  --report-path out/p30-envelope.md
```

After the client hold completes, the command waits the catalog-authored 240
seconds for the slowest required Cloud Monitoring series to become visible.
This wait is part of the gate; do not cancel it and substitute dashboard values.

The run passes only when all 30 real logins bootstrap a versioned Guacamole RDP
session, reach display synchronization, and hold continuously for at least 120
seconds. The report must also show p50/p95/p99 bootstrap latency and pass every
authored threshold: zero tunnel drops, relevant pod restarts, Redis evictions
and rejects, Cloud SQL failed connections, load-balancer 5xx/504/no-response
requests, and unhealthy backends; plus bounded guacd CPU, SQL/Redis connection
utilization, and load-balancer p95. Missing telemetry fails the gate.

## Scale down

Scale-down is deliberately separate from event qualification.

1. Preserve the gate report and confirm no active event or long-lived RDP
   sessions depend on the event capacity.
2. Select the normal lower profile in `shifter.yaml` and run the same reviewed
   deployment workflow. Never use `kubectl scale` or provider-console edits.
3. Allow the 300-second load-balancer drain and 330-second pod termination
   window to complete. HPA scale-down stabilization is 900 seconds.
4. Re-render desired state and rerun the drift checker. A clean lower-profile
   drift result is the completion signal. Cloud SQL storage is a non-shrinking
   floor: a disk retained from a larger profile is accepted when it is at least
   the selected profile's minimum; an undersized disk still reports drift.

If scale-up or the gate fails, keep the current profile while investigating.
Rollback means selecting the last known-good profile and deploying it through
the same path; it does not mean deleting resources mid-session.
