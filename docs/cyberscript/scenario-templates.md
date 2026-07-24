# Scenario Templates

Complete schema reference for CyberScript scenario YAML templates. Validated by `ScenarioTemplate` in `cms/scenarios/schema.py`.

## Top-Level Fields

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| **`id`** | string | yes | -- | Unique scenario identifier. Must match the filename stem. |
| **`name`** | string | yes | -- | Human-readable display name shown in the UI. |
| **`description`** | string | yes | -- | User-facing description of the scenario. |
| **`enabled`** | bool | no | `true` | Whether the scenario is visible in the scenario catalog. |
| **`ngfw`** | bool | no | `false` | Whether the scenario requires NGFW provisioning. |
| **`caldera`** | bool | no | `false` | Whether provisioning starts Caldera on the Kali attacker and deploys sandcat to non-attacker VMs. |
| **`instances`** | list | yes | -- | Instance configurations. Must contain at least one entry. |
| **`subnets`** | list | no | `[]` | Subnet configurations. If empty, the hydrator creates a single `default` subnet at runtime. |

## Validation Rules

- `instances` must be non-empty. An empty list raises a validation error.
- Every instance name referenced in a `subnets[].instances` list must match an entry in `instances[].name`. Unknown references raise a validation error.
- `id` must be unique across all YAML templates and DB scenarios.

## Minimal Valid Template

```yaml
id: minimal
name: Minimal Range
description: Simplest possible scenario.
instances:
  - name: Attacker
    role: attacker
    os_type: kali
```

This produces a single Kali instance in a `default` subnet with no NGFW.

## Field Details

### `id`

The canonical identifier for the scenario. Used in:

- Filename: `cms/scenarios/templates/{id}.yaml`
- Hydration: `hydrate_scenario(scenario_id="{id}", ...)`
- UI: scenario selection, URL routing
- Metadata: `ScenarioMetadata.scenario_id`

Convention: lowercase, underscores for word separation (for example `ad_attack_lab`, `ad_attack_lab_ngfw`).

### `ngfw`

When `true`, the Engine provisions a Palo Alto Networks NGFW appliance alongside the range instances. Subnet `connected_to` declarations become firewall rules routed through the NGFW. See [Networking](networking.md).

### `caldera`

When `true`, provisioning starts the baked Caldera server on the single Kali
attacker instance and deploys sandcat agents to VM instances with role `victim`
or `dc`.

Defaults are intentionally conservative:

- Caldera is disabled unless the scenario opts in.
- The attacker callback URL uses the attacker's private IP and port `8888`.
- Linux sandcat is left at `/tmp/sandcat.go-linux`.
- Windows sandcat is left at `C:\Users\Public\sandcat.exe`.
- Windows Defender handling uses a path/process exclusion for the sandcat path.

The range topology remains authoritative. Enabling Caldera does not add public
ingress, expose the Caldera UI/API through Mission Control or Guacamole, or
install Caldera at runtime; the Kali image bake owns `/opt/caldera` and
`/usr/local/bin/start-caldera`.

### `instances`

List of `InstanceConfig` objects. See [Instances](instances.md) for the full field reference.

### `subnets`

List of `SubnetConfig` objects. See [Networking](networking.md) for the full field reference.

## Schema Source

```
cms/scenarios/schema.py :: ScenarioTemplate
```
