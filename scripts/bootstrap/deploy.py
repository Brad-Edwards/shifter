#!/usr/bin/env python3
"""Shifter deployment CLI.

Guides you through deploying Shifter infrastructure from a bare AWS account.

Features:
- Interactive prompts with automated options (via gh CLI, git)
- Confirmation before any changes (yes/no/manual)
- Dry-run mode to preview without making changes
- Manual fallback for all steps

Usage:
    ./scripts/bootstrap/deploy.py bootstrap --env prod --profile my-prod-profile
    ./scripts/bootstrap/deploy.py bootstrap --env prod --profile my-prod-profile --dry-run
    ./scripts/bootstrap/deploy.py terraform --env prod --profile my-prod-profile
    ./scripts/bootstrap/deploy.py terraform --env prod --profile my-prod-profile --dry-run
    ./scripts/bootstrap/deploy.py full --env prod --profile my-prod-profile
"""

import argparse
import getpass
import importlib.util
import ipaddress
import json
import os
import re
import shutil
import subprocess  # nosec B404
import sys
import tempfile
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from textwrap import dedent
from typing import Any
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request

# Import runner setup module
try:
    from runner import get_runner_config, walkthrough_runner_setup

    RUNNER_AVAILABLE = True
except ImportError:
    RUNNER_AVAILABLE = False

import terraform_backend as tb

# SonarCloud S1192: extracted duplicated string literals.
HELP_AWS_PROFILE = "AWS CLI profile name"
HELP_DRY_RUN = "Show what would be done"


# Colors for terminal output
class Colors:
    HEADER = "\033[95m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    END = "\033[0m"


def info(msg: str) -> None:
    print(f"{Colors.CYAN}ℹ {msg}{Colors.END}")


def success(msg: str) -> None:
    print(f"{Colors.GREEN}✓ {msg}{Colors.END}")


def warn(msg: str) -> None:
    print(f"{Colors.YELLOW}⚠ {msg}{Colors.END}")


def error(msg: str) -> None:
    print(f"{Colors.RED}✗ {msg}{Colors.END}")


def header(msg: str) -> None:
    print(f"\n{Colors.BOLD}{Colors.HEADER}{'=' * 60}{Colors.END}")
    print(f"{Colors.BOLD}{Colors.HEADER}{msg}{Colors.END}")
    print(f"{Colors.BOLD}{Colors.HEADER}{'=' * 60}{Colors.END}\n")


def subheader(msg: str) -> None:
    print(f"\n{Colors.BOLD}{Colors.CYAN}--- {msg} ---{Colors.END}\n")


def code_block(text: str) -> None:
    """Print a code block with dimmed formatting."""
    print(f"{Colors.DIM}┌{'─' * 58}┐{Colors.END}")
    for line in text.strip().split("\n"):
        print(f"{Colors.DIM}│{Colors.END} {line}")
    print(f"{Colors.DIM}└{'─' * 58}┘{Colors.END}")


def confirm(msg: str, default_yes: bool = False) -> bool:
    """Prompt for yes/no confirmation. Returns default_yes if not interactive."""
    # Check if we're in a non-interactive environment
    if not sys.stdin.isatty():
        return default_yes

    while True:
        response = input(f"{Colors.YELLOW}{msg} [y/N]: {Colors.END}").strip().lower()
        if response in ("y", "yes"):
            return True
        if response in ("n", "no", ""):
            return False
        print("Please enter 'y' or 'n'")


def confirm_or_manual(msg: str) -> str:
    """Prompt for yes/no/manual. Returns 'yes', 'no', or 'manual'.

    Note: 'no' will cause the script to abort with an error explanation,
    as all steps are required for a functioning deployment.
    """
    # Check if we're in a non-interactive environment
    if not sys.stdin.isatty():
        return "manual"

    while True:
        response = input(f"{Colors.YELLOW}{msg} [y/n/m]: {Colors.END}").strip().lower()
        if response in ("y", "yes"):
            return "yes"
        if response in ("n", "no"):
            return "no"
        if response in ("m", "manual"):
            return "manual"
        print("Please enter 'y' (yes), 'n' (no - will abort), or 'm' (manual)")


def wait_for_user(msg: str) -> None:
    """Wait for user to confirm they've completed a manual step."""
    # Skip in non-interactive mode
    if not sys.stdin.isatty():
        print(f"\n{Colors.BOLD}{Colors.YELLOW}ACTION REQUIRED:{Colors.END}")
        print(f"{msg}\n")
        print(f"{Colors.DIM}[Non-interactive mode - skipping prompt]{Colors.END}")
        return

    print(f"\n{Colors.BOLD}{Colors.YELLOW}ACTION REQUIRED:{Colors.END}")
    print(f"{msg}\n")
    while True:
        response = input(f"{Colors.GREEN}Press Enter when done (or 'skip' to skip): {Colors.END}").strip().lower()
        if response == "":
            return
        if response == "skip":
            warn("Step skipped - you'll need to complete this manually later")
            return
        print("Press Enter to continue, or type 'skip' to skip this step")


def prompt_required_value(prompt: str, *, secret: bool = False) -> str:
    """Prompt until a non-empty value is provided."""
    if not sys.stdin.isatty():
        raise RuntimeError(f"{prompt} must be provided via environment for non-interactive bootstrap")

    while True:
        value = (
            getpass.getpass(f"{Colors.CYAN}{prompt}: {Colors.END}")
            if secret
            else input(f"{Colors.CYAN}{prompt}: {Colors.END}")
        )
        value = value.strip()
        if value:
            return value
        print("Value is required")


def _format_sample_env_assignment(key: str, value: str = "") -> str:
    """Build sample env entries without embedding credential literals in source."""
    return f"{key}={value}"


def _sample_guest_access_defaults() -> list[str]:
    """Return placeholder guest credential env entries.

    Issue #762: GDC_KALI_PASSWORD / GDC_UBUNTU_PASSWORD /
    GDC_WINDOWS_ADMIN_PASSWORD were dropped. Guest passwords are now
    per-instance GCP Secret Manager secrets created at provisioning
    time. The DC role keeps its deployment-scoped DC_DOMAIN_PASSWORD
    contract (set elsewhere in the deploy pipeline).
    """
    return []


def _validate_argv(cmd: list[str]) -> None:
    """Validate an argv list before handing it to subprocess.

    Commands here are always argv lists (never shell strings), so shell-metacharacter
    injection is not possible. This guards the remaining risk from external/CLI-derived
    tokens: every element must be a string, and no element may contain a NUL byte
    (which `execve` rejects and which can truncate arguments in some C wrappers).
    """
    for index, arg in enumerate(cmd):
        if not isinstance(arg, str):
            raise ValueError(f"argv[{index}] must be a string, got {type(arg).__name__}")
        if "\x00" in arg:
            raise ValueError(f"argv[{index}] contains a NUL byte")


_SENSITIVE_FLAG_HINTS = ("password", "secret", "token", "credential", "private-key", "passwd")


def _looks_like_inline_secret(token: str) -> bool:
    """Heuristically detect an argv token that may carry an inline credential."""
    # Inline JSON/structured documents (e.g. IAM policy bodies, bootstrap XML)
    # can embed credentials; long opaque tokens are likely keys/secrets.
    if token[:1] in "{[":
        return True
    return len(token) >= 40 and not token.startswith("-") and not any(c in token for c in " /:")


def _redact_argv_for_log(cmd: list[str]) -> str:
    """Render an argv list for logging with secret-bearing tokens masked.

    Masks the value following any flag whose name signals a secret, plus any
    token that looks like an inline credential, so the deploy log never carries
    a password, secret, or key in clear text (py/clear-text-logging).
    """
    redacted: list[str] = []
    mask_next = False
    for token in cmd:
        if mask_next:
            redacted.append("***")
            mask_next = False
            continue
        lowered = token.lower()
        if token.startswith("-") and any(hint in lowered for hint in _SENSITIVE_FLAG_HINTS):
            redacted.append(token)
            mask_next = True
        elif _looks_like_inline_secret(token):
            redacted.append("***")
        else:
            redacted.append(token)
    return " ".join(redacted)


def run_cmd(
    cmd: list[str],
    dry_run: bool = False,
    check: bool = True,
    capture: bool = False,
    profile: str = None,
) -> subprocess.CompletedProcess | None:
    """Run a command, optionally in dry-run mode."""
    # Insert --profile flag for AWS CLI commands
    if profile and cmd[0] == "aws":
        cmd = cmd[:1] + ["--profile", profile] + cmd[1:]

    _validate_argv(cmd)
    cmd_str = _redact_argv_for_log(cmd)
    if dry_run:
        print(f"{Colors.BLUE}[DRY-RUN] Would run: {cmd_str}{Colors.END}")
        return None

    info(f"Running: {cmd_str}")
    try:
        if capture:
            result = subprocess.run(cmd, check=check, capture_output=True, text=True)  # nosec B603 B607
        else:
            result = subprocess.run(cmd, check=check, text=True)  # nosec B603 B607
        return result
    except subprocess.CalledProcessError as e:
        error(f"Command failed: {e}")
        if hasattr(e, "stderr") and e.stderr:
            print(e.stderr)
        if check:
            sys.exit(1)
        return None


def get_aws_account_id(profile: str = None) -> str:
    """Get current AWS account ID."""
    cmd = ["aws", "sts", "get-caller-identity", "--query", "Account", "--output", "text"]
    if profile:
        cmd = ["aws", "--profile", profile, "sts", "get-caller-identity", "--query", "Account", "--output", "text"]
    _validate_argv(cmd)
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)  # nosec B603 B607
    return result.stdout.strip()


def get_repo_root() -> Path:
    """Get the repository root directory."""
    return Path(__file__).parent.parent.parent


GDC_API_SERVICES = [
    "anthos.googleapis.com",
    "anthosaudit.googleapis.com",
    "anthosgke.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "compute.googleapis.com",
    "connectgateway.googleapis.com",
    "container.googleapis.com",
    "iap.googleapis.com",
    "gkeconnect.googleapis.com",
    "gkehub.googleapis.com",
    "gkeonprem.googleapis.com",
    "iam.googleapis.com",
    "kubernetesmetadata.googleapis.com",
    "logging.googleapis.com",
    "monitoring.googleapis.com",
    "opsconfigmonitoring.googleapis.com",
    "secretmanager.googleapis.com",
    "serviceusage.googleapis.com",
    "stackdriver.googleapis.com",
    "storage.googleapis.com",
]

GDC_SERVICE_ACCOUNT_ROLES = [
    "roles/compute.viewer",
    "roles/gkehub.connect",
    "roles/gkehub.admin",
    "roles/logging.logWriter",
    "roles/monitoring.metricWriter",
    "roles/monitoring.dashboardEditor",
    "roles/monitoring.viewer",
    "roles/opsconfigmonitoring.resourceMetadata.writer",
    "roles/serviceusage.serviceUsageViewer",
    "roles/stackdriver.resourceMetadata.writer",
    "roles/kubernetesmetadata.publisher",
]

GCP_TERRAFORM_BOOTSTRAP_ROLES = [
    "roles/owner",
]

GCP_TERRAFORM_BOOTSTRAP_BUCKET_ROLE = "roles/storage.objectAdmin"
GCP_IAP_TCP_SOURCE_RANGE = "35.235.240.0/20"


def s3_bucket_exists(bucket_name: str, profile: str) -> bool:
    """Check if an S3 bucket exists."""
    cmd = ["aws", "--profile", profile, "s3api", "head-bucket", "--bucket", bucket_name]
    _validate_argv(cmd)
    result = subprocess.run(  # nosec B603 B607
        cmd,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def dynamodb_table_exists(table_name: str, region: str, profile: str) -> bool:
    """Check if a DynamoDB table exists."""
    result = subprocess.run(  # nosec B603 B607
        [
            "aws",
            "--profile",
            profile,
            "dynamodb",
            "describe-table",
            "--table-name",
            table_name,
            "--region",
            region,
        ],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def github_secret_exists(secret_name: str, github_org: str, github_repo: str) -> bool:
    """Check if a GitHub secret exists."""
    result = subprocess.run(  # nosec B603 B607
        ["gh", "secret", "list", "--repo", f"{github_org}/{github_repo}"],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0 and result.stdout is not None and secret_name in result.stdout


def create_s3_bucket(bucket_name: str, region: str, profile: str, dry_run: bool) -> None:
    """Create and configure an S3 bucket for Terraform state."""
    run_cmd(
        [
            "aws",
            "s3api",
            "create-bucket",
            "--bucket",
            bucket_name,
            "--region",
            region,
            "--create-bucket-configuration",
            f"LocationConstraint={region}",
        ],
        dry_run=dry_run,
        profile=profile,
    )

    run_cmd(
        [
            "aws",
            "s3api",
            "put-bucket-versioning",
            "--bucket",
            bucket_name,
            "--versioning-configuration",
            "Status=Enabled",
        ],
        dry_run=dry_run,
        profile=profile,
    )

    run_cmd(
        [
            "aws",
            "s3api",
            "put-bucket-encryption",
            "--bucket",
            bucket_name,
            "--server-side-encryption-configuration",
            '{"Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]}',
        ],
        dry_run=dry_run,
        profile=profile,
    )

    run_cmd(
        [
            "aws",
            "s3api",
            "put-public-access-block",
            "--bucket",
            bucket_name,
            "--public-access-block-configuration",
            (
                '{"BlockPublicAcls": true, "IgnorePublicAcls": true, '
                '"BlockPublicPolicy": true, "RestrictPublicBuckets": true}'
            ),
        ],
        dry_run=dry_run,
        profile=profile,
    )


def create_dynamodb_table(table_name: str, region: str, profile: str, dry_run: bool) -> None:
    """Create a DynamoDB table for Terraform state locking."""
    run_cmd(
        [
            "aws",
            "dynamodb",
            "create-table",
            "--table-name",
            table_name,
            "--attribute-definitions",
            "AttributeName=LockID,AttributeType=S",
            "--key-schema",
            "AttributeName=LockID,KeyType=HASH",
            "--billing-mode",
            "PAY_PER_REQUEST",
            "--region",
            region,
        ],
        dry_run=dry_run,
        profile=profile,
    )

    if not dry_run:
        info("Waiting for table to be active...")
        run_cmd(
            ["aws", "dynamodb", "wait", "table-exists", "--table-name", table_name, "--region", region],
            profile=profile,
        )


def administrator_access_policy_document() -> str:
    """Return the inline administrator policy used for bootstrap and CI roles.

    Some AWS organizations deny iam:AttachRolePolicy via SCP while still
    allowing inline role policies. The effective policy matches AWS managed
    AdministratorAccess without depending on managed-policy attachment APIs.
    """
    return json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": "*",
                    "Resource": "*",
                }
            ],
        }
    )


AWS_ENVIRONMENTS = ("dev", "proof", "prod")


@dataclass
class BootstrapConfig:
    env: str
    region: str = "us-east-2"  # TODO: Make configurable via CLI argument if multi-region support needed
    github_org: str = "Brad-Edwards"  # USER-SPECIFIC: Change to your GitHub organization
    github_repo: str = "shifter"  # USER-SPECIFIC: Change to your repository name

    @property
    def bucket_prefix(self) -> str:
        return "shifter-infra" if self.env == "prod" else f"shifter-{self.env}-infra"

    @property
    def table_prefix(self) -> str:
        return "shifter-terraform" if self.env == "prod" else f"shifter-{self.env}-terraform"

    @property
    def bootstrap_role_name(self) -> str:
        """Temporary bootstrap role - deleted after terraform creates the real one."""
        return f"github-actions-shifter-{self.env}-bootstrap"

    @property
    def role_name(self) -> str:
        """Production role managed by Terraform - never touched by this script."""
        return f"github-actions-shifter-{self.env}"

    @property
    def secret_name(self) -> str:
        if self.env == "prod":
            return "AWS_ROLE_ARN"
        return f"AWS_ROLE_ARN_{self.env.upper().replace('-', '_')}"


@dataclass(frozen=True)
class GDCHost:
    """Single host in the GDC-on-Compute-Engine evaluation topology."""

    name: str
    role: str
    primary_ip: str
    vxlan_ip: str


@dataclass
class GDCBootstrapConfig:
    """Configuration for a repeatable GDC VM Runtime bootstrap."""

    project_id: str
    cluster_id: str = "cluster1"
    region: str = "us-central1"
    zone: str = "us-central1-a"
    bmctl_version: str = "1.34.200-gke.68"
    environment: str = "gcp-dev"
    network_name: str | None = None
    subnetwork_name: str | None = None
    subnet_cidr: str = "10.240.0.0/20"
    vxlan_cidr: str = "10.200.0.0/24"
    pod_cidr: str = "192.168.0.0/16"
    service_cidr: str = "172.26.232.0/24"
    control_plane_vip: str = "10.200.0.49"
    ingress_vip: str = "10.200.0.50"
    address_pool: str = "10.200.0.50-10.200.0.70"
    # Platform-reachable control-plane endpoint for the GKE-hosted provisioner
    # (D23). The bmctl kubeconfig points at control_plane_vip, which lives on the
    # cluster's VXLAN overlay and is unreachable from the platform VPC. Terraform
    # fronts the control-plane nodes with an internal TCP load balancer on the
    # peered range VPC and passes its address here so the stored kubeconfig is
    # rewritten to a reachable endpoint. "host" or "host:port" (default port
    # 6444, the kube-apiserver bundled-LB backend port).
    control_plane_platform_endpoint: str | None = None
    machine_type: str = "n1-standard-8"
    boot_disk_size_gb: int = 200
    boot_disk_type: str = "pd-ssd"
    service_account_name: str = "baremetal-gcr"
    google_account_email: str | None = None
    # Root installation config (shifter.yaml) that feeds the range egress render
    # (#1015). None falls back to SHIFTER_CONFIG / repo-root shifter.yaml.
    shifter_config_path: str | None = None

    @property
    def resolved_network_name(self) -> str:
        return self.network_name or f"{self.cluster_id}-gdc"

    @property
    def resolved_subnetwork_name(self) -> str:
        return self.subnetwork_name or f"{self.resolved_network_name}-{self.region}"

    @property
    def service_account_email(self) -> str:
        return f"{self.service_account_name}@{self.project_id}.iam.gserviceaccount.com"

    @property
    def terraform_bootstrap_service_account_name(self) -> str:
        return f"shifter-{self.environment}-tf-bootstrap"

    @property
    def terraform_bootstrap_service_account_email(self) -> str:
        return f"{self.terraform_bootstrap_service_account_name}@{self.project_id}.iam.gserviceaccount.com"

    @property
    def terraform_state_bucket_name(self) -> str:
        return f"{self.project_id}-terraform-state"

    @property
    def cluster_namespace(self) -> str:
        return f"{self.cluster_id}-ns"

    @property
    def cluster_workspace_dir(self) -> str:
        return f"/root/bmctl-workspace/{self.cluster_id}"

    @property
    def kubeconfig_path(self) -> str:
        return f"{self.cluster_workspace_dir}/{self.cluster_id}-kubeconfig"

    @property
    def staging_dir(self) -> str:
        return "/root/shifter-gdc-bootstrap"

    @property
    def staging_bundle_dir(self) -> str:
        return f"{self.staging_dir}/{self.cluster_id}"

    @property
    def bmctl_gcs_source(self) -> str:
        return f"gs://anthos-baremetal-release/bmctl/{self.bmctl_version}/linux-amd64/bmctl"

    @property
    def instance_tag(self) -> str:
        return f"{self.cluster_id}-gdc"

    @property
    def ssh_firewall_rule_name(self) -> str:
        return f"{self.cluster_id}-allow-ssh-rule"

    @property
    def internal_firewall_rule_name(self) -> str:
        return f"{self.cluster_id}-allow-internal-rule"

    @property
    def lb_firewall_rule_name(self) -> str:
        return f"{self.cluster_id}-allow-lb-traffic-rule"

    @property
    def cloud_router_name(self) -> str:
        return f"{self.cluster_id}-nat-router"

    @property
    def cloud_nat_name(self) -> str:
        return f"{self.cluster_id}-nat"

    @property
    def cluster_context(self) -> str:
        return f"{self.cluster_id}-admin@{self.cluster_id}"

    @property
    def gdc_access_secret_id(self) -> str:
        return f"shifter-{self.environment}-gdc-access"

    @property
    def gdc_vm_image_gcs_secret_id(self) -> str:
        return f"shifter-{self.environment}-gdc-vm-image-gcs"

    @property
    def gdc_vm_image_bucket(self) -> str:
        """GCS bucket the packer-gcp image pipeline exports guest disks into.

        Matches platform/terraform/gcp/modules/cicd-github-oidc (bucket
        ``${name_prefix}-gdc-vm-images`` with ``name_prefix = shifter-${env}``),
        so the VM Runtime boot images resolve to
        ``gs://<bucket>/<guest>.qcow2`` without baking a per-env literal here.
        """
        return f"shifter-{self.environment}-gdc-vm-images"

    def gdc_vm_image_url(self, guest: str) -> str:
        """gs:// URL of the exported VM Runtime boot disk for a guest class."""
        return f"gs://{self.gdc_vm_image_bucket}/{guest}.qcow2"

    @property
    def workstation(self) -> GDCHost:
        return GDCHost(
            name=f"{self.cluster_id}-abm-ws0-001",
            role="workstation",
            primary_ip="10.240.0.2",
            vxlan_ip="10.200.0.2",
        )

    @property
    def control_plane_hosts(self) -> list[GDCHost]:
        return [
            GDCHost(f"{self.cluster_id}-abm-cp1-001", "control-plane", "10.240.0.3", "10.200.0.3"),
            GDCHost(f"{self.cluster_id}-abm-cp2-001", "control-plane", "10.240.0.4", "10.200.0.4"),
            GDCHost(f"{self.cluster_id}-abm-cp3-001", "control-plane", "10.240.0.5", "10.200.0.5"),
        ]

    @property
    def worker_hosts(self) -> list[GDCHost]:
        return [
            GDCHost(f"{self.cluster_id}-abm-w1-001", "worker", "10.240.0.6", "10.200.0.6"),
            GDCHost(f"{self.cluster_id}-abm-w2-001", "worker", "10.240.0.7", "10.200.0.7"),
        ]

    @property
    def all_hosts(self) -> list[GDCHost]:
        return [self.workstation, *self.control_plane_hosts, *self.worker_hosts]

    @property
    def cluster_node_hosts(self) -> list[GDCHost]:
        return [*self.control_plane_hosts, *self.worker_hosts]


def parse_env_file(path: Path) -> dict[str, str]:
    """Parse a simple KEY=VALUE env file without extra dependencies."""
    values: dict[str, str] = {}
    if not path.exists():
        return values

    for line in path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip("'").strip('"')
    return values


def read_gcp_control_plane_security_inputs(tf_dir: Path) -> dict[str, object]:
    """Read the security-sensitive Terraform inputs, honoring ``*.auto.tfvars`` overrides.

    Operators set deployment-specific values (project_id, public_hostname,
    gke_master_authorized_cidrs, ...) in a gitignored ``local.auto.tfvars`` per
    the committed ``terraform.tfvars`` header, and Terraform auto-loads
    ``*.auto.tfvars`` with the local values winning. The validator must read the
    same effective values, not just ``terraform.tfvars`` — otherwise a correctly
    configured operator hits a false "requires gke_master_authorized_cidrs"
    failure. Terraform's load order is ``terraform.tfvars`` then ``*.auto.tfvars``
    alphabetically, with later assignments winning, so we scan in that order and
    take the last value of each setting.
    """
    tfvars_files = [tf_dir / "terraform.tfvars", *sorted(tf_dir.glob("*.auto.tfvars"))]
    contents = "\n".join(path.read_text() for path in tfvars_files if path.exists())

    public_hostname_matches = re.findall(r'(?m)^\s*public_hostname\s*=\s*"([^"]*)"\s*$', contents)
    managed_tls_matches = re.findall(r"(?m)^\s*enable_managed_tls\s*=\s*(true|false)\s*$", contents)
    cidr_block_matches = re.findall(r"gke_master_authorized_cidrs\s*=\s*\[(.*?)\]", contents, re.DOTALL)

    return {
        "public_hostname": public_hostname_matches[-1].strip() if public_hostname_matches else "",
        "enable_managed_tls": bool(managed_tls_matches and managed_tls_matches[-1] == "true"),
        "gke_master_authorized_cidrs": (
            [match.strip() for match in re.findall(r'"([^"]+)"', cidr_block_matches[-1])] if cidr_block_matches else []
        ),
    }


def validate_gcp_control_plane_security_inputs(tf_dir: Path) -> None:
    """Fail fast when the GCP control plane would be bootstrapped with an insecure public posture."""
    settings = read_gcp_control_plane_security_inputs(tf_dir)

    if not settings["public_hostname"]:
        raise ValueError(
            "GCP bootstrap requires a public hostname before applying the control plane. "
            "Set public_hostname in terraform.tfvars."
        )
    if not settings["enable_managed_tls"]:
        raise ValueError(
            "GCP bootstrap requires managed TLS for the public ingress. "
            "Set enable_managed_tls = true in terraform.tfvars."
        )
    authorized_cidrs = settings["gke_master_authorized_cidrs"]
    if not authorized_cidrs:
        raise ValueError(
            "GCP bootstrap requires gke_master_authorized_cidrs so the public GKE control-plane endpoint "
            "is restricted to admin networks."
        )
    # Same contract the Terraform variable validation enforces (see
    # platform/terraform/gcp/modules/platform-core/variables.tf::gke_master_authorized_cidrs):
    #   1. an explicit "/N" suffix is present (rejects bare IPs).
    #   2. the entry parses as a CIDR (rejects garbage / bad octets / bad prefixes).
    #   3. the parsed prefix length is > 0 (rejects /0 from the parsed prefix
    #      number, not from a string-suffix check).
    for cidr in authorized_cidrs:
        if "/" not in cidr:
            raise ValueError(
                f"GCP bootstrap rejected gke_master_authorized_cidrs entry {cidr!r}: must include an "
                "explicit /N prefix (e.g. 203.0.113.10/32)."
            )
        try:
            network = ipaddress.ip_network(cidr, strict=False)
        except ValueError as exc:
            raise ValueError(
                f"GCP bootstrap rejected gke_master_authorized_cidrs entry {cidr!r}: not a valid CIDR ({exc})."
            ) from exc
        if network.prefixlen == 0:
            raise ValueError(
                f"GCP bootstrap rejected gke_master_authorized_cidrs entry {cidr!r}: a /0 range opens the "
                "public GKE control-plane endpoint to the entire internet. List specific admin networks instead."
            )


def get_default_gdc_project_id() -> str:
    """Resolve the default GDC/GCP project from env vars or the repo-root .env."""
    for key in ("GCP_PROJECT_ID", "GOOGLE_CLOUD_PROJECT", "GCLOUD_PROJECT", "PANW_GCP_DEV"):
        value = os.environ.get(key, "").strip()
        if value:
            return value

    repo_env = parse_env_file(get_repo_root() / ".env")
    for key in ("GCP_PROJECT_ID", "GOOGLE_CLOUD_PROJECT", "GCLOUD_PROJECT", "PANW_GCP_DEV"):
        value = repo_env.get(key, "").strip()
        if value:
            return value
    return ""


def gcloud_resource_exists(cmd: list[str]) -> bool:
    """Return True when the gcloud describe/list command exits successfully."""
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)  # nosec B603 B607
    return result.returncode == 0


_UNKNOWN_ERROR = "unknown error"
_GKE_WORKLOAD_IDENTITY_ANNOTATION = "iam.gke.io/gcp-service-account"
_GDC_SCENARIO_POD_KALI_IMAGE = (
    "docker.io/kalilinux/kali-rolling@sha256:256893c92bbd289b07d9ef8a62e75f9c7cb3d9e570fb3d3725b2e86b9acd5728"
)
_YAML_METADATA = "metadata:"


def render_gdc_cluster_config(config: GDCBootstrapConfig) -> str:
    """Render the hybrid-cluster config used by bmctl on the workstation."""
    lines = [
        "---",
        "gcrKeyPath: /root/bm-gcr.json",
        "sshPrivateKeyPath: /root/.ssh/id_rsa",
        "gkeConnectAgentServiceAccountKeyPath: /root/bm-gcr.json",
        "gkeConnectRegisterServiceAccountKeyPath: /root/bm-gcr.json",
        "cloudOperationsServiceAccountKeyPath: /root/bm-gcr.json",
        "---",
        "apiVersion: v1",
        "kind: Namespace",
        _YAML_METADATA,
        f"  name: {config.cluster_namespace}",
        "---",
        "apiVersion: baremetal.cluster.gke.io/v1",
        "kind: Cluster",
        _YAML_METADATA,
        f"  name: {config.cluster_id}",
        f"  namespace: {config.cluster_namespace}",
        "spec:",
        "  type: hybrid",
        f"  anthosBareMetalVersion: {config.bmctl_version}",
        "  gkeConnect:",
        f"    projectID: {config.project_id}",
        "  controlPlane:",
        "    nodePoolSpec:",
        f"      clusterName: {config.cluster_id}",
        "      nodes:",
    ]
    lines.extend(f"      - address: {host.vxlan_ip}" for host in config.control_plane_hosts)
    lines.extend(
        [
            "  clusterNetwork:",
            "    multipleNetworkInterfaces: true",
            "    pods:",
            "      cidrBlocks:",
            f"      - {config.pod_cidr}",
            "    services:",
            "      cidrBlocks:",
            f"      - {config.service_cidr}",
            "  loadBalancer:",
            "    mode: bundled",
            "    ports:",
            "      controlPlaneLBPort: 443",
            "    vips:",
            f"      controlPlaneVIP: {config.control_plane_vip}",
            f"      ingressVIP: {config.ingress_vip}",
            "    addressPools:",
            "    - name: ingress-pool",
            "      addresses:",
            f"      - {config.address_pool}",
            "  clusterOperations:",
            f"    location: {config.region}",
            f"    projectID: {config.project_id}",
        ]
    )
    if config.google_account_email:
        lines.extend(
            [
                "  clusterSecurity:",
                "    authorization:",
                "      clusterAdmin:",
                "        gcpAccounts:",
                f"        - {config.google_account_email}",
            ]
        )
    lines.extend(
        [
            "  storage:",
            "    lvpNodeMounts:",
            "      path: /mnt/localpv-disk",
            "      storageClassName: node-disk",
            "    lvpShare:",
            "      numPVUnderSharedPath: 5",
            "      path: /mnt/localpv-share",
            "      storageClassName: local-shared",
            "  nodeConfig:",
            "    podDensity:",
            "      maxPodsPerNode: 250",
            "---",
            "apiVersion: baremetal.cluster.gke.io/v1",
            "kind: NodePool",
            _YAML_METADATA,
            "  name: node-pool-1",
            f"  namespace: {config.cluster_namespace}",
            "spec:",
            f"  clusterName: {config.cluster_id}",
            "  nodes:",
        ]
    )
    lines.extend(f"  - address: {host.vxlan_ip}" for host in config.worker_hosts)
    return "\n".join(lines) + "\n"


def render_gdc_prepare_workstation_script(config: GDCBootstrapConfig) -> str:
    """Render the workstation prep script."""
    return dedent(
        f"""\
        #!/usr/bin/env bash
        set -euo pipefail

        export DEBIAN_FRONTEND=noninteractive

        apt-get -qq update
        apt-get -qq install -y ca-certificates curl jq

        if ! command -v docker >/dev/null 2>&1; then
          curl -fsSL https://get.docker.com -o /tmp/get-docker.sh
          sh /tmp/get-docker.sh
        fi
        systemctl enable --now docker

        if ! command -v kubectl >/dev/null 2>&1; then
          KUBECTL_VERSION="$(curl -fsSL https://storage.googleapis.com/kubernetes-release/release/stable.txt)"
          curl -fsSLo /usr/local/sbin/kubectl \
            "https://storage.googleapis.com/kubernetes-release/release/${{KUBECTL_VERSION}}/bin/linux/amd64/kubectl"
          chmod +x /usr/local/sbin/kubectl
        fi

        install -d -m 700 /root/.ssh {config.staging_dir} {config.cluster_workspace_dir}
        install -m 600 {config.staging_bundle_dir}/id_rsa /root/.ssh/id_rsa
        install -m 644 {config.staging_bundle_dir}/id_rsa.pub /root/.ssh/id_rsa.pub
        install -m 600 {config.staging_bundle_dir}/bm-gcr.json /root/bm-gcr.json
        install -m 755 {config.staging_bundle_dir}/bmctl /usr/local/sbin/bmctl
        # accept-new (not yes): the workstation has never contacted the freshly
        # created cluster nodes, so their host keys are not yet known. accept-new
        # pins each node's key on first connect but still rejects a *changed*
        # key later, which is the correct posture for first-boot provisioning of
        # private-network nodes (plain `yes` aborts; `no` disables the change
        # check entirely). Applies to bmctl's node SSH too via this Host * block.
        printf 'Host *\\n  StrictHostKeyChecking accept-new\\n  BatchMode yes\\n' >/root/.ssh/config
        chmod 600 /root/.ssh/config
        """
    )


def render_gdc_prepare_hosts_script(config: GDCBootstrapConfig) -> str:
    """Render the host prep script that creates vxlan0 and hardening on all nodes."""
    peer_ips = " ".join(host.primary_ip for host in config.all_hosts)
    lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        "",
        "configure_node() {",
        '  local vxlan_ip="$1"',
        "  local default_iface",
        "  default_iface=\"$(ip route show default | awk '/default/ {print $5; exit}')\"",
        "  if ! ip link show vxlan0 >/dev/null 2>&1; then",
        '    ip link add vxlan0 type vxlan id 42 dev "$default_iface" dstport 8472',
        "  fi",
        f"  for peer_ip in {peer_ips}; do",
        '    bridge fdb append to 00:00:00:00:00:00 dst "$peer_ip" dev vxlan0 2>/dev/null || true',
        "  done",
        '  ip addr replace "${vxlan_ip}/24" dev vxlan0',
        "  ip link set up dev vxlan0",
        "",
        "  install -d -m 755 /mnt/localpv-disk /mnt/localpv-share",
        "  cat >/etc/sysctl.d/99-gdc-vmruntime-inotify.conf <<'EOF'",
        "fs.inotify.max_user_instances = 1024",
        "fs.inotify.max_user_watches = 1048576",
        "EOF",
        "  sysctl --load /etc/sysctl.d/99-gdc-vmruntime-inotify.conf",
        "}",
        "",
        "configure_remote_host() {",
        '  local host_ip="$1"',
        '  local vxlan_ip="$2"',
        "  ssh -o StrictHostKeyChecking=accept-new -o BatchMode=yes \\",
        '    "root@${host_ip}" "bash -s" -- "${vxlan_ip}" <<\'EOF\'',
        "set -euo pipefail",
        'vxlan_ip="$1"',
        "default_iface=\"$(ip route show default | awk '/default/ {print $5; exit}')\"",
        "if ! ip link show vxlan0 >/dev/null 2>&1; then",
        '  ip link add vxlan0 type vxlan id 42 dev "$default_iface" dstport 8472',
        "fi",
        f"for peer_ip in {peer_ips}; do",
        '  bridge fdb append to 00:00:00:00:00:00 dst "$peer_ip" dev vxlan0 2>/dev/null || true',
        "done",
        'ip addr replace "${vxlan_ip}/24" dev vxlan0',
        "ip link set up dev vxlan0",
        "install -d -m 755 /mnt/localpv-disk /mnt/localpv-share",
        "cat >/etc/sysctl.d/99-gdc-vmruntime-inotify.conf <<'EON'",
        "fs.inotify.max_user_instances = 1024",
        "fs.inotify.max_user_watches = 1048576",
        "EON",
        "sysctl --load /etc/sysctl.d/99-gdc-vmruntime-inotify.conf",
        "EOF",
        "}",
        "",
        f'configure_node "{config.workstation.vxlan_ip}"',
    ]
    lines.extend(f'configure_remote_host "{host.primary_ip}" "{host.vxlan_ip}"' for host in config.cluster_node_hosts)
    return "\n".join(lines) + "\n"


def render_gdc_create_cluster_script(config: GDCBootstrapConfig) -> str:
    """Render the cluster creation and VM Runtime enablement script."""
    return dedent(
        f"""\
        #!/usr/bin/env bash
        set -euo pipefail

        export GOOGLE_APPLICATION_CREDENTIALS=/root/bm-gcr.json
        install -d -m 755 {config.cluster_workspace_dir}

        if [ ! -f {config.kubeconfig_path} ]; then
          bmctl create config -c {config.cluster_id} --force
          install -m 600 {config.staging_bundle_dir}/cluster.yaml \
            {config.cluster_workspace_dir}/{config.cluster_id}.yaml
          bmctl check preflight -c {config.cluster_id}
          bmctl create cluster -c {config.cluster_id}
        fi
        bmctl check vmruntimepfc --kubeconfig {config.kubeconfig_path}
        kubectl --kubeconfig {config.kubeconfig_path} patch vmruntime vmruntime --type merge \
          -p '{{"spec":{{"enabled":true}}}}'
        kubectl --kubeconfig {config.kubeconfig_path} wait \
          --for=jsonpath='{{.status.ready}}'=true vmruntime/vmruntime --timeout=10m
        """
    )


def render_gdc_install_helper_script(config: GDCBootstrapConfig) -> str:
    """Render helper scripts for repeated admin access on the workstation."""
    return dedent(
        f"""\
        #!/usr/bin/env bash
        set -euo pipefail

        cat >/usr/local/bin/shifter-gdc-kubectl <<'EOF'
        #!/usr/bin/env bash
        set -euo pipefail
        exec env KUBECONFIG="{config.kubeconfig_path}" kubectl "$@"
        EOF
        chmod +x /usr/local/bin/shifter-gdc-kubectl

        cat >/usr/local/bin/shifter-gdc-kubeconfig <<'EOF'
        #!/usr/bin/env bash
        set -euo pipefail
        printf '%s\\n' "{config.kubeconfig_path}"
        EOF
        chmod +x /usr/local/bin/shifter-gdc-kubeconfig
        """
    )


_GDC_APISERVER_BACKEND_PORT = 6444


def rewrite_gdc_kubeconfig_for_platform_access(kubeconfig: str, endpoint: str) -> str:
    """Repoint a bmctl kubeconfig at a platform-reachable control-plane endpoint.

    The kubeconfig generated by bmctl targets the cluster's bundled-LB
    ``controlPlaneVIP`` (e.g. ``https://10.200.0.49:443``), which lives on the
    cluster's VXLAN overlay and is unreachable from the platform VPC where the
    provisioner runs (D23). Terraform fronts the control-plane nodes with an
    internal TCP load balancer on the peered range VPC; this rewrites every
    cluster ``server`` to that endpoint (kube-apiserver listens on
    ``*:6444``).

    The apiserver serving certificate's SANs only cover the overlay VIP and the
    per-node overlay IPs/hostnames, never the load-balancer address, so server
    identity cannot be verified against the LB IP without re-issuing the
    cluster certs. Rather than touch the running cluster's certs, the rewritten
    kubeconfig sets ``insecure-skip-tls-verify: true`` and drops the now-unusable
    ``certificate-authority(-data)``. The channel stays TLS-encrypted and the
    client still authenticates with its credentials; access is bounded to the
    provisioner by VPC peering scope, the range-VPC firewall, and the
    ``shifter-jobs`` NetworkPolicy.

    Implemented as a line transform (no YAML dependency: the bootstrap is
    dependency-free) over the standard bmctl kubeconfig shape.
    """
    host, _, port = endpoint.partition(":")
    server = f"https://{host}:{port or _GDC_APISERVER_BACKEND_PORT}"
    out: list[str] = []
    for line in kubeconfig.splitlines():
        stripped = line.lstrip()
        indent = line[: len(line) - len(stripped)]
        if stripped.startswith("server:"):
            out.append(f"{indent}server: {server}")
            out.append(f"{indent}insecure-skip-tls-verify: true")
            continue
        if stripped.startswith("certificate-authority-data:") or stripped.startswith("certificate-authority:"):
            # Mutually exclusive with insecure-skip-tls-verify; drop it.
            continue
        out.append(line)
    return "\n".join(out) + ("\n" if kubeconfig.endswith("\n") else "")


def build_gdc_access_secret_payload(config: GDCBootstrapConfig, kubeconfig: str) -> str:
    """Build the provisioner-facing GDC access bundle stored in Secret Manager."""
    payload = {
        "cluster_id": config.cluster_id,
        "region": config.region,
        "vxlan_cidr": config.vxlan_cidr,
        "network_interface": "vxlan0",
        "range_namespace_prefix": "range",
        "dns_nameservers": ["8.8.8.8"],
        "static_ip_reservation_count": 4,
        "kubeconfig": kubeconfig,
    }
    return json.dumps(payload, indent=2)


def _gdc_ssh_read_file(config: GDCBootstrapConfig, remote_path: str) -> str | None:
    """Read a workstation file over gcloud ssh, returning None when absent."""
    result = subprocess.run(  # nosec B603 B607
        [
            "gcloud",
            "compute",
            "ssh",
            f"root@{config.workstation.name}",
            "--project",
            config.project_id,
            "--zone",
            config.zone,
            "--command",
            f"sudo cat {remote_path}",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout


def _service_account_key_is_active(config: GDCBootstrapConfig, key_payload: str) -> bool:
    """Return True when the service-account key embedded in the payload still exists."""
    try:
        private_key_id = json.loads(key_payload)["private_key_id"]
    except (json.JSONDecodeError, KeyError) as exc:
        raise RuntimeError("Existing workstation service-account key payload is invalid") from exc

    result = subprocess.run(  # nosec B603 B607
        [
            "gcloud",
            "iam",
            "service-accounts",
            "keys",
            "list",
            "--iam-account",
            config.service_account_email,
            "--project",
            config.project_id,
            "--format=value(name)",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip() if result.stderr else _UNKNOWN_ERROR
        raise RuntimeError(f"Failed to list service-account keys for {config.service_account_email}: {stderr}")

    active_key_ids = {line.rstrip("/").split("/")[-1] for line in result.stdout.splitlines() if line.strip()}
    return private_key_id in active_key_ids


def _fetch_existing_gdc_bootstrap_material(config: GDCBootstrapConfig) -> dict[str, str] | None:
    """Reuse the workstation bootstrap credentials when they already exist and remain valid."""
    if not gcloud_resource_exists(
        [
            "gcloud",
            "compute",
            "instances",
            "describe",
            config.workstation.name,
            "--project",
            config.project_id,
            "--zone",
            config.zone,
        ]
    ):
        return None

    material = {
        "private_key": _gdc_ssh_read_file(config, "/root/.ssh/id_rsa"),
        "public_key": _gdc_ssh_read_file(config, "/root/.ssh/id_rsa.pub"),
        "service_account_key": _gdc_ssh_read_file(config, "/root/bm-gcr.json"),
    }
    if any(value is None or not value.strip() for value in material.values()):
        return None

    if not _service_account_key_is_active(config, material["service_account_key"]):
        warn(
            "Workstation bootstrap service-account key is no longer active; a fresh key will be created for this rerun"
        )
        return None

    info(f"Reusing existing bootstrap credentials from {config.workstation.name}")
    return material


def stage_gdc_bootstrap_assets(config: GDCBootstrapConfig, staging_dir: Path, dry_run: bool = False) -> dict[str, Path]:
    """Create the local assets that will be uploaded to the admin workstation."""
    assets_dir = staging_dir / config.cluster_id
    assets_dir.mkdir(parents=True, exist_ok=True)

    private_key_path = assets_dir / "id_rsa"
    public_key_path = assets_dir / "id_rsa.pub"
    service_account_key_path = assets_dir / "bm-gcr.json"
    bmctl_binary_path = assets_dir / "bmctl"
    ssh_metadata_path = assets_dir / "ssh-metadata"
    cluster_config_path = assets_dir / "cluster.yaml"
    workstation_script = assets_dir / "prepare-workstation.sh"
    hosts_script = assets_dir / "prepare-hosts.sh"
    cluster_script = assets_dir / "create-cluster.sh"
    helper_script = assets_dir / "install-helper.sh"

    if dry_run:
        info(f"[DRY-RUN] Would generate bootstrap assets in {assets_dir}")
    else:
        existing_material = _fetch_existing_gdc_bootstrap_material(config)
        if existing_material:
            private_key_path.write_text(existing_material["private_key"])
            public_key_path.write_text(existing_material["public_key"])
            service_account_key_path.write_text(existing_material["service_account_key"])
        else:
            run_cmd(["ssh-keygen", "-t", "rsa", "-N", "", "-f", str(private_key_path)])
            run_cmd(
                [
                    "gcloud",
                    "iam",
                    "service-accounts",
                    "keys",
                    "create",
                    str(service_account_key_path),
                    "--iam-account",
                    config.service_account_email,
                    "--project",
                    config.project_id,
                ]
            )
        run_cmd(["gcloud", "storage", "cp", config.bmctl_gcs_source, str(bmctl_binary_path)])
        private_key_path.chmod(0o600)
        public_key_path.chmod(0o644)
        service_account_key_path.chmod(0o600)
        ssh_metadata_path.write_text(f"root:{public_key_path.read_text().strip()}\n")
        cluster_config_path.write_text(render_gdc_cluster_config(config))
        workstation_script.write_text(render_gdc_prepare_workstation_script(config))
        hosts_script.write_text(render_gdc_prepare_hosts_script(config))
        cluster_script.write_text(render_gdc_create_cluster_script(config))
        helper_script.write_text(render_gdc_install_helper_script(config))
        for script_path in (workstation_script, hosts_script, cluster_script, helper_script):
            script_path.chmod(0o755)

    return {
        "assets_dir": assets_dir,
        "private_key": private_key_path,
        "public_key": public_key_path,
        "service_account_key": service_account_key_path,
        "bmctl_binary": bmctl_binary_path,
        "ssh_metadata": ssh_metadata_path,
        "cluster_config": cluster_config_path,
        "workstation_script": workstation_script,
        "hosts_script": hosts_script,
        "cluster_script": cluster_script,
        "helper_script": helper_script,
    }


def ensure_gdc_apis(config: GDCBootstrapConfig, dry_run: bool = False) -> None:
    """Enable the GDC/GKE/GCP APIs required by the evaluation cluster."""
    run_cmd(["gcloud", "config", "set", "project", config.project_id], dry_run=dry_run)
    run_cmd(["gcloud", "services", "enable", *GDC_API_SERVICES, "--project", config.project_id], dry_run=dry_run)


def wait_for_gdc_service_account_visible(
    config: GDCBootstrapConfig,
    *,
    timeout_seconds: int = 60,
    poll_seconds: int = 2,
) -> None:
    """Wait for the shared GDC service account to become visible to follow-on IAM calls."""
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if gcloud_resource_exists(
            [
                "gcloud",
                "iam",
                "service-accounts",
                "describe",
                config.service_account_email,
                "--project",
                config.project_id,
            ]
        ):
            return
        time.sleep(poll_seconds)
    raise RuntimeError(
        f"GDC service account {config.service_account_email} did not become visible within {timeout_seconds} seconds"
    )


def _is_retryable_gcp_iam_binding_error(message: str) -> bool:
    """Return True when a project IAM binding failed with a transient error worth retrying.

    Two distinct races are covered:

    1. **Member propagation** — service-account creation is eventually consistent
       *across* GCP subsystems: a new SA can resolve via ``gcloud iam
       service-accounts describe`` while ``add-iam-policy-binding`` still rejects
       it as nonexistent ("service account ... does not exist") for a short window.

    2. **Concurrent policy change (ETag conflict)** — ``add-iam-policy-binding`` is
       a read-modify-write; if the project IAM policy changes between read and
       write (e.g. Google asynchronously provisioning service agents right after an
       API enable), the write fails with "concurrent policy changes" / an ETag
       mismatch. GCP's guidance for this is to retry with exponential backoff.

    Both are first-run/transient and clear on retry.
    """
    normalized_message = message.lower()
    member_not_yet_visible = "does not exist" in normalized_message and "service account" in normalized_message
    concurrent_policy_change = (
        "concurrent policy change" in normalized_message
        or "the subject of a conflict" in normalized_message
        or ("etag" in normalized_message and "did not match" in normalized_message)
    )
    return member_not_yet_visible or concurrent_policy_change


def add_project_iam_binding_with_retry(
    project_id: str,
    member: str,
    role: str,
    *,
    dry_run: bool = False,
    max_attempts: int = 12,
    sleep_seconds: int = 5,
) -> None:
    """Bind a project IAM role, retrying transient binding races — member
    propagation and concurrent-policy-change (ETag) conflicts — with backoff
    (see ``_is_retryable_gcp_iam_binding_error``)."""
    binding_cmd = [
        "gcloud",
        "projects",
        "add-iam-policy-binding",
        project_id,
        "--member",
        member,
        "--role",
        role,
        "--no-user-output-enabled",
    ]
    if dry_run:
        print(f"{Colors.BLUE}[DRY-RUN] Would run: {_redact_argv_for_log(binding_cmd)}{Colors.END}")
        return

    info(f"Running: {_redact_argv_for_log(binding_cmd)}")
    for attempt in range(1, max_attempts + 1):
        result = subprocess.run(binding_cmd, capture_output=True, text=True, check=False)  # nosec B603 B607
        if result.stdout:
            print(result.stdout, end="")
        if result.stderr:
            print(result.stderr, end="", file=sys.stderr)
        if result.returncode == 0:
            return

        combined_output = "\n".join(part for part in (result.stdout, result.stderr) if part).strip()
        if _is_retryable_gcp_iam_binding_error(combined_output) and attempt < max_attempts:
            warn(
                f"Transient IAM binding race for {role} on {member} "
                f"(propagation or concurrent policy change); retrying in {sleep_seconds}s "
                f"({attempt}/{max_attempts})"
            )
            time.sleep(sleep_seconds)
            continue

        error(f"Command failed: Command '{' '.join(binding_cmd)}' returned non-zero exit status {result.returncode}.")
        sys.exit(1)


def ensure_gdc_service_account(config: GDCBootstrapConfig, dry_run: bool = False) -> None:
    """Create the shared GDC service account and grant the required project roles."""
    service_account_exists = gcloud_resource_exists(
        [
            "gcloud",
            "iam",
            "service-accounts",
            "describe",
            config.service_account_email,
            "--project",
            config.project_id,
        ]
    )

    if dry_run or not service_account_exists:
        run_cmd(
            [
                "gcloud",
                "iam",
                "service-accounts",
                "create",
                config.service_account_name,
                "--project",
                config.project_id,
            ],
            dry_run=dry_run,
            check=False,
        )
        if not dry_run:
            wait_for_gdc_service_account_visible(config)

    member = f"serviceAccount:{config.service_account_email}"
    for role in GDC_SERVICE_ACCOUNT_ROLES:
        add_project_iam_binding_with_retry(config.project_id, member, role, dry_run=dry_run)


def ensure_gdc_access_secret(config: GDCBootstrapConfig, dry_run: bool = False) -> None:
    """Ensure the provisioner-facing GDC access secret exists."""
    if dry_run or not gcloud_resource_exists(
        [
            "gcloud",
            "secrets",
            "describe",
            config.gdc_access_secret_id,
            "--project",
            config.project_id,
        ]
    ):
        run_cmd(
            [
                "gcloud",
                "secrets",
                "create",
                config.gdc_access_secret_id,
                "--replication-policy",
                "automatic",
                "--project",
                config.project_id,
            ],
            dry_run=dry_run,
            check=False,
        )


def ensure_gdc_vm_image_secret(config: GDCBootstrapConfig, dry_run: bool = False) -> None:
    """Ensure the VM Runtime image-import Secret Manager secret exists."""
    if dry_run or not gcloud_resource_exists(
        [
            "gcloud",
            "secrets",
            "describe",
            config.gdc_vm_image_gcs_secret_id,
            "--project",
            config.project_id,
        ]
    ):
        run_cmd(
            [
                "gcloud",
                "secrets",
                "create",
                config.gdc_vm_image_gcs_secret_id,
                "--replication-policy",
                "automatic",
                "--project",
                config.project_id,
            ],
            dry_run=dry_run,
            check=False,
        )


def _ensure_gcloud_resource(describe_args: list[str], create_args: list[str], *, dry_run: bool) -> None:
    """Run `create_args` unless the resource described by `describe_args` already exists."""
    if dry_run or not gcloud_resource_exists(describe_args):
        run_cmd(create_args, dry_run=dry_run)


def ensure_gdc_network(config: GDCBootstrapConfig, dry_run: bool = False) -> None:
    """Create the custom VPC, subnet, and firewall rules used by the cluster."""
    _ensure_gcloud_resource(
        ["gcloud", "compute", "networks", "describe", config.resolved_network_name, "--project", config.project_id],
        [
            "gcloud",
            "compute",
            "networks",
            "create",
            config.resolved_network_name,
            "--project",
            config.project_id,
            "--subnet-mode",
            "custom",
        ],
        dry_run=dry_run,
    )

    _ensure_gcloud_resource(
        [
            "gcloud",
            "compute",
            "networks",
            "subnets",
            "describe",
            config.resolved_subnetwork_name,
            "--project",
            config.project_id,
            "--region",
            config.region,
        ],
        [
            "gcloud",
            "compute",
            "networks",
            "subnets",
            "create",
            config.resolved_subnetwork_name,
            "--project",
            config.project_id,
            "--network",
            config.resolved_network_name,
            "--region",
            config.region,
            "--range",
            config.subnet_cidr,
            "--enable-private-ip-google-access",
        ],
        dry_run=dry_run,
    )

    _ensure_gcloud_resource(
        [
            "gcloud",
            "compute",
            "routers",
            "describe",
            config.cloud_router_name,
            "--project",
            config.project_id,
            "--region",
            config.region,
        ],
        [
            "gcloud",
            "compute",
            "routers",
            "create",
            config.cloud_router_name,
            "--project",
            config.project_id,
            "--region",
            config.region,
            "--network",
            config.resolved_network_name,
        ],
        dry_run=dry_run,
    )

    _ensure_gcloud_resource(
        [
            "gcloud",
            "compute",
            "routers",
            "nats",
            "describe",
            config.cloud_nat_name,
            "--project",
            config.project_id,
            "--router",
            config.cloud_router_name,
            "--region",
            config.region,
        ],
        [
            "gcloud",
            "compute",
            "routers",
            "nats",
            "create",
            config.cloud_nat_name,
            "--project",
            config.project_id,
            "--router",
            config.cloud_router_name,
            "--region",
            config.region,
            "--auto-allocate-nat-external-ips",
            "--nat-custom-subnet-ip-ranges",
            config.resolved_subnetwork_name,
            "--enable-logging",
        ],
        dry_run=dry_run,
    )

    firewall_rules = [
        (
            config.ssh_firewall_rule_name,
            "tcp:22",
            GCP_IAP_TCP_SOURCE_RANGE,
        ),
        (
            config.internal_firewall_rule_name,
            "tcp,udp,icmp",
            config.subnet_cidr,
        ),
        (
            config.lb_firewall_rule_name,
            "tcp:443,tcp:6444",
            config.subnet_cidr,
        ),
    ]

    for name, rules, source_ranges in firewall_rules:
        _ensure_gcloud_resource(
            ["gcloud", "compute", "firewall-rules", "describe", name, "--project", config.project_id],
            [
                "gcloud",
                "compute",
                "firewall-rules",
                "create",
                name,
                "--project",
                config.project_id,
                "--network",
                config.resolved_network_name,
                "--direction",
                "INGRESS",
                "--allow",
                rules,
                "--source-ranges",
                source_ranges,
                "--target-tags",
                config.instance_tag,
            ],
            dry_run=dry_run,
        )


def gdc_instance_create_command(
    config: GDCBootstrapConfig,
    host: GDCHost,
    ssh_metadata_path: Path,
) -> list[str]:
    """Build the gcloud command to create a single cluster VM."""
    return [
        "gcloud",
        "compute",
        "instances",
        "create",
        host.name,
        "--project",
        config.project_id,
        "--zone",
        config.zone,
        "--machine-type",
        config.machine_type,
        "--boot-disk-size",
        f"{config.boot_disk_size_gb}G",
        "--boot-disk-type",
        config.boot_disk_type,
        "--image-family",
        "ubuntu-2204-lts",
        "--image-project",
        "ubuntu-os-cloud",
        "--subnet",
        config.resolved_subnetwork_name,
        "--no-address",
        "--private-network-ip",
        host.primary_ip,
        "--can-ip-forward",
        "--min-cpu-platform",
        "Intel Haswell",
        "--enable-nested-virtualization",
        "--service-account",
        config.service_account_email,
        "--scopes",
        "cloud-platform",
        "--tags",
        config.instance_tag,
        "--metadata",
        f"cluster_id={config.cluster_id},bmctl_version={config.bmctl_version},enable-oslogin=FALSE",
        "--metadata-from-file",
        f"ssh-keys={ssh_metadata_path}",
    ]


def ensure_gdc_instances(config: GDCBootstrapConfig, ssh_metadata_path: Path, dry_run: bool = False) -> None:
    """Create the workstation and cluster nodes if they do not already exist."""
    for host in config.all_hosts:
        if dry_run or not gcloud_resource_exists(
            [
                "gcloud",
                "compute",
                "instances",
                "describe",
                host.name,
                "--project",
                config.project_id,
                "--zone",
                config.zone,
            ]
        ):
            run_cmd(gdc_instance_create_command(config, host, ssh_metadata_path), dry_run=dry_run)


def get_gdc_instance_ssh_metadata(config: GDCBootstrapConfig, host_name: str) -> str:
    """Return the current ssh-keys metadata value for the given host."""
    result = subprocess.run(  # nosec B603 B607
        [
            "gcloud",
            "compute",
            "instances",
            "describe",
            host_name,
            "--project",
            config.project_id,
            "--zone",
            config.zone,
            "--format=get(metadata.items[ssh-keys])",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip() if result.stderr else _UNKNOWN_ERROR
        raise RuntimeError(f"Failed to read ssh metadata for {host_name}: {stderr}")
    return result.stdout


def sync_gdc_instance_ssh_metadata(config: GDCBootstrapConfig, ssh_metadata_path: Path, dry_run: bool = False) -> None:
    """Ensure all instances trust the current bootstrap key pair."""
    # In dry-run the staging step does not materialise the ssh-metadata file, and
    # the per-host comparison below is skipped, so avoid reading a file that does
    # not exist (the metadata is only needed to short-circuit unchanged hosts).
    expected_metadata = "" if dry_run else ssh_metadata_path.read_text().strip()
    for host in config.all_hosts:
        if not dry_run:
            current_metadata = get_gdc_instance_ssh_metadata(config, host.name).strip()
            if current_metadata == expected_metadata:
                continue
        run_cmd(
            [
                "gcloud",
                "compute",
                "instances",
                "add-metadata",
                host.name,
                "--project",
                config.project_id,
                "--zone",
                config.zone,
                "--metadata-from-file",
                f"ssh-keys={ssh_metadata_path}",
            ],
            dry_run=dry_run,
        )


def wait_for_gdc_ssh(config: GDCBootstrapConfig, host: GDCHost, dry_run: bool = False) -> None:
    """Wait until gcloud compute ssh succeeds for the given host."""
    if dry_run:
        info(f"[DRY-RUN] Would wait for SSH on {host.name}")
        return

    for attempt in range(1, 31):
        result = subprocess.run(  # nosec B603 B607
            [
                "gcloud",
                "compute",
                "ssh",
                f"root@{host.name}",
                "--tunnel-through-iap",
                "--project",
                config.project_id,
                "--zone",
                config.zone,
                "--command",
                "printf ready",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            return
        info(f"Waiting for SSH on {host.name} (attempt {attempt}/30)")
        time.sleep(10)

    error(f"Timed out waiting for SSH on {host.name}")
    sys.exit(1)


def upload_gdc_assets(config: GDCBootstrapConfig, assets_dir: Path, dry_run: bool = False) -> None:
    """Upload the rendered bootstrap bundle to the admin workstation."""
    run_cmd(
        [
            "gcloud",
            "compute",
            "ssh",
            f"root@{config.workstation.name}",
            "--tunnel-through-iap",
            "--project",
            config.project_id,
            "--zone",
            config.zone,
            "--command",
            f"rm -rf {config.staging_bundle_dir} && mkdir -p {config.staging_dir}",
        ],
        dry_run=dry_run,
    )
    run_cmd(
        [
            "gcloud",
            "compute",
            "scp",
            "--recurse",
            "--tunnel-through-iap",
            "--project",
            config.project_id,
            "--zone",
            config.zone,
            str(assets_dir),
            f"root@{config.workstation.name}:{config.staging_dir}/",
        ],
        dry_run=dry_run,
    )


def run_gdc_workstation_script(
    config: GDCBootstrapConfig,
    script_name: str,
    dry_run: bool = False,
) -> None:
    """Execute a staged script on the admin workstation."""
    run_cmd(
        [
            "gcloud",
            "compute",
            "ssh",
            f"root@{config.workstation.name}",
            "--tunnel-through-iap",
            "--project",
            config.project_id,
            "--zone",
            config.zone,
            "--command",
            f"bash {config.staging_dir}/{config.cluster_id}/{script_name}",
        ],
        dry_run=dry_run,
    )


def fetch_gdc_kubeconfig(config: GDCBootstrapConfig, dry_run: bool = False) -> str:
    """Fetch the generated kubeconfig from the admin workstation."""
    if dry_run:
        return ""

    result = run_cmd(
        [
            "gcloud",
            "compute",
            "ssh",
            f"root@{config.workstation.name}",
            "--tunnel-through-iap",
            "--project",
            config.project_id,
            "--zone",
            config.zone,
            "--command",
            f"cat {config.kubeconfig_path}",
        ],
        capture=True,
    )
    if result is None or not result.stdout.strip():
        error("Failed to read the GDC kubeconfig from the admin workstation")
        sys.exit(1)
    return result.stdout


def sync_gdc_access_secret(config: GDCBootstrapConfig, dry_run: bool = False) -> None:
    """Publish the current GDC kubeconfig and range-plane settings to Secret Manager."""
    ensure_gdc_access_secret(config, dry_run=dry_run)
    kubeconfig = fetch_gdc_kubeconfig(config, dry_run=dry_run)
    if config.control_plane_platform_endpoint:
        # Repoint the provisioner kubeconfig at the platform-reachable
        # control-plane load balancer; the bmctl kubeconfig targets the
        # overlay VIP that the platform VPC cannot reach (D23).
        kubeconfig = rewrite_gdc_kubeconfig_for_platform_access(kubeconfig, config.control_plane_platform_endpoint)
    payload = build_gdc_access_secret_payload(config, kubeconfig)

    if dry_run:
        info(f"[DRY-RUN] Would add a new version to Secret Manager secret {config.gdc_access_secret_id}")
        return
    latest_payload = get_latest_gcp_secret_payload(config.gdc_access_secret_id, config.project_id)
    if latest_payload == payload:
        info(f"GDC access secret {config.gdc_access_secret_id} already matches the desired payload")
        return

    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".json") as handle:
        handle.write(payload)
        payload_path = Path(handle.name)

    try:
        run_cmd(
            [
                "gcloud",
                "secrets",
                "versions",
                "add",
                config.gdc_access_secret_id,
                "--data-file",
                str(payload_path),
                "--project",
                config.project_id,
            ]
        )
    finally:
        payload_path.unlink(missing_ok=True)


def sync_gdc_vm_image_secret(config: GDCBootstrapConfig, service_account_key_path: Path, dry_run: bool = False) -> None:
    """Publish the GCS image-import key to Secret Manager for range provisioning."""
    ensure_gdc_vm_image_secret(config, dry_run=dry_run)
    if not dry_run:
        latest_payload = get_latest_gcp_secret_payload(config.gdc_vm_image_gcs_secret_id, config.project_id)
        desired_payload = service_account_key_path.read_text()
        if latest_payload == desired_payload:
            info(
                f"GDC VM image secret {config.gdc_vm_image_gcs_secret_id} already matches "
                "the desired service-account key"
            )
            return
    run_cmd(
        [
            "gcloud",
            "secrets",
            "versions",
            "add",
            config.gdc_vm_image_gcs_secret_id,
            "--data-file",
            str(service_account_key_path),
            "--project",
            config.project_id,
        ],
        dry_run=dry_run,
    )


def _load_python_script_module(script_path: Path, module_name: str):
    """Load a local Python script as a module without changing repo packaging."""
    spec = importlib.util.spec_from_file_location(module_name, script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load Python module from {script_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _get_output_value(outputs: dict[str, dict[str, object]], key: str):
    """Return the Terraform output value for a key or raise a clear error."""
    try:
        return outputs[key]["value"]
    except KeyError as exc:
        raise KeyError(f"Missing Terraform output: {key}") from exc


def _merge_csv_env_values(*groups: list[str]) -> str:
    """Merge comma-separated values while preserving order and uniqueness."""
    ordered: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for raw_value in group:
            for part in raw_value.split(","):
                value = part.strip().lower()
                if not value or value in seen:
                    continue
                seen.add(value)
                ordered.append(value)
    return ",".join(ordered)


def _unique_nonempty_strings(values: list[str | None]) -> list[str]:
    """Return non-empty strings in first-seen order."""
    ordered: list[str] = []
    seen: set[str] = set()
    for raw_value in values:
        value = (raw_value or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def _host_as_single_address_cidr(value: object) -> str | None:
    """Convert a Terraform host/IP output into a /32 or /128 CIDR."""
    if value is None:
        return None
    host = str(value).strip()
    if not host:
        return None
    if "/" in host:
        return host
    try:
        address = ipaddress.ip_address(host)
    except ValueError as exc:
        raise ValueError(f"Expected IP address Terraform output, got {host!r}") from exc
    prefix = 32 if address.version == 4 else 128
    return f"{address}/{prefix}"


def render_gcp_platform_runtime_env(
    config: GDCBootstrapConfig,
    *,
    bootstrap_operator_email: str | None = None,
) -> str:
    """Render the static, project-aware runtime env contract for the GKE control plane."""
    gdc_vm_image_secret = f"projects/{config.project_id}/secrets/{config.gdc_vm_image_gcs_secret_id}"
    bootstrap_values = load_bootstrap_env_values()
    bootstrap_staff_emails = _merge_csv_env_values(
        [bootstrap_values.get("PLATFORM_BOOTSTRAP_STAFF_EMAILS", "")],
        [bootstrap_operator_email or ""],
    )
    bootstrap_superuser_emails = _merge_csv_env_values(
        [bootstrap_values.get("PLATFORM_BOOTSTRAP_SUPERUSER_EMAILS", "")],
        [bootstrap_operator_email or ""],
    )
    lines = [
        "CLOUD_PROVIDER=gcp",
        f"ENVIRONMENT={config.environment}",
        f"CLOUD_REGION={config.region}",
        f"GCP_REGION={config.region}",
        f"GCP_PROJECT_ID={config.project_id}",
        f"GOOGLE_CLOUD_PROJECT={config.project_id}",
        "ENGINE_TASK_NAMESPACE=shifter-jobs",
        "ENGINE_TASK_SERVICE_ACCOUNT_NAME=provisioner",
        "ENGINE_TASK_IMAGE_PULL_POLICY=Always",
        "GDC_VM_STORAGE_CLASS=local-shared",
        f"GDC_VM_IMAGE_GCS_SECRET_ID={gdc_vm_image_secret}",
        "# Palo Alto VM-Series on GDC VM Runtime. These are required before creating",
        "# a GCP/GDC NGFW; values are intentionally explicit because this is not a",
        "# generic firewall path.",
        "GDC_VMSERIES_IMAGE_URL=",
        "GDC_VMSERIES_BOOTSTRAP_BUCKET=",
        "GDC_VMSERIES_STORAGE_CLASS=local-shared",
        f"GDC_VMSERIES_IMAGE_GCS_SECRET_ID={gdc_vm_image_secret}",
        "GDC_VMSERIES_NAMESPACE_PREFIX=ngfw",
        "GDC_VMSERIES_MGMT_NETWORK_NAME=pod-network",
        "GDC_VMSERIES_MGMT_IP_CIDR=",
        "GDC_VMSERIES_DATA_NETWORK_NAME=",
        "GDC_VMSERIES_DATA_IP_CIDR=",
        "GDC_VMSERIES_ROUTE_NEXT_HOP_IP=",
        "GDC_VMSERIES_VCPUS=4",
        "GDC_VMSERIES_MEMORY=8Gi",
        "GDC_VMSERIES_DISK_SIZE_GIB=81",
        "GDC_VMSERIES_BOOTSTRAP_DISK_SIZE_GIB=1",
        "GDC_VMSERIES_BOOTSTRAP_XML_TEMPLATE_SECRET_ID=",
        "# Guest access defaults for VM Runtime assets.",
        *_sample_guest_access_defaults(),
        "# VM Runtime boot images, exported by the packer-gcp pipeline to the GDC",
        "# VM image bucket. DISK_SIZE_GIB must be >= the exported qcow2 virtual",
        "# size (the GCE builder disk size): ubuntu 20, kali 40, windows/dc 64.",
        f"GDC_KALI_IMAGE_URL={config.gdc_vm_image_url('kali')}",
        "GDC_KALI_VCPUS=2",
        "GDC_KALI_MEMORY=4Gi",
        "GDC_KALI_DISK_SIZE_GIB=40",
        f"GDC_UBUNTU_IMAGE_URL={config.gdc_vm_image_url('ubuntu')}",
        "GDC_UBUNTU_VCPUS=1",
        "GDC_UBUNTU_MEMORY=2Gi",
        "GDC_UBUNTU_DISK_SIZE_GIB=20",
        f"GDC_WINDOWS_IMAGE_URL={config.gdc_vm_image_url('windows')}",
        "GDC_WINDOWS_VCPUS=2",
        "GDC_WINDOWS_MEMORY=8Gi",
        "GDC_WINDOWS_DISK_SIZE_GIB=64",
        f"GDC_DC_IMAGE_URL={config.gdc_vm_image_url('dc')}",
        "GDC_DC_VCPUS=2",
        "GDC_DC_MEMORY=8Gi",
        "GDC_DC_DISK_SIZE_GIB=64",
        "# Optional overrides for lower-fidelity in-range scenario Pods.",
        "GDC_SCENARIO_POD_IMAGE_PULL_POLICY=IfNotPresent",
        f"GDC_SCENARIO_POD_KALI_IMAGE={_GDC_SCENARIO_POD_KALI_IMAGE}",
        "GDC_SCENARIO_POD_UBUNTU_IMAGE=docker.io/library/ubuntu:24.04",
        f"PLATFORM_BOOTSTRAP_STAFF_EMAILS={bootstrap_staff_emails}",
        f"PLATFORM_BOOTSTRAP_SUPERUSER_EMAILS={bootstrap_superuser_emails}",
    ]
    return "".join(f"{line}\n" for line in lines)


def parse_env_contract(rendered: str) -> dict[str, str]:
    """Parse KEY=VALUE env contract text into a mapping, ignoring blank lines and comments."""
    values: dict[str, str] = {}
    for raw_line in rendered.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator:
            raise ValueError(f"Invalid env contract line: {raw_line!r}")
        values[key] = value
    return values


def validate_image_tag(image_tag: str) -> str:
    """Return a non-moving image tag suitable for deployment manifests."""
    tag = image_tag.strip()
    if not tag:
        raise ValueError("image tag must be non-empty")
    if tag == "latest":
        raise ValueError("image tag must be immutable; refusing to use latest")
    return tag


def resolve_gcp_control_plane_image_tag() -> str:
    """Resolve the immutable tag used for all GCP control-plane images."""
    env_tag = os.environ.get("SHIFTER_IMAGE_TAG", "").strip()
    if env_tag:
        return validate_image_tag(env_tag)

    github_sha = os.environ.get("GITHUB_SHA", "").strip()
    if github_sha:
        return validate_image_tag(github_sha[:7])

    result = run_cmd(
        ["git", "-C", str(get_repo_root()), "rev-parse", "--short=7", "HEAD"],
        check=False,
        capture=True,
    )
    if result is None:
        raise RuntimeError("Unable to resolve image tag from git")
    if result.returncode != 0:
        stderr = result.stderr.strip() if result.stderr else _UNKNOWN_ERROR
        raise RuntimeError(f"Unable to resolve image tag from git: {stderr}")
    return validate_image_tag(result.stdout.strip())


def parse_simple_env_file(path: Path) -> dict[str, str]:
    """Parse a basic KEY=VALUE env file into a mapping."""
    if not path.exists():
        return {}

    values: dict[str, str] = {}
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        parsed_value = value.strip()
        if len(parsed_value) >= 2 and parsed_value[0] == parsed_value[-1] and parsed_value[0] in {"'", '"'}:
            parsed_value = parsed_value[1:-1]
        values[key.strip()] = parsed_value
    return values


def load_bootstrap_env_values() -> dict[str, str]:
    """Load bootstrap values from repo-local env files, then overlay the process environment."""
    repo_root = get_repo_root()
    values: dict[str, str] = {}
    for env_path in [repo_root / ".env", repo_root.parent / "shifter" / ".env"]:
        values.update(parse_simple_env_file(env_path))
    values.update(os.environ)
    return values


def resolve_gcp_bootstrap_operator_credentials() -> tuple[str, str] | None:
    """Resolve the first operator email/password for the GCP identity bootstrap."""
    values = load_bootstrap_env_values()

    email = (
        values.get("GCP_BOOTSTRAP_ADMIN_EMAIL")
        or values.get("SHIFTER_BOOTSTRAP_ADMIN_EMAIL")
        or values.get("BOOTSTRAP_ADMIN_EMAIL")
    )
    password = (
        values.get("GCP_BOOTSTRAP_ADMIN_PASSWORD")
        or values.get("SHIFTER_BOOTSTRAP_ADMIN_PASSWORD")
        or values.get("BOOTSTRAP_ADMIN_PASSWORD")
    )
    if not email or not password:
        return None
    return email.strip().lower(), password.strip()


def prompt_for_gcp_bootstrap_operator_credentials() -> tuple[str, str]:
    """Collect the first GCP operator email/password interactively."""
    header("Configure GCP Operator Login")
    print(
        "Bootstrap will create the first corporate Shifter operator in Identity Platform.\n"
        "They will enroll TOTP MFA on first sign-in.\n"
    )

    email = prompt_required_value("Operator email").lower()
    password = prompt_required_value("Operator password", secret=True)
    return email, password


def _resolve_operator_email_domain(outputs: dict[str, dict[str, object]] | None) -> tuple[str, str]:
    """Resolve the required operator email domain and its source (Terraform output, else env)."""
    if outputs is not None:
        tf_value = outputs.get("identity_allowed_email_domain", {}).get("value")
        if isinstance(tf_value, str) and tf_value.strip():
            return tf_value.strip().lower(), "Terraform output identity_allowed_email_domain"
    env_value = os.environ.get("SHIFTER_GCP_OPERATOR_EMAIL_DOMAIN", "").strip().lower()
    if env_value:
        return env_value, "SHIFTER_GCP_OPERATOR_EMAIL_DOMAIN"
    return "", ""


def _validate_gcp_bootstrap_operator_email(
    email: str,
    outputs: dict[str, dict[str, object]] | None = None,
) -> None:
    """Validate the bootstrap operator email against the Identity Platform allow-list.

    The shape check (must contain a single `@`, non-empty local + domain parts)
    is always enforced. The domain restriction is derived, in order:

    1. The ``identity_allowed_email_domain`` Terraform output (when ``outputs``
       is supplied) — this is the same value the Identity Platform
       ``beforeCreate`` hook uses, so the bootstrap operator the bootstrap
       script writes into ``PLATFORM_BOOTSTRAP_*`` will actually be able to
       sign in to the deployed portal.
    2. The ``SHIFTER_GCP_OPERATOR_EMAIL_DOMAIN`` environment variable as a
       fallback for callers that have not yet run Terraform (e.g., unit tests
       or dry-run flows). Unset means "accept any well-formed email" — only
       legitimate when no Identity Platform deployment is in scope.
    """
    if email.count("@") != 1:
        raise ValueError("GCP operator email must contain exactly one '@' character")
    local, _, domain = email.partition("@")
    if not local or not domain:
        raise ValueError("GCP operator email must have a non-empty local part and domain")

    required_domain, source = _resolve_operator_email_domain(outputs)

    if required_domain and not email.lower().endswith(f"@{required_domain}"):
        raise ValueError(
            f"GCP operator email must use the {required_domain} domain "
            f"(constraint from {source}). Bootstrap-time validation matches the "
            "Identity Platform allow-list, so an operator whose domain fails "
            "here cannot subsequently sign in to the deployed portal."
        )


def _gcp_identity_access_token() -> str:
    result = subprocess.run(  # nosec B603 B607
        ["gcloud", "auth", "print-access-token"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip() if result.stderr else _UNKNOWN_ERROR
        raise RuntimeError(f"Failed to acquire a GCP access token for Identity Platform: {stderr}")
    return result.stdout.strip()


def _gcp_identity_admin_request(
    *,
    config: GDCBootstrapConfig,
    outputs: dict[str, dict[str, object]],
    path: str,
    payload: dict[str, object],
) -> dict[str, object]:
    del outputs
    access_token = _gcp_identity_access_token()
    url = f"https://identitytoolkit.googleapis.com/v1{path}"
    parsed_url = urllib_parse.urlparse(url)
    if parsed_url.scheme != "https" or parsed_url.netloc != "identitytoolkit.googleapis.com" or parsed_url.query:
        raise RuntimeError(f"Refusing to call unexpected Identity Platform endpoint: {url}")
    request = urllib_request.Request(  # noqa: S310 - URL is validated immediately above
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "X-Goog-User-Project": config.project_id,
        },
        method="POST",
    )

    try:
        with urllib_request.urlopen(request, timeout=30) as response:  # nosec B310  # noqa: S310
            return json.loads(response.read().decode("utf-8"))
    except urllib_error.HTTPError as exc:  # pragma: no cover - exercised via unit tests with monkeypatch
        body = exc.read().decode("utf-8") if exc.fp is not None else ""
        try:
            parsed = json.loads(body) if body else {}
        except json.JSONDecodeError:
            parsed = {}
        message = parsed.get("error", {}).get("message", exc.reason)
        raise RuntimeError(str(message)) from exc


def ensure_gcp_identity_platform_operator(
    config: GDCBootstrapConfig,
    outputs: dict[str, dict[str, object]],
    dry_run: bool = False,
) -> str | None:
    """Create the first GCP operator account if it does not already exist."""
    credentials = resolve_gcp_bootstrap_operator_credentials()
    if credentials is None:
        if dry_run:
            info("[DRY-RUN] Would prompt for the first GCP operator email and password")
            return None
        credentials = prompt_for_gcp_bootstrap_operator_credentials()

    email, password = credentials
    _validate_gcp_bootstrap_operator_email(email, outputs=outputs)

    if dry_run:
        info(f"[DRY-RUN] Would create or verify the Identity Platform operator account for {email}")
        return email

    try:
        _gcp_identity_admin_request(
            config=config,
            outputs=outputs,
            path=f"/projects/{config.project_id}/accounts",
            payload={
                "email": email,
                "password": password,
                "displayName": "Shifter Operator",
                "emailVerified": True,
            },
        )
        success(f"Created Identity Platform operator {email}")
        return email
    except RuntimeError as exc:
        if "EMAIL_EXISTS" in str(exc):
            info(f"Identity Platform operator {email} already exists")
            return email
        raise


def render_gcp_helm_values(
    config: GDCBootstrapConfig,
    outputs: dict[str, dict[str, object]],
    *,
    guacamole_db_payload: dict[str, str],
    guacamole_json_secret: str,
    image_tag: str,
    bootstrap_operator_email: str | None = None,
) -> dict[str, object]:
    """Render Helm values for the Shifter release from Terraform outputs and runtime secrets."""
    pinned_image_tag = validate_image_tag(image_tag)
    image_roots = _get_output_value(outputs, "artifact_registry_image_roots")
    service_accounts = _get_output_value(outputs, "workload_service_accounts")
    public_hostname = str(_get_output_value(outputs, "public_hostname")).strip()
    managed_tls_enabled = bool(_get_output_value(outputs, "managed_tls_enabled"))
    runtime_renderer = _load_python_script_module(
        get_repo_root() / "scripts" / "gcp" / "render_runtime_env.py",
        "bootstrap_render_runtime_env",
    )
    runtime_env = {
        **parse_env_contract(
            render_gcp_platform_runtime_env(config, bootstrap_operator_email=bootstrap_operator_email)
        ),
        **parse_env_contract(runtime_renderer.render_env(outputs, image_tag=pinned_image_tag)),
    }
    edge_policy_name = str(_get_output_value(outputs, "cloud_armor_security_policy_name")).strip()
    control_plane_database = _get_output_value(outputs, "control_plane_database")
    control_plane_cache = _get_output_value(outputs, "control_plane_cache")
    guacamole_database = _get_output_value(outputs, "guacamole_database")
    private_service_cidrs = _unique_nonempty_strings(
        [
            _host_as_single_address_cidr(control_plane_database.get("private_ip")),
            _host_as_single_address_cidr(control_plane_cache.get("host")),
            _host_as_single_address_cidr(guacamole_database.get("host")),
            str(_get_output_value(outputs, "gke_services_cidr")).strip(),
        ]
    )
    # The range-provisioning Jobs reach the GDC range cluster apiserver through
    # the internal TCP load balancer on the peered range VPC (D23). Allow egress
    # to exactly that endpoint host/port; empty until the endpoint is wired.
    range_cluster_host, _, range_cluster_port = (config.control_plane_platform_endpoint or "").partition(":")
    range_cluster_api_cidrs = _unique_nonempty_strings([_host_as_single_address_cidr(range_cluster_host)])

    return {
        "releaseNamespace": "shifter-system",
        "serviceAccounts": {
            "portal": {
                "annotations": {
                    _GKE_WORKLOAD_IDENTITY_ANNOTATION: service_accounts["portal"],
                }
            },
            "workers": {
                "annotations": {
                    _GKE_WORKLOAD_IDENTITY_ANNOTATION: service_accounts["workers"],
                }
            },
            "ctfScheduler": {
                "annotations": {
                    _GKE_WORKLOAD_IDENTITY_ANNOTATION: service_accounts["ctf-scheduler"],
                }
            },
            "provisioner": {
                "annotations": {
                    _GKE_WORKLOAD_IDENTITY_ANNOTATION: service_accounts["provisioner"],
                }
            },
        },
        "runtimeEnv": runtime_env,
        "guacamoleRuntimeSecret": {
            "enabled": True,
            "name": "guacamole-runtime",
            "stringData": {
                "POSTGRESQL_USER": guacamole_db_payload["username"],
                "POSTGRESQL_PASSWORD": guacamole_db_payload["password"],
                "JSON_SECRET_KEY": guacamole_json_secret,
            },
        },
        "images": {
            "portal": {
                "repository": image_roots["portal"],
                "tag": pinned_image_tag,
                "pullPolicy": "Always",
            },
            "guacd": {
                "repository": image_roots["guacd"],
                "tag": pinned_image_tag,
                "pullPolicy": "Always",
            },
            "guacamoleClient": {
                "repository": image_roots["guacamole-client"],
                "tag": pinned_image_tag,
                "pullPolicy": "Always",
            },
        },
        "ingress": {
            "enabled": True,
            "class": "gce",
            "staticIpName": _get_output_value(outputs, "public_ingress_ip_name"),
            "host": public_hostname,
            "managedTls": {
                "enabled": managed_tls_enabled,
                "certificateName": "platform-managed-cert",
                "frontendConfigName": "platform-frontend-config",
            },
        },
        "services": {
            "portal": {
                "backendConfig": {
                    "enabled": True,
                    "name": "portal-web",
                    "securityPolicyName": edge_policy_name,
                }
            },
            "guacamoleClient": {
                "backendConfig": {
                    "enabled": True,
                    "name": "guacamole-client",
                    "securityPolicyName": edge_policy_name,
                }
            },
        },
        "networkPolicy": {
            "enabled": True,
            "gclbSourceRanges": [
                "35.191.0.0/16",  # NOSONAR - Google Cloud Load Balancer health check/proxy range.
                "130.211.0.0/22",  # NOSONAR - Google Cloud Load Balancer health check/proxy range.
            ],
            "googleApiCidrs": [
                "199.36.153.4/30",  # NOSONAR - restricted.googleapis.com VIP range.
                "199.36.153.8/30",  # NOSONAR - private.googleapis.com VIP range.
            ],
            "privateServiceCidrs": private_service_cidrs,
            "rangeClusterApiCidrs": range_cluster_api_cidrs,
            "rangeClusterApiPort": int(range_cluster_port or _GDC_APISERVER_BACKEND_PORT),
        },
    }


def fetch_gcp_secret_payload(secret_id: str, project_id: str) -> str:
    """Return the latest Secret Manager payload for the given secret resource/name."""
    secret_name = secret_id.rstrip("/").split("/")[-1]
    result = subprocess.run(  # nosec B603 B607
        [
            "gcloud",
            "secrets",
            "versions",
            "access",
            "latest",
            "--secret",
            secret_name,
            "--project",
            project_id,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip() if result.stderr else _UNKNOWN_ERROR
        raise RuntimeError(f"Failed to read Secret Manager payload for {secret_name}: {stderr}")
    return result.stdout


def get_latest_gcp_secret_payload(secret_id: str, project_id: str) -> str | None:
    """Return the latest secret payload when one exists, otherwise None."""
    try:
        return fetch_gcp_secret_payload(secret_id, project_id)
    except RuntimeError as exc:
        message = str(exc).lower()
        if "not found" in message or "has no versions" in message:
            return None
        raise


def _is_retryable_gcp_terraform_init_error(message: str) -> bool:
    """Return True when terraform init failed due to bootstrap key or bucket IAM propagation."""
    normalized_message = message.lower()
    return "invalid jwt signature" in normalized_message or (
        "failed to get existing workspaces" in normalized_message
        and "403" in normalized_message
        and (
            "storage.objects.list" in normalized_message
            or "access to the google cloud storage bucket" in normalized_message
        )
    )


def _is_retryable_gcp_terraform_apply_error(message: str) -> bool:
    """Return True when terraform apply failed due to temporary bootstrap auth propagation."""
    normalized_message = message.lower()
    return "invalid jwt signature" in normalized_message or (
        "permission denied" in normalized_message
        or ("permission '" in normalized_message and " denied" in normalized_message)
        or " denied on resource " in normalized_message
        or "iam_permission_denied" in normalized_message
        or "does not have" in normalized_message
        or "error 403" in normalized_message
    )


def run_gcp_terraform_init_with_retry(
    config: GDCBootstrapConfig,
    tf_state_bucket: str,
    credentials_path: Path,
    *,
    max_attempts: int = 12,
    sleep_seconds: int = 5,
) -> None:
    """Run terraform init and retry only documented GCS backend IAM propagation failures."""
    init_cmd = [
        "terraform",
        "init",
        "-reconfigure",
        f"-backend-config=bucket={tf_state_bucket}",
        f"-backend-config=prefix=shifter/{config.environment}/platform-core",
        f"-backend-config=credentials={credentials_path}",
    ]
    info(f"Running: {' '.join(init_cmd)}")

    for attempt in range(1, max_attempts + 1):
        result = subprocess.run(init_cmd, capture_output=True, text=True, check=False)  # nosec B603 B607
        if result.stdout:
            print(result.stdout, end="")
        if result.stderr:
            print(result.stderr, end="", file=sys.stderr)
        if result.returncode == 0:
            return

        combined_output = "\n".join(part for part in (result.stdout, result.stderr) if part).strip()
        if _is_retryable_gcp_terraform_init_error(combined_output) and attempt < max_attempts:
            warn(
                "Terraform bootstrap credentials are still propagating; "
                f"retrying terraform init in {sleep_seconds}s ({attempt}/{max_attempts})"
            )
            time.sleep(sleep_seconds)
            continue

        error(f"Command failed: Command '{' '.join(init_cmd)}' returned non-zero exit status {result.returncode}.")
        sys.exit(1)


def run_gcp_terraform_apply_with_retry(
    config: GDCBootstrapConfig, *, max_attempts: int = 24, sleep_seconds: int = 5
) -> None:
    """Run terraform apply and retry only temporary bootstrap-auth propagation failures."""
    apply_cmd = ["terraform", "apply", "-auto-approve", f"-var=project_id={config.project_id}"]
    info(f"Running: {' '.join(apply_cmd)}")

    for attempt in range(1, max_attempts + 1):
        result = subprocess.run(apply_cmd, capture_output=True, text=True, check=False)  # nosec B603 B607
        if result.stdout:
            print(result.stdout, end="")
        if result.stderr:
            print(result.stderr, end="", file=sys.stderr)
        if result.returncode == 0:
            return

        combined_output = "\n".join(part for part in (result.stdout, result.stderr) if part).strip()
        if _is_retryable_gcp_terraform_apply_error(combined_output) and attempt < max_attempts:
            warn(
                "Terraform apply hit a temporary bootstrap-auth propagation error; "
                f"retrying in {sleep_seconds}s ({attempt}/{max_attempts})"
            )
            time.sleep(sleep_seconds)
            continue

        error(f"Command failed: Command '{' '.join(apply_cmd)}' returned non-zero exit status {result.returncode}.")
        sys.exit(1)


def _run_gcp_bootstrap_probe(cmd: list[str], credentials_path: Path) -> subprocess.CompletedProcess[str]:
    """Run a gcloud probe using the temporary bootstrap credential file."""
    env = os.environ.copy()
    env["CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE"] = str(credentials_path)
    env["GOOGLE_APPLICATION_CREDENTIALS"] = str(credentials_path)
    return subprocess.run(  # nosec B603 B607
        cmd,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def wait_for_gcp_terraform_bootstrap_access(
    config: GDCBootstrapConfig,
    credentials_path: Path,
    *,
    max_attempts: int = 24,
    sleep_seconds: int = 5,
) -> None:
    """Wait until the bootstrap credentials can read the project resources Terraform manages."""
    probe_cmds = [
        [
            "gcloud",
            "storage",
            "buckets",
            "describe",
            f"gs://{config.terraform_state_bucket_name}",
            "--project",
            config.project_id,
        ],
        [
            "gcloud",
            "storage",
            "buckets",
            "list",
            "--project",
            config.project_id,
        ],
        [
            "gcloud",
            "artifacts",
            "repositories",
            "list",
            "--location",
            config.region,
            "--project",
            config.project_id,
        ],
    ]

    for attempt in range(1, max_attempts + 1):
        failures: list[str] = []
        for probe_cmd in probe_cmds:
            result = _run_gcp_bootstrap_probe(probe_cmd, credentials_path)
            if result.returncode == 0:
                continue
            failures.append("\n".join(part for part in (result.stdout, result.stderr) if part).strip())

        if not failures:
            return

        combined_output = "\n".join(failures).strip()
        if _is_retryable_gcp_terraform_apply_error(combined_output) and attempt < max_attempts:
            warn(
                "Terraform bootstrap credentials are not usable yet; "
                f"retrying readiness probes in {sleep_seconds}s ({attempt}/{max_attempts})"
            )
            time.sleep(sleep_seconds)
            continue

        error("Bootstrap credentials never became usable for Terraform-managed GCP resources.")
        if combined_output:
            print(combined_output, file=sys.stderr)
        sys.exit(1)


def prune_stale_gcp_terraform_bootstrap_keys(config: GDCBootstrapConfig) -> None:
    """Delete any leftover user-managed keys on the bootstrap service account before minting a fresh one."""
    result = subprocess.run(  # nosec B603 B607
        [
            "gcloud",
            "iam",
            "service-accounts",
            "keys",
            "list",
            "--iam-account",
            config.terraform_bootstrap_service_account_email,
            "--project",
            config.project_id,
            "--managed-by=user",
            "--format=value(name.basename())",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip() if result.stderr else _UNKNOWN_ERROR
        raise RuntimeError(f"Failed to list Terraform bootstrap service-account keys: {stderr}")

    key_ids = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    for key_id in key_ids:
        run_cmd(
            [
                "gcloud",
                "iam",
                "service-accounts",
                "keys",
                "delete",
                key_id,
                "--iam-account",
                config.terraform_bootstrap_service_account_email,
                "--project",
                config.project_id,
                "--quiet",
            ],
            check=False,
        )


def _ensure_terraform_bootstrap_service_account(config: GDCBootstrapConfig) -> None:
    """Create the Terraform-bootstrap service account if it does not already exist."""
    if not gcloud_resource_exists(
        [
            "gcloud",
            "iam",
            "service-accounts",
            "describe",
            config.terraform_bootstrap_service_account_email,
            "--project",
            config.project_id,
        ]
    ):
        run_cmd(
            [
                "gcloud",
                "iam",
                "service-accounts",
                "create",
                config.terraform_bootstrap_service_account_name,
                "--project",
                config.project_id,
            ],
            check=False,
        )


def _grant_terraform_bootstrap_iam(config: GDCBootstrapConfig, member: str, bucket_url: str) -> None:
    """Grant the bootstrap service account the project roles and state-bucket binding."""
    for role in GCP_TERRAFORM_BOOTSTRAP_ROLES:
        add_project_iam_binding_with_retry(config.project_id, member, role)
    run_cmd(
        [
            "gcloud",
            "storage",
            "buckets",
            "add-iam-policy-binding",
            bucket_url,
            "--member",
            member,
            "--role",
            GCP_TERRAFORM_BOOTSTRAP_BUCKET_ROLE,
        ]
    )


def _revoke_terraform_bootstrap_iam(
    config: GDCBootstrapConfig,
    member: str,
    bucket_url: str,
    key_id: str,
    previous_env: dict[str, str | None],
) -> None:
    """Restore env vars and revoke the bootstrap key, project roles, and bucket binding."""
    for key, value in previous_env.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value

    if key_id:
        run_cmd(
            [
                "gcloud",
                "iam",
                "service-accounts",
                "keys",
                "delete",
                key_id,
                "--iam-account",
                config.terraform_bootstrap_service_account_email,
                "--project",
                config.project_id,
                "--quiet",
            ],
            check=False,
        )

    for role in GCP_TERRAFORM_BOOTSTRAP_ROLES:
        run_cmd(
            [
                "gcloud",
                "projects",
                "remove-iam-policy-binding",
                config.project_id,
                "--member",
                member,
                "--role",
                role,
                "--no-user-output-enabled",
            ],
            check=False,
        )
    run_cmd(
        [
            "gcloud",
            "storage",
            "buckets",
            "remove-iam-policy-binding",
            bucket_url,
            "--member",
            member,
            "--role",
            GCP_TERRAFORM_BOOTSTRAP_BUCKET_ROLE,
        ],
        check=False,
    )


@contextmanager
def gcp_terraform_bootstrap_credentials(config: GDCBootstrapConfig):
    """Provision temporary ADC-compatible credentials for Terraform bootstrap."""
    _ensure_terraform_bootstrap_service_account(config)

    member = f"serviceAccount:{config.terraform_bootstrap_service_account_email}"
    bucket_url = f"gs://{config.terraform_state_bucket_name}"
    _grant_terraform_bootstrap_iam(config, member, bucket_url)

    env_keys = ("GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_BACKEND_CREDENTIALS", "GOOGLE_CREDENTIALS")
    previous_env = {key: os.environ.get(key) for key in env_keys}
    key_id = ""

    with tempfile.TemporaryDirectory(prefix="shifter-gcp-tf-creds-") as temp_dir:
        credentials_path = Path(temp_dir) / "terraform-bootstrap.json"
        prune_stale_gcp_terraform_bootstrap_keys(config)
        run_cmd(
            [
                "gcloud",
                "iam",
                "service-accounts",
                "keys",
                "create",
                str(credentials_path),
                "--iam-account",
                config.terraform_bootstrap_service_account_email,
                "--project",
                config.project_id,
            ]
        )
        key_id = str(json.loads(credentials_path.read_text()).get("private_key_id", "")).strip()

        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(credentials_path)
        os.environ.pop("GOOGLE_BACKEND_CREDENTIALS", None)
        os.environ.pop("GOOGLE_CREDENTIALS", None)

        try:
            yield credentials_path
        finally:
            _revoke_terraform_bootstrap_iam(config, member, bucket_url, key_id, previous_env)


# Generated GCP range egress bridge tfvars (range_egress_mode +
# range_egress_allowed_cidrs). Rendered from shifter.yaml; gitignored.
RANGE_EGRESS_BRIDGE_FILENAME = "range_egress.auto.tfvars"


def resolve_shifter_config_path(config: GDCBootstrapConfig, repo_root: Path) -> Path:
    """Resolve the root installation config (shifter.yaml) that feeds the range egress render.

    Precedence: explicit ``--shifter-config`` (``config.shifter_config_path``), the
    ``SHIFTER_CONFIG`` environment variable, then the repo-root ``shifter.yaml``.

    A missing root config is a hard deploy failure: the deployed range firewall must be
    rendered from the single authoritative source, never silently defaulted to
    ``status-quo`` (the drift #1015 closes). Fails loud naming only the resolved path and
    the docs reference, never the config contents.
    """
    candidate = config.shifter_config_path or os.environ.get("SHIFTER_CONFIG")
    config_path = Path(candidate) if candidate else (repo_root / "shifter.yaml")
    if not config_path.exists():
        error(
            "Range egress render requires a root installation config (shifter.yaml). "
            f"Looked for: {config_path}. Provide --shifter-config or set SHIFTER_CONFIG. "
            "See docs/dev/deploy-secrets.md."
        )
        sys.exit(1)
    return config_path


def render_range_egress_tfvars(repo_root: Path, config_path: Path, output_path: Path, dry_run: bool = False) -> None:
    """Render the range egress bridge tfvars from ``config_path`` via ``shifter-config render``.

    Reuses the installation package's CLI (loader + RangeEgressPolicy + renderer) so the
    deployed firewall is generated from ``settings.range_egress`` rather than transcribed
    (ADR-017-R4). The config path and output path are passed as argv; the config contents
    are never read, echoed, or logged here.
    """
    run_cmd(
        [
            "uv",
            "run",
            "--project",
            str(repo_root / "shifter" / "installation"),
            "shifter-config",
            "render",
            str(config_path),
            "--output",
            str(output_path),
        ],
        dry_run=dry_run,
    )


def ensure_gcp_artifact_registry_service_identity(config: GDCBootstrapConfig, dry_run: bool = False) -> None:
    """Force-create the Artifact Registry service agent (P4SA) before Terraform.

    On a fresh project the AR service agent
    (``service-<num>@gcp-sa-artifactregistry.iam.gserviceaccount.com``) does not
    exist until its service identity is provisioned. The platform-core Terraform
    grants that agent the CMEK encrypter role on the Artifact Registry key ring,
    which 400s ("service account ... does not exist") on the first apply of a
    blank-slate project. Provisioning the identity here — idempotent on reruns —
    lets the control-plane apply succeed without operator intervention.

    Uses the Service Usage ``generateServiceIdentity`` REST endpoint (GA-accessible
    via the active credentials; the ``gcloud beta`` component is not required).
    """
    service = "artifactregistry.googleapis.com"
    if dry_run:
        info(f"[DRY-RUN] Would provision the {service} service identity (P4SA)")
        return

    # generateServiceIdentity requires the service enabled. Terraform's
    # project_services module also enables it (idempotent), but the identity must
    # exist before that module's CMEK IAM grant runs.
    run_cmd(["gcloud", "services", "enable", service, "--project", config.project_id])

    access_token = _gcp_identity_access_token()
    url = (
        "https://serviceusage.googleapis.com/v1beta1/"
        f"projects/{config.project_id}/services/{service}:generateServiceIdentity"
    )
    parsed_url = urllib_parse.urlparse(url)
    if parsed_url.scheme != "https" or parsed_url.netloc != "serviceusage.googleapis.com" or parsed_url.query:
        raise RuntimeError(f"Refusing to call unexpected Service Usage endpoint: {url}")
    request = urllib_request.Request(  # noqa: S310 - URL is validated immediately above
        url,
        data=b"",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "X-Goog-User-Project": config.project_id,
        },
        method="POST",
    )
    try:
        with urllib_request.urlopen(request, timeout=30) as response:  # nosec B310  # noqa: S310
            json.loads(response.read().decode("utf-8"))
        success(f"Ensured {service} service identity exists")
    except urllib_error.HTTPError as exc:  # pragma: no cover - exercised via unit tests with monkeypatch
        body = exc.read().decode("utf-8") if exc.fp is not None else ""
        try:
            parsed = json.loads(body) if body else {}
        except json.JSONDecodeError:
            parsed = {}
        message = parsed.get("error", {}).get("message", exc.reason)
        raise RuntimeError(f"Failed to provision {service} service identity: {message}") from exc


def apply_gcp_control_plane_terraform(
    config: GDCBootstrapConfig, dry_run: bool = False
) -> dict[str, dict[str, object]]:
    """Apply the GCP control-plane Terraform environment for the active project."""
    repo_root = get_repo_root()
    tf_dir = repo_root / "platform" / "terraform" / "gcp" / "environments" / config.environment
    if not tf_dir.exists():
        error(f"GCP Terraform directory not found: {tf_dir}")
        sys.exit(1)
    try:
        validate_gcp_control_plane_security_inputs(tf_dir)
    except ValueError as exc:
        error(str(exc))
        sys.exit(1)

    # Range egress single-source preflight (#1015): resolve and validate the root
    # config before any Terraform/state side effects so a missing config fails the
    # deploy up front instead of applying a stale/status-quo allowlist.
    shifter_config_path = resolve_shifter_config_path(config, repo_root)

    tf_state_bucket = config.terraform_state_bucket_name
    if not gcloud_resource_exists(
        ["gcloud", "storage", "buckets", "describe", f"gs://{tf_state_bucket}", "--project", config.project_id]
    ):
        run_cmd(
            [
                "gcloud",
                "storage",
                "buckets",
                "create",
                f"gs://{tf_state_bucket}",
                "--project",
                config.project_id,
                "--location",
                config.region,
                "--uniform-bucket-level-access",
            ],
            dry_run=dry_run,
        )

    run_cmd(
        ["gcloud", "storage", "buckets", "update", f"gs://{tf_state_bucket}", "--versioning"],
        dry_run=dry_run,
    )

    # Render the range egress bridge tfvars from shifter.yaml before Terraform consumes
    # the variables, so the deployed firewall matches settings.range_egress (#1015).
    render_range_egress_tfvars(repo_root, shifter_config_path, tf_dir / RANGE_EGRESS_BRIDGE_FILENAME, dry_run=dry_run)

    # Provision the Artifact Registry service agent before Terraform grants it the
    # CMEK encrypter role; on a fresh project the agent does not exist yet and the
    # grant would 400 on first apply.
    ensure_gcp_artifact_registry_service_identity(config, dry_run=dry_run)

    original_dir = os.getcwd()
    os.chdir(tf_dir)
    try:
        if dry_run:
            run_cmd(
                [
                    "terraform",
                    "init",
                    "-reconfigure",
                    f"-backend-config=bucket={tf_state_bucket}",
                    f"-backend-config=prefix=shifter/{config.environment}/platform-core",
                ],
                dry_run=dry_run,
            )
            run_cmd(
                [
                    "terraform",
                    "apply",
                    "-auto-approve",
                    f"-var=project_id={config.project_id}",
                ],
                dry_run=dry_run,
            )
            return {}

        with gcp_terraform_bootstrap_credentials(config) as credentials_path:
            run_gcp_terraform_init_with_retry(config, tf_state_bucket, credentials_path)
            wait_for_gcp_terraform_bootstrap_access(config, credentials_path)
            run_gcp_terraform_apply_with_retry(config)

            output_result = subprocess.run(  # nosec B603 B607
                ["terraform", "output", "-json"],
                capture_output=True,
                text=True,
                check=False,
            )
            if output_result.returncode != 0:
                stderr = output_result.stderr.strip() if output_result.stderr else _UNKNOWN_ERROR
                raise RuntimeError(f"Failed to capture Terraform outputs: {stderr}")
            return json.loads(output_result.stdout)
    finally:
        os.chdir(original_dir)


def stage_gcp_control_plane_values(
    config: GDCBootstrapConfig,
    outputs: dict[str, dict[str, object]],
    staging_root: Path,
    *,
    image_tag: str,
    bootstrap_operator_email: str | None = None,
) -> Path:
    """Stage the generated Helm values file for the Shifter release."""
    runtime_secret_ids = _get_output_value(outputs, "runtime_secret_ids")
    guacamole_db_payload = json.loads(fetch_gcp_secret_payload(runtime_secret_ids["guacamole-db"], config.project_id))
    guacamole_json_secret = fetch_gcp_secret_payload(
        runtime_secret_ids["guacamole-json-auth"],
        config.project_id,
    ).strip()
    values = render_gcp_helm_values(
        config,
        outputs,
        guacamole_db_payload=guacamole_db_payload,
        guacamole_json_secret=guacamole_json_secret,
        image_tag=image_tag,
        bootstrap_operator_email=bootstrap_operator_email,
    )
    values_path = staging_root / "shifter.values.generated.json"
    values_path.write_text(json.dumps(values, indent=2, sort_keys=True))
    return values_path


def push_gcp_control_plane_images(
    outputs: dict[str, dict[str, object]],
    *,
    image_tag: str,
    dry_run: bool = False,
) -> None:
    """Build and push the control-plane images to Artifact Registry."""
    pinned_image_tag = validate_image_tag(image_tag)
    image_roots = _get_output_value(outputs, "artifact_registry_image_roots")
    artifact_registry_host = str(image_roots["portal"]).split("/")[0]
    repo_root = get_repo_root()

    run_cmd(["gcloud", "auth", "configure-docker", artifact_registry_host, "--quiet"], dry_run=dry_run)

    image_builds = [
        (
            f"{image_roots['portal']}:{pinned_image_tag}",
            repo_root / "shifter",
            repo_root / "shifter" / "shifter_platform" / "Dockerfile",
        ),
        (
            # Context is the shifter/ root: the Dockerfile COPYs engine/provisioner/
            # and the sibling cyberscript/ lib (issue #1103), both relative to shifter/.
            f"{image_roots['pulumi-provisioner']}:{pinned_image_tag}",
            repo_root / "shifter",
            repo_root / "shifter" / "engine" / "provisioner" / "Dockerfile",
        ),
        (
            f"{image_roots['guacd']}:{pinned_image_tag}",
            repo_root / "shifter" / "engine" / "guacd",
            repo_root / "shifter" / "engine" / "guacd" / "Dockerfile",
        ),
        (
            f"{image_roots['guacamole-client']}:{pinned_image_tag}",
            repo_root / "shifter" / "engine" / "guacamole",
            repo_root / "shifter" / "engine" / "guacamole" / "Dockerfile",
        ),
    ]

    for tag, context_dir, dockerfile in image_builds:
        run_cmd(
            ["docker", "build", "-f", str(dockerfile), "-t", tag, str(context_dir)],
            dry_run=dry_run,
        )
        run_cmd(["docker", "push", tag], dry_run=dry_run)


def install_gke_gcloud_auth_plugin_user_space(dry_run: bool = False) -> None:
    """Install the GKE kubectl auth plugin into ~/.local/bin without root privileges."""
    if dry_run:
        info("Would install gke-gcloud-auth-plugin into ~/.local/bin via apt package extraction")
        return

    if not shutil.which("apt") or not shutil.which("dpkg-deb"):
        error(
            "gke-gcloud-auth-plugin is required for kubectl access to GKE and is not installed. "
            "User-space install requires both apt and dpkg-deb."
        )
        sys.exit(1)

    with tempfile.TemporaryDirectory(prefix="gke-auth-plugin-") as temp_dir:
        temp_path = Path(temp_dir)
        subprocess.run(  # nosec B603 B607
            ["apt", "download", "google-cloud-cli-gke-gcloud-auth-plugin"],
            cwd=temp_dir,
            check=True,
            capture_output=True,
            text=True,
        )
        deb_packages = sorted(temp_path.glob("google-cloud-cli-gke-gcloud-auth-plugin_*.deb"))
        if not deb_packages:
            error("Unable to locate downloaded google-cloud-cli-gke-gcloud-auth-plugin package.")
            sys.exit(1)

        extract_dir = temp_path / "extract"
        subprocess.run(  # nosec B603 B607
            ["dpkg-deb", "-x", str(deb_packages[0]), str(extract_dir)],
            check=True,
            capture_output=True,
            text=True,
        )
        source_binary = extract_dir / "usr" / "lib" / "google-cloud-sdk" / "bin" / "gke-gcloud-auth-plugin"
        if not source_binary.exists():
            error("Downloaded gke-gcloud-auth-plugin package did not contain the expected binary.")
            sys.exit(1)

        destination_dir = Path.home() / ".local" / "bin"
        destination_dir.mkdir(parents=True, exist_ok=True)
        destination_binary = destination_dir / "gke-gcloud-auth-plugin"
        shutil.copy2(source_binary, destination_binary)
        destination_binary.chmod(0o755)


def _gcloud_components_install_gke_plugin(dry_run: bool = False) -> bool:
    """Install the GKE auth plugin via the gcloud component manager.

    This is the portable method for SDK/tarball gcloud installs, where the
    ``google-cloud-cli`` apt repo (which the apt-based paths require) is typically
    not configured. Returns True on success, False when the component manager is
    unavailable or disabled (e.g. apt/snap-managed gcloud), so the caller falls
    back to apt.
    """
    if not shutil.which("gcloud"):
        return False
    if dry_run:
        info("[DRY-RUN] Would run: gcloud components install gke-gcloud-auth-plugin --quiet")
        return True
    result = subprocess.run(  # nosec B603 B607
        ["gcloud", "components", "install", "gke-gcloud-auth-plugin", "--quiet"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        return True
    # Component manager disabled (apt/snap-managed gcloud) or another failure;
    # surface the reason and let the caller fall back to apt.
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    return False


def ensure_gke_gcloud_auth_plugin(dry_run: bool = False) -> None:
    """Ensure the kubectl GKE auth plugin is present on the bootstrap host."""
    if shutil.which("gke-gcloud-auth-plugin"):
        return

    warn("Installing gke-gcloud-auth-plugin for kubectl access to GKE")

    # Prefer the gcloud component manager: it works for SDK/tarball gcloud
    # installs, where the google-cloud-cli apt repo that the apt paths below
    # require is typically not configured (the cause of a first-run failure).
    if not _gcloud_components_install_gke_plugin(dry_run=dry_run):
        if shutil.which("apt-get"):
            if os.geteuid() == 0:
                command_prefix: list[str] = []
            elif shutil.which("sudo"):
                command_prefix = ["sudo"]
            else:
                command_prefix = None
            if command_prefix is not None:
                run_cmd([*command_prefix, "apt-get", "update"], dry_run=dry_run)
                run_cmd(
                    [*command_prefix, "apt-get", "install", "-y", "google-cloud-cli-gke-gcloud-auth-plugin"],
                    dry_run=dry_run,
                )
            else:
                install_gke_gcloud_auth_plugin_user_space(dry_run=dry_run)
        else:
            error(
                "gke-gcloud-auth-plugin is required for kubectl access to GKE and is not installed. "
                "Automatic installation requires the gcloud component manager or apt-based package tooling."
            )
            sys.exit(1)

    if dry_run:
        return

    if not shutil.which("gke-gcloud-auth-plugin"):
        error("gke-gcloud-auth-plugin install completed but the binary is still unavailable on PATH.")
        sys.exit(1)


def helm_release_exists(release_name: str, namespace: str) -> bool:
    """Return True when the named Helm release already exists in the target namespace."""
    result = subprocess.run(  # nosec B603 B607
        ["helm", "status", release_name, "--namespace", namespace],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


def _kubectl_get_resource_names(get_args: list[str], namespace: str, description: str) -> list[str] | None:
    """Run `kubectl get` and return resource names, or None if the namespace does not exist."""
    result = subprocess.run(  # nosec B603 B607
        ["kubectl", "-n", namespace, "get", *get_args, "-o", "name", "--ignore-not-found"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip().lower() if result.stderr else ""
        if f'namespaces "{namespace}" not found' in stderr:
            return None
        raise RuntimeError(f"Failed to inspect {description} resources in namespace {namespace}: {result.stderr}")
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def list_gcp_helm_cutover_resources(namespace: str) -> list[str]:
    """List legacy Shifter resources in a namespace that must be purged before Helm takes ownership."""
    labeled = _kubectl_get_resource_names(
        [
            "deploy,svc,sa,cm,secret,ingress,rs,pod,job,cronjob,sts,ds",
            "-l",
            "app.kubernetes.io/part-of=shifter",
        ],
        namespace,
        "legacy Helm-cutover",
    )
    if labeled is None:
        return []

    explicit_resource_names = {
        "shifter-platform": ["configmap/platform-runtime", "secret/guacamole-runtime"],
        "shifter-jobs": ["serviceaccount/provisioner"],
    }
    explicit_resources = explicit_resource_names.get(namespace, [])
    named: list[str] = []
    if explicit_resources:
        named_result = _kubectl_get_resource_names(explicit_resources, namespace, "explicit Helm-cutover")
        if named_result is None:
            return []
        named = named_result

    return sorted(set(labeled + named))


def prepare_gcp_helm_cutover(dry_run: bool = False) -> None:
    """Delete legacy unmanaged Shifter resources before the first Helm-managed install."""
    release_name = "shifter"
    release_namespace = "shifter-system"
    managed_namespaces = ["shifter-system", "shifter-platform", "shifter-jobs"]

    if helm_release_exists(release_name, release_namespace):
        return

    found_resources = {namespace: list_gcp_helm_cutover_resources(namespace) for namespace in managed_namespaces}
    if not any(found_resources.values()):
        return

    warn("No Helm release exists yet. Deleting legacy Shifter resources before Helm cutover.")
    for namespace, resources_to_delete in found_resources.items():
        if not resources_to_delete:
            continue
        run_cmd(
            [
                "kubectl",
                "-n",
                namespace,
                "delete",
                *resources_to_delete,
                "--ignore-not-found=true",
                "--wait=true",
                "--timeout=10m",
            ],
            dry_run=dry_run,
        )


def _get_kubernetes_namespace(name: str) -> dict[str, object] | None:
    """Return namespace JSON or None when the namespace does not exist."""
    result = subprocess.run(  # nosec B603 B607
        ["kubectl", "get", "namespace", name, "-o", "json"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        return json.loads(result.stdout)

    stderr = result.stderr.strip().lower() if result.stderr else ""
    if f'namespaces "{name}" not found' in stderr:
        return None

    raise RuntimeError(f"Failed to inspect namespace {name}: {result.stderr}")


def _wait_for_namespace_absent(name: str, timeout_seconds: int = 300, poll_seconds: int = 2) -> None:
    """Wait until a namespace no longer exists."""
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        namespace = _get_kubernetes_namespace(name)
        if namespace is None:
            return
        time.sleep(poll_seconds)

    raise RuntimeError(f"Namespace {name} is still terminating after {timeout_seconds} seconds")


def _wait_for_namespace_active(name: str, timeout_seconds: int = 120, poll_seconds: int = 2) -> None:
    """Wait until a namespace exists and is Active."""
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        namespace = _get_kubernetes_namespace(name)
        if namespace and namespace.get("status", {}).get("phase") == "Active":
            return
        time.sleep(poll_seconds)

    raise RuntimeError(f"Namespace {name} did not become Active within {timeout_seconds} seconds")


def ensure_gcp_control_plane_namespaces(dry_run: bool = False) -> None:
    """Ensure Helm target namespaces exist outside of the release lifecycle."""
    namespace_specs = {
        "shifter-platform": {
            "app.kubernetes.io/part-of": "shifter",
            "shifter.dev/plane": "control",
            "pod-security.kubernetes.io/enforce": "restricted",
            "pod-security.kubernetes.io/audit": "restricted",
            "pod-security.kubernetes.io/warn": "restricted",
        },
        "shifter-jobs": {
            "app.kubernetes.io/part-of": "shifter",
            "shifter.dev/plane": "jobs",
            "pod-security.kubernetes.io/enforce": "restricted",
            "pod-security.kubernetes.io/audit": "restricted",
            "pod-security.kubernetes.io/warn": "restricted",
        },
    }

    for namespace_name, labels in namespace_specs.items():
        namespace = _get_kubernetes_namespace(namespace_name)
        if namespace and namespace.get("metadata", {}).get("deletionTimestamp"):
            warn(f"Namespace {namespace_name} is terminating; waiting for deletion before recreating it")
            if not dry_run:
                _wait_for_namespace_absent(namespace_name)

        manifest = {
            "apiVersion": "v1",
            "kind": "Namespace",
            "metadata": {
                "name": namespace_name,
                "labels": labels,
            },
        }

        if dry_run:
            info(f"Would apply namespace manifest for {namespace_name}")
            continue

        subprocess.run(  # nosec B603 B607
            ["kubectl", "apply", "-f", "-"],
            input=json.dumps(manifest),
            text=True,
            check=True,
            capture_output=True,
        )
        _wait_for_namespace_active(namespace_name)


def deploy_gcp_control_plane_with_helm(
    config: GDCBootstrapConfig,
    outputs: dict[str, dict[str, object]],
    values_path: Path,
    dry_run: bool = False,
) -> None:
    """Deploy Shifter onto GKE via Helm and wait for a healthy release."""
    cluster_name = str(_get_output_value(outputs, "gke_cluster_name"))
    cluster_location = str(_get_output_value(outputs, "gke_cluster_location"))
    chart_path = get_repo_root() / "platform" / "charts" / "shifter"
    environment_values_path = chart_path / f"values-{config.environment}.yaml"

    if not environment_values_path.exists():
        error(f"Missing Helm values override for environment {config.environment}: {environment_values_path}")
        sys.exit(1)

    ensure_gke_gcloud_auth_plugin(dry_run=dry_run)

    run_cmd(
        [
            "gcloud",
            "container",
            "clusters",
            "get-credentials",
            cluster_name,
            "--location",
            cluster_location,
            "--project",
            config.project_id,
        ],
        dry_run=dry_run,
    )
    prepare_gcp_helm_cutover(dry_run=dry_run)
    ensure_gcp_control_plane_namespaces(dry_run=dry_run)
    run_cmd(
        [
            "helm",
            "upgrade",
            "--install",
            "shifter",
            str(chart_path),
            "--namespace",
            "shifter-system",
            "--create-namespace",
            "--values",
            str(environment_values_path),
            "--values",
            str(values_path),
            "--atomic",
            "--wait",
            "--timeout",
            "15m",
            "--history-max",
            "10",
        ],
        dry_run=dry_run,
    )


def get_gcp_managed_certificate_status(
    certificate_name: str = "platform-managed-cert",
    namespace: str = "shifter-platform",
) -> str:
    """Return the current managed certificate status from the cluster."""
    result = subprocess.run(  # nosec B603 B607
        [
            "kubectl",
            "-n",
            namespace,
            "get",
            "managedcertificate",
            certificate_name,
            "-o",
            "json",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip() if result.stderr else _UNKNOWN_ERROR
        raise RuntimeError(f"Failed to inspect managed certificate {certificate_name}: {stderr}")

    payload = json.loads(result.stdout)
    return str(payload.get("status", {}).get("certificateStatus", "")).strip()


def wait_for_gcp_managed_certificate_active(
    timeout_seconds: int = 1800,
    poll_seconds: int = 10,
) -> None:
    """Wait until the GKE managed certificate reports Active."""
    deadline = time.time() + timeout_seconds
    last_status = ""
    while time.time() < deadline:
        status = get_gcp_managed_certificate_status()
        last_status = status or "UNKNOWN"
        normalized_status = last_status.lower()
        if normalized_status == "active":
            success("GKE managed certificate is active")
            return
        if normalized_status.startswith("failed"):
            raise RuntimeError(f"GKE managed certificate entered terminal status: {last_status}")
        info(f"Waiting for GKE managed certificate to become Active (current status: {last_status})")
        time.sleep(poll_seconds)

    raise RuntimeError(
        f"GKE managed certificate did not become Active within {timeout_seconds} seconds "
        f"(last status: {last_status or 'UNKNOWN'})"
    )


def verify_gcp_public_portal(hostname: str) -> None:
    """Verify the public Shifter endpoints are reachable over HTTPS."""
    health_result = subprocess.run(  # nosec B603 B607
        ["curl", "-sS", "-o", "/dev/null", "-w", "%{http_code}", f"https://{hostname}/health/"],
        capture_output=True,
        text=True,
        check=False,
    )
    health_code = health_result.stdout.strip()
    if health_result.returncode != 0 or health_code != "200":
        raise RuntimeError(
            f"Portal health check failed for https://{hostname}/health/ "
            f"(exit={health_result.returncode}, code={health_code or 'n/a'})"
        )

    mission_control_result = subprocess.run(  # nosec B603 B607
        ["curl", "-sS", "-o", "/dev/null", "-w", "%{http_code}", f"https://{hostname}/mission-control/"],
        capture_output=True,
        text=True,
        check=False,
    )
    mission_control_code = mission_control_result.stdout.strip()
    if mission_control_result.returncode != 0 or mission_control_code not in {"200", "301", "302", "303", "307", "308"}:
        raise RuntimeError(
            f"Mission Control endpoint failed for https://{hostname}/mission-control/ "
            f"(exit={mission_control_result.returncode}, code={mission_control_code or 'n/a'})"
        )

    success(f"Verified public portal over HTTPS at https://{hostname}/")


def walkthrough_gcp_dns_setup_and_wait_for_tls(
    outputs: dict[str, dict[str, object]],
    dry_run: bool = False,
) -> None:
    """Guide the operator through DNS cutover and wait for the managed certificate to become active."""
    header("Point Domain to GCP Load Balancer")

    hostname = str(_get_output_value(outputs, "public_hostname")).strip()
    ingress_ip = str(_get_output_value(outputs, "public_ingress_ip_address")).strip()

    print("The GCP ingress and global IP now exist.\n")
    subheader("Create or update this DNS record")
    print(f"  {Colors.BOLD}Type:{Colors.END}  A")
    print(f"  {Colors.BOLD}Name:{Colors.END}  {hostname}")
    print(f"  {Colors.BOLD}Value:{Colors.END} {ingress_ip}")
    print(
        f"\n{Colors.DIM}If the hostname is proxied through Cloudflare or another CDN, "
        f"disable proxying until the Google-managed certificate reports Active.{Colors.END}"
    )

    if dry_run:
        info(f"[DRY-RUN] Would wait for DNS to point {hostname} at {ingress_ip} and verify HTTPS")
        return

    wait_for_user(
        f"Update DNS so {hostname} points to {ingress_ip}.\n"
        "Once the record is live, bootstrap will wait for the managed certificate and verify the portal."
    )
    wait_for_gcp_managed_certificate_active()
    verify_gcp_public_portal(hostname)


def bootstrap_gcp_control_plane(config: GDCBootstrapConfig, dry_run: bool = False) -> dict[str, dict[str, object]]:
    """Bootstrap the GCP control-plane infrastructure and workloads for gcp-dev."""
    header(f"Deploying {config.environment} Shifter Platform")
    outputs = apply_gcp_control_plane_terraform(config, dry_run=dry_run)
    if dry_run:
        return outputs

    bootstrap_operator_email = ensure_gcp_identity_platform_operator(config, outputs, dry_run=dry_run)
    image_tag = resolve_gcp_control_plane_image_tag()
    push_gcp_control_plane_images(outputs, image_tag=image_tag, dry_run=dry_run)
    with tempfile.TemporaryDirectory(prefix="shifter-gcp-platform-") as staging_root_name:
        values_path = stage_gcp_control_plane_values(
            config,
            outputs,
            Path(staging_root_name),
            image_tag=image_tag,
            bootstrap_operator_email=bootstrap_operator_email,
        )
        deploy_gcp_control_plane_with_helm(config, outputs, values_path, dry_run=dry_run)
    walkthrough_gcp_dns_setup_and_wait_for_tls(outputs, dry_run=dry_run)
    success(f"{config.environment} Shifter platform deployed")
    return outputs


def gdc_bootstrap_cluster(config: GDCBootstrapConfig, dry_run: bool = False) -> dict[str, str]:
    """Bootstrap the repeatable GDC-on-Compute-Engine VM Runtime cluster."""
    if not config.project_id:
        error("GDC bootstrap requires a GCP project ID. Set PANW_GCP_DEV or pass --project-id.")
        sys.exit(1)

    header(f"Bootstrapping {config.cluster_id} GDC Cluster")

    info(f"GCP Project: {config.project_id}")
    info(f"Region / Zone: {config.region} / {config.zone}")
    info(f"Network: {config.resolved_network_name} ({config.subnet_cidr})")
    info(f"Service Account: {config.service_account_email}")
    info(f"VM Runtime VIPs: control-plane={config.control_plane_vip}, ingress={config.ingress_vip}")

    if not dry_run and not confirm("Create or reconcile these GDC bootstrap resources?"):
        warn("Aborted by user")
        sys.exit(0)

    ensure_gdc_apis(config, dry_run=dry_run)
    ensure_gdc_service_account(config, dry_run=dry_run)

    with tempfile.TemporaryDirectory(prefix="shifter-gdc-bootstrap-") as staging_dir_name:
        staged_assets = stage_gdc_bootstrap_assets(config, Path(staging_dir_name), dry_run=dry_run)
        ensure_gdc_network(config, dry_run=dry_run)
        ensure_gdc_instances(config, staged_assets["ssh_metadata"], dry_run=dry_run)
        sync_gdc_instance_ssh_metadata(config, staged_assets["ssh_metadata"], dry_run=dry_run)

        for host in config.all_hosts:
            wait_for_gdc_ssh(config, host, dry_run=dry_run)

        upload_gdc_assets(config, staged_assets["assets_dir"], dry_run=dry_run)
        run_gdc_workstation_script(config, "prepare-workstation.sh", dry_run=dry_run)
        run_gdc_workstation_script(config, "prepare-hosts.sh", dry_run=dry_run)
        run_gdc_workstation_script(config, "create-cluster.sh", dry_run=dry_run)
        run_gdc_workstation_script(config, "install-helper.sh", dry_run=dry_run)
        sync_gdc_access_secret(config, dry_run=dry_run)
        sync_gdc_vm_image_secret(config, staged_assets["service_account_key"], dry_run=dry_run)

    control_plane_outputs = bootstrap_gcp_control_plane(config, dry_run=dry_run)

    success("GDC bootstrap complete")
    print("\nNext commands:")
    ssh_command = (
        f"gcloud compute ssh root@{config.workstation.name} --tunnel-through-iap "
        f"--project {config.project_id} --zone {config.zone}"
    )
    code_block(
        f"""{ssh_command}
shifter-gdc-kubectl get nodes
shifter-gdc-kubeconfig"""
    )

    return {
        "project_id": config.project_id,
        "cluster_id": config.cluster_id,
        "region": config.region,
        "zone": config.zone,
        "network_name": config.resolved_network_name,
        "subnetwork_name": config.resolved_subnetwork_name,
        "workstation": config.workstation.name,
        "kubeconfig_path": config.kubeconfig_path,
        "gdc_access_secret_id": config.gdc_access_secret_id,
        "gdc_vm_image_gcs_secret_id": config.gdc_vm_image_gcs_secret_id,
        "gke_cluster_name": (
            str(_get_output_value(control_plane_outputs, "gke_cluster_name")) if control_plane_outputs else ""
        ),
    }


def _ensure_state_bucket(bucket_name: str, config: BootstrapConfig, profile: str, dry_run: bool) -> None:
    """Create the Terraform-state S3 bucket, or confirm reuse if it already exists."""
    if not dry_run and s3_bucket_exists(bucket_name, profile):
        warn(f"S3 bucket '{bucket_name}' already exists")
        if not confirm("Continue using existing bucket?"):
            error("Cannot continue without S3 bucket for Terraform state")
            sys.exit(1)
        info("Using existing bucket")
    else:
        create_s3_bucket(bucket_name, config.region, profile, dry_run)


def _run_iam_terraform(config: BootstrapConfig, bucket_name: str, account_id: str, profile: str, dry_run: bool) -> str:
    """Run the global/iam Terraform stack and return the production GitHub Actions role ARN."""
    repo_root = get_repo_root()
    iam_tf_dir = repo_root / "platform" / "terraform" / "global" / "iam"

    if not iam_tf_dir.exists():
        error(f"IAM Terraform directory not found: {iam_tf_dir}")
        sys.exit(1)

    # Write per-instance backend config outside the product repo.
    instance_dir = tb.instance_dir_from_env() or Path.home() / ".shifter" / f"{config.env}-{bucket_name}"
    backend_dir = tb.resolve_instance_backend_dir(
        env=config.env,
        bucket=bucket_name,
        instance_dir=instance_dir,
    )
    backend_config_file = tb.backend_config_for_stack(backend_dir, "global/iam", config.env)
    if not dry_run:
        tb.write_instance_backend_configs(
            backend_dir=backend_dir,
            env=config.env,
            bucket=bucket_name,
            region=config.region,
        )
        info("Using rendered instance backend config for global/iam")
        success(f"Backend config ready for {config.env}")
    else:
        info("[DRY-RUN] Would write instance backend config outside the product repo")

    original_dir = os.getcwd()
    os.chdir(iam_tf_dir)

    # Set AWS_PROFILE for Terraform (only affects this process and its children)
    os.environ["AWS_PROFILE"] = profile

    try:
        info("Running terraform init with instance backend config")
        run_cmd(
            ["terraform", "init", "-reconfigure", f"-backend-config={backend_config_file}"],
            dry_run=dry_run,
        )
        _terraform_apply_iam(config, dry_run)
        role_arn = _terraform_iam_role_arn(config, account_id, dry_run)
    finally:
        os.chdir(original_dir)

    return role_arn


def _terraform_apply_iam(config: BootstrapConfig, dry_run: bool) -> None:
    """Run terraform apply (or plan in dry-run) for the IAM stack; exit on apply failure."""
    info(f"Running terraform apply for {config.env}...")
    tfvars_file = f"{config.env}.tfvars"

    if dry_run:
        run_cmd(["terraform", "plan", f"-var-file={tfvars_file}"], dry_run=dry_run)
        return

    apply_result = run_cmd(
        ["terraform", "apply", "-auto-approve", f"-var-file={tfvars_file}"],
        dry_run=dry_run,
        check=False,
    )
    if apply_result and apply_result.returncode != 0:
        error("Terraform apply failed for IAM module")
        error("The bootstrap role is still active - you can retry manually")
        sys.exit(1)


def _terraform_iam_role_arn(config: BootstrapConfig, account_id: str, dry_run: bool) -> str:
    """Return the production GitHub Actions role ARN from terraform output (synthetic in dry-run)."""
    if dry_run:
        return f"arn:aws:iam::{account_id}:role/{config.role_name}"

    result = subprocess.run(  # nosec B603 B607
        ["terraform", "output", "-raw", "github_actions_role_arn"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        error("Failed to get role ARN from Terraform output")
        sys.exit(1)
    role_arn = result.stdout.strip()
    success("Production IAM role created")
    return role_arn


def bootstrap_account(config: BootstrapConfig, profile: str, dry_run: bool = False) -> dict:
    """Bootstrap AWS account with state backend and IAM role."""
    header(f"Bootstrapping {config.env.upper()} AWS Account")

    info(f"Using AWS Profile: {profile}")

    # Get account ID
    if not dry_run:
        account_id = get_aws_account_id(profile)
        info(f"AWS Account ID: {account_id}")
    else:
        account_id = "123456789012"
        info("[DRY-RUN] Would get AWS account ID")

    # Generate UUID for uniqueness
    uid = str(uuid.uuid4())
    bucket_name = f"{config.bucket_prefix}-{uid}"

    info(f"S3 Bucket: {bucket_name}")
    info("State locking: S3 native (use_lockfile = true) — no DynamoDB needed")
    info(f"IAM Role: {config.role_name}")

    if not dry_run and not confirm("Create these resources?"):
        warn("Aborted by user")
        sys.exit(0)

    # Step 1: S3 Bucket
    header("Step 1/3: Creating S3 Bucket")
    _ensure_state_bucket(bucket_name, config, profile, dry_run)
    success("S3 bucket ready")

    # Step 2: Bootstrap IAM Role (temporary - will be replaced by Terraform)
    header("Step 2/3: Creating Bootstrap IAM Role")

    # Construct OIDC ARN - the provider will be created by Terraform, but the ARN format is deterministic
    # Format: arn:aws:iam::<account_id>:oidc-provider/token.actions.githubusercontent.com
    oidc_arn = f"arn:aws:iam::{account_id}:oidc-provider/token.actions.githubusercontent.com"

    # OIDC Trust Policy for GitHub Actions
    # VERIFIED OFFICIAL VALUES (Brad Edwards, 2026-01-02):
    # - token.actions.githubusercontent.com:aud must be "sts.amazonaws.com"
    # - token.actions.githubusercontent.com:sub format: "repo:ORG/REPO:*"
    # Source: https://docs.github.com/en/actions/deployment/security-hardening-your-deployments/configuring-openid-connect-in-amazon-web-services
    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Federated": oidc_arn},
                "Action": "sts:AssumeRoleWithWebIdentity",
                "Condition": {
                    "StringEquals": {"token.actions.githubusercontent.com:aud": "sts.amazonaws.com"},
                    "StringLike": {
                        "token.actions.githubusercontent.com:sub": (f"repo:{config.github_org}/{config.github_repo}:*")
                    },
                },
            }
        ],
    }

    info(f"Creating temporary bootstrap role: {config.bootstrap_role_name}")
    info("This role will be deleted after Terraform creates the production role")

    run_cmd(
        [
            "aws",
            "iam",
            "create-role",
            "--role-name",
            config.bootstrap_role_name,
            "--assume-role-policy-document",
            json.dumps(trust_policy),
            "--tags",
            f"Key=Name,Value={config.bootstrap_role_name}",
            "Key=Project,Value=shifter",
            "Key=Purpose,Value=bootstrap-temporary",
        ],
        dry_run=dry_run,
        check=False,  # May already exist
        profile=profile,
    )

    # Add AdministratorAccess-equivalent permissions inline. The target AWS org
    # may deny iam:AttachRolePolicy via SCP even for admin operators.
    run_cmd(
        [
            "aws",
            "iam",
            "put-role-policy",
            "--role-name",
            config.bootstrap_role_name,
            "--policy-name",
            "bootstrap-administrator-access",
            "--policy-document",
            administrator_access_policy_document(),
        ],
        dry_run=dry_run,
        profile=profile,
    )

    success("Bootstrap IAM role created with AdministratorAccess")

    # Step 3: Run Terraform to create OIDC provider and production IAM role
    header("Step 3/3: Creating OIDC Provider and IAM Role via Terraform")

    info("Running Terraform to create properly scoped IAM policies...")
    info("The production role will be: " + config.role_name)
    role_arn = _run_iam_terraform(config, bucket_name, account_id, profile, dry_run)

    # Cleanup: Delete the bootstrap role
    header("Cleanup: Removing Bootstrap Role")

    info(f"Deleting temporary bootstrap role: {config.bootstrap_role_name}")

    # Delete the inline bootstrap policy first.
    run_cmd(
        [
            "aws",
            "iam",
            "delete-role-policy",
            "--role-name",
            config.bootstrap_role_name,
            "--policy-name",
            "bootstrap-administrator-access",
        ],
        dry_run=dry_run,
        check=False,
        profile=profile,
    )

    # Delete the role
    run_cmd(
        [
            "aws",
            "iam",
            "delete-role",
            "--role-name",
            config.bootstrap_role_name,
        ],
        dry_run=dry_run,
        check=False,
        profile=profile,
    )

    success("Bootstrap role deleted - using Terraform-managed role going forward")

    return {
        "bucket_name": bucket_name,
        "role_arn": role_arn,
        "region": config.region,
        "env": config.env,
        "secret_name": config.secret_name,
        "github_org": config.github_org,
        "github_repo": config.github_repo,
    }


def _infra_state_bucket(*, bucket_name: str | None = None) -> str:
    """Resolve the Terraform state bucket from args or TF_INFRA_STATE_BUCKET."""
    if bucket_name:
        return bucket_name
    from_env = os.environ.get("TF_INFRA_STATE_BUCKET", "").strip()
    if from_env:
        return from_env
    error("Set TF_INFRA_STATE_BUCKET or pass the bootstrap bucket name before running Terraform")
    raise SystemExit(1)


def _instance_root(*, env: str, bucket: str) -> Path:
    """Return the per-instance config root (explicit env dir or ~/.shifter/<env>-<bucket>)."""
    explicit = tb.instance_dir_from_env()
    if explicit is not None:
        return explicit
    return Path.home() / ".shifter" / f"{env}-{bucket}"


def _component_stack_dir(env: str, component: str) -> str:
    """Map a deploy component name to its path under platform/terraform/environments/."""
    if component == "core":
        return f"environments/{env}"
    return f"environments/{env}/{component}"


def walkthrough_backend_config(bootstrap_result: dict, dry_run: bool = False) -> None:
    """Write per-instance Terraform backend configs outside the product repo."""
    header("Write Instance Terraform Backend Configuration")

    bucket = bootstrap_result["bucket_name"]
    region = bootstrap_result["region"]
    env = bootstrap_result["env"]
    instance_dir = _instance_root(env=env, bucket=bucket)
    backend_dir = tb.resolve_instance_backend_dir(env=env, bucket=bucket, instance_dir=instance_dir)

    print("Backend configs are written outside the product repo so multiple instances")
    print("can share one checkout without committing state-bucket names.\n")
    print("Instance config is stored under your SHIFTER instance directory (~/.shifter/<env>-<bucket>/).\n")

    if dry_run:
        info("[DRY-RUN] Would write backend configs outside the product repo")
        bootstrap_result["instance_dir"] = str(instance_dir)
        bootstrap_result["backend_config_dir"] = str(backend_dir)
        return

    choice = confirm_or_manual("Write instance backend configuration files?")
    if choice == "yes":
        paths = tb.write_instance_backend_configs(
            backend_dir=backend_dir,
            env=env,
            bucket=bucket,
            region=region,
        )
        portal_vars = tb.write_portal_remote_state_tfvars(
            instance_dir=instance_dir,
            env=env,
            bucket=bucket,
            region=region,
        )
        success(f"Wrote {len(paths)} backend config file(s) to the instance directory")
        success("Wrote portal remote-state tfvars for portal stack")
        bootstrap_result["portal_remote_state_tfvars"] = str(portal_vars)
    elif choice == "manual":
        wait_for_user(
            "Create the backend config files manually under the instance directory shown above.\n"
            "Each stack needs bucket, key, region, encrypt, and use_lockfile entries."
        )
        success("Backend configuration ready")
        bootstrap_result["portal_remote_state_tfvars"] = str(
            instance_dir / "terraform-vars" / env / "portal" / "remote-state.auto.tfvars"
        )
    else:
        error("Backend configuration is required for Terraform state management")
        sys.exit(1)

    bootstrap_result["instance_dir"] = str(instance_dir)
    bootstrap_result["backend_config_dir"] = str(backend_dir)


def _gh_secret_set_or_exit(secret_name: str, secret_value: str, github_org: str, github_repo: str) -> None:
    """Set one GitHub Actions secret via gh CLI or exit on failure."""
    result = subprocess.run(  # nosec B603 B607
        [
            "gh",
            "secret",
            "set",
            secret_name,
            "--body",
            secret_value,
            "--repo",
            f"{github_org}/{github_repo}",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        error(f"Failed to set {secret_name}: {result.stderr}")
        sys.exit(1)


def _ensure_tf_infra_state_bucket_secret(bucket_name: str, github_org: str, github_repo: str) -> None:
    """Ensure TF_INFRA_STATE_BUCKET exists when reusing an existing role secret."""
    if github_secret_exists("TF_INFRA_STATE_BUCKET", github_org, github_repo):
        info("TF_INFRA_STATE_BUCKET already configured")
        return

    warn("TF_INFRA_STATE_BUCKET is not set (required for CI Terraform backend rendering)")
    choice = confirm_or_manual("Set TF_INFRA_STATE_BUCKET via gh CLI?")
    if choice == "yes":
        _gh_secret_set_or_exit("TF_INFRA_STATE_BUCKET", bucket_name, github_org, github_repo)
        success("TF_INFRA_STATE_BUCKET configured via gh CLI")
        return
    if choice == "no":
        error("TF_INFRA_STATE_BUCKET is required for GitHub Actions deploy workflows")
        sys.exit(1)

    print(f"\n{Colors.BOLD}Manual Steps:{Colors.END}")
    print(f"  1. Go to: https://github.com/{github_org}/{github_repo}/settings/secrets/actions")
    print("  2. Click 'New repository secret'")
    print("  3. Name: TF_INFRA_STATE_BUCKET")
    print("  4. Value: (same S3 state bucket created during bootstrap)")
    print("  5. Click 'Add secret'")
    wait_for_user("Add TF_INFRA_STATE_BUCKET, then press Enter to continue.")
    success("TF_INFRA_STATE_BUCKET configured")


def _configure_github_secrets_via_gh(
    secret_name: str, role_arn: str, bucket_name: str, github_org: str, github_repo: str
) -> bool:
    """Try to set the GitHub secrets via the gh CLI.

    Returns True if the secrets were fully handled (caller should return); False if
    the user chose the manual path and the caller should print the manual steps.
    """
    print(f"\n{Colors.GREEN}✓ GitHub CLI detected{Colors.END}")

    secret_exists = github_secret_exists(secret_name, github_org, github_repo)

    if secret_exists:
        warn(f"Secret '{secret_name}' already exists in {github_org}/{github_repo}")
        choice = confirm_or_manual("Overwrite existing secret?")
    else:
        choice = confirm_or_manual("Automatically set these secrets using gh CLI?")

    if choice == "yes":
        info(f"Running: gh secret set {secret_name} --repo {github_org}/{github_repo}")
        _gh_secret_set_or_exit(secret_name, role_arn, github_org, github_repo)
        _gh_secret_set_or_exit("TF_INFRA_STATE_BUCKET", bucket_name, github_org, github_repo)
        success("GitHub secrets configured via gh CLI")
        return True
    if choice == "no":
        if secret_exists:
            info("Keeping existing secret value")
            _ensure_tf_infra_state_bucket_secret(bucket_name, github_org, github_repo)
            return True
        error("GitHub secret is required for CI/CD to authenticate with AWS")
        error("Without this, GitHub Actions cannot deploy infrastructure")
        sys.exit(1)
    return False


def walkthrough_github_secrets(bootstrap_result: dict, dry_run: bool = False) -> None:
    """Walk user through setting GitHub secrets."""
    header("Configure GitHub Secrets")

    role_arn = bootstrap_result["role_arn"]
    secret_name = bootstrap_result["secret_name"]
    github_org = bootstrap_result["github_org"]
    github_repo = bootstrap_result["github_repo"]
    bucket_name = bootstrap_result["bucket_name"]

    # The role ARN is the value of the GitHub secret. Rather than echoing it to
    # the terminal (and the operator's scrollback), point the operator at the
    # authoritative Terraform output, so no role/secret value is written to the
    # deploy log (CodeQL py/clear-text-logging).
    role_arn_source = "run `terraform output -raw github_actions_role_arn` in platform/terraform/global/iam"

    print("CI/CD needs the IAM role ARN to authenticate with AWS.\n")

    subheader("GitHub Secret to Add")
    print(f"  {Colors.BOLD}Name:{Colors.END}  {secret_name}")
    print(f"  {Colors.BOLD}Value:{Colors.END} ({role_arn_source})")
    print(f"\n  {Colors.BOLD}Name:{Colors.END}  TF_INFRA_STATE_BUCKET")
    print(f"  {Colors.BOLD}Value:{Colors.END} (same S3 state bucket shown above)")

    if dry_run:
        return

    gh_available = subprocess.run(["which", "gh"], capture_output=True).returncode == 0  # nosec B603 B607

    if gh_available:
        if _configure_github_secrets_via_gh(secret_name, role_arn, bucket_name, github_org, github_repo):
            return
    else:
        warn("GitHub CLI (gh) not found - using manual method")

    print(f"\n{Colors.BOLD}Manual Steps:{Colors.END}")
    print(f"  1. Go to: https://github.com/{github_org}/{github_repo}/settings/secrets/actions")
    print("  2. Click 'New repository secret'")
    print(f"  3. Name: {secret_name}")
    print(f"  4. Value: {role_arn_source}")
    print("  5. Click 'Add secret'")
    print("  6. Add another secret named TF_INFRA_STATE_BUCKET with the state bucket value above")
    wait_for_user("Add the GitHub secrets, then press Enter to continue.")
    success("GitHub secrets configured")


_COMPONENT_REQUIREMENT_REASON = {
    "core": "Core creates ECR repositories needed for container images",
    "range": "Range VPC is required for isolated attack/defense environments",
    "portal": "Portal is the main application infrastructure",
}


def _capture_terraform_outputs() -> dict:
    """Return parsed `terraform output -json`, or empty dict on failure.

    Used by the post-apply portal step; isolated from the deploy loop so
    the loop body stays at a reasonable nesting depth.
    """
    cmd = ["terraform", "output", "-json"]
    result = subprocess.run(  # nosec B603 B607  # NOSONAR
        cmd,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return {}
    return json.loads(result.stdout)


def _terraform_init_or_exit(
    component: str,
    dry_run: bool,
    *,
    backend_config_path: Path,
) -> None:
    """Run `terraform init -reconfigure -backend-config=<path>`."""
    info(f"Running terraform init for {component}...")
    init_result = run_cmd(
        ["terraform", "init", "-reconfigure", f"-backend-config={backend_config_path}"],
        dry_run=dry_run,
    )
    if not dry_run and init_result and init_result.returncode != 0:
        error(f"Terraform init failed for {component}")
        error(f"Check that {backend_config_path} exists and has the real bucket name")
        raise SystemExit(1)


def _terraform_plan_or_exit(component: str, dry_run: bool, *, var_files: list[Path] | None = None) -> None:
    """Run `terraform plan -out=tfplan`."""
    info("Running terraform plan...")
    plan_cmd = ["terraform", "plan", "-out=tfplan"]
    for var_file in var_files or []:
        plan_cmd.extend(["-var-file", str(var_file)])
    plan_result = run_cmd(plan_cmd, dry_run=dry_run)
    if not dry_run and plan_result and plan_result.returncode != 0:
        error(f"Terraform plan failed for {component}")
        error("Review errors above and fix before continuing")
        sys.exit(1)


def _terraform_apply_or_exit(component: str) -> dict[str, Any]:
    """Show plan, confirm, apply, and capture outputs (for portal). Exits on failure."""
    print(f"\n{Colors.BOLD}Plan Summary:{Colors.END}")
    subprocess.run(["terraform", "show", "-no-color", "tfplan"], check=False)  # nosec B603 B607

    if not confirm("\nApply this plan?"):
        error(f"Terraform apply for {component} is required")
        error("All infrastructure components are mandatory for Shifter to function")
        sys.exit(1)

    info("Running terraform apply...")
    apply_result = run_cmd(["terraform", "apply", "tfplan"])
    if apply_result and apply_result.returncode != 0:
        error(f"Terraform apply failed for {component}")
        error("Infrastructure deployment incomplete")
        sys.exit(1)

    success(f"{component} deployed successfully")
    if component == "portal":
        return _capture_terraform_outputs()
    return {}


def _require_component_deploy(component: str, dry_run: bool) -> None:
    """Confirm a Terraform component deploy or exit when the operator declines."""
    if dry_run or confirm(f"Deploy {component}?"):
        return
    error(f"{component.title()} deployment is required")
    reason = _COMPONENT_REQUIREMENT_REASON.get(component)
    if reason:
        error(reason)
    raise SystemExit(1)


def _portal_remote_state_var_file(env: str, bucket: str) -> Path:
    """Return the portal remote-state tfvars path for one instance."""
    return _instance_root(env=env, bucket=bucket) / "terraform-vars" / env / "portal" / "remote-state.auto.tfvars"


def _ensure_portal_remote_state_var_file(env: str, bucket: str, portal_var_file: Path) -> None:
    """Create portal remote-state tfvars when missing before portal init/plan."""
    if portal_var_file.exists():
        return
    tb.write_portal_remote_state_tfvars(
        instance_dir=_instance_root(env=env, bucket=bucket),
        env=env,
        bucket=bucket,
        region=os.environ.get("AWS_REGION", "us-east-2"),
    )


def _deploy_terraform_component(
    env: str,
    component: str,
    dry_run: bool,
    *,
    bucket_name: str | None = None,
) -> dict[str, Any]:
    """Run init/plan/apply for one Terraform component; return any captured outputs."""
    _require_component_deploy(component, dry_run)

    bucket = _infra_state_bucket(bucket_name=bucket_name)
    instance_dir = _instance_root(env=env, bucket=bucket)
    backend_dir = tb.resolve_instance_backend_dir(env=env, bucket=bucket, instance_dir=instance_dir)
    backend_config_path = tb.backend_config_for_stack(
        backend_dir,
        _component_stack_dir(env, component),
        env,
    )
    portal_var_file = _portal_remote_state_var_file(env, bucket)
    if component == "portal":
        _ensure_portal_remote_state_var_file(env, bucket, portal_var_file)

    base_path = get_repo_root() / "platform" / "terraform" / "environments" / env
    tf_dir = base_path if component == "core" else base_path / component
    if not tf_dir.exists():
        error(f"Directory not found: {tf_dir}")
        return {}

    original_dir = os.getcwd()
    os.chdir(tf_dir)
    try:
        _terraform_init_or_exit(
            component,
            dry_run,
            backend_config_path=backend_config_path,
        )
        var_files = [portal_var_file] if component == "portal" and portal_var_file.exists() else None
        _terraform_plan_or_exit(component, dry_run, var_files=var_files)
        if dry_run:
            return {}
        return _terraform_apply_or_exit(component)
    finally:
        os.chdir(original_dir)


def terraform_deploy(
    env: str,
    profile: str,
    dry_run: bool = False,
    *,
    bucket_name: str | None = None,
) -> dict[str, Any]:
    """Deploy all Terraform components in order."""
    header(f"Deploying {env.upper()} Infrastructure")

    # Set AWS_PROFILE for Terraform (only affects this process and its children)
    os.environ["AWS_PROFILE"] = profile

    components = [
        ("core", "ECR repositories"),
        ("range", "Range VPC + Pulumi state"),
        ("portal", "Portal infrastructure (VPC, RDS, EC2, ALB, Cognito)"),
    ]

    outputs: dict[str, Any] = {}
    for i, (component, description) in enumerate(components, 1):
        header(f"Step {i}/{len(components)}: {description}")
        info(f"Component: {component}")
        captured = _deploy_terraform_component(env, component, dry_run, bucket_name=bucket_name)
        if captured:
            outputs = captured
    return outputs


def walkthrough_acm_validation(outputs: dict, dry_run: bool = False) -> None:
    """Walk user through ACM certificate validation."""
    header("ACM Certificate Validation")

    print("Your SSL certificate needs DNS validation before HTTPS will work.\n")

    if "acm_validation_records" in outputs:
        records = outputs["acm_validation_records"]["value"]

        subheader("Add these CNAME records to your DNS provider")

        print(f"{'Domain':<40} {'Record Name':<50}")
        print("-" * 90)

        for domain, record in records.items():
            print(f"\n{Colors.BOLD}Domain:{Colors.END} {domain}")
            print(f"  {Colors.BOLD}Type:{Colors.END}  CNAME")
            print(f"  {Colors.BOLD}Name:{Colors.END}  {record['name']}")
            print(f"  {Colors.BOLD}Value:{Colors.END} {record['value']}")
    else:
        print("Run this command to get the validation records:")
        code_block("terraform output -json acm_validation_records")

    if not dry_run:
        wait_for_user(
            "Add the CNAME record(s) to your DNS provider.\n"
            "AWS will validate automatically within ~5 minutes after DNS propagates."
        )
        success("ACM validation records added")


def walkthrough_dns_setup(outputs: dict, dry_run: bool = False) -> None:
    """Walk user through pointing domain to ALB."""
    header("Point Domain to Load Balancer")

    print("Your domain needs to point to the Application Load Balancer.\n")

    if "alb_dns_name" in outputs:
        alb_dns = outputs["alb_dns_name"]["value"]

        subheader("Create this DNS record")
        print(f"  {Colors.BOLD}Type:{Colors.END}  CNAME (or Alias if using Route53)")
        print(f"  {Colors.BOLD}Name:{Colors.END}  shifter.yourdomain.com (your domain)")
        print(f"  {Colors.BOLD}Value:{Colors.END} {alb_dns}")
    else:
        print("Run this command to get the ALB DNS name:")
        code_block("terraform output alb_dns_name")

    if not dry_run:
        wait_for_user("Add the CNAME record pointing your domain to the ALB.")
        success("Domain DNS configured")


def walkthrough_cognito_user(outputs: dict, env: str, profile: str, dry_run: bool = False) -> None:
    """Walk user through creating first Cognito user."""
    header("Create First User")

    print("You need at least one user to log into the portal.\n")

    if "cognito_user_pool_id" in outputs:
        pool_id = outputs["cognito_user_pool_id"]["value"]

        subheader("Create admin user")

        cmd = f"""aws cognito-idp admin-create-user \\
  --user-pool-id {pool_id} \\
  --username YOUR_EMAIL@example.com \\
  --user-attributes Name=email,Value=YOUR_EMAIL@example.com \\
  --desired-delivery-mediums EMAIL"""

        code_block(cmd)

        print(f"\n{Colors.DIM}The user will receive an email with a temporary password.{Colors.END}")
    else:
        print("Run this to get the user pool ID:")
        code_block("terraform output cognito_user_pool_id")
        print("\nThen create a user with:")
        code_block("""aws cognito-idp admin-create-user \\
  --user-pool-id <POOL_ID> \\
  --username user@example.com \\
  --user-attributes Name=email,Value=user@example.com""")

    if not dry_run:
        if confirm("Create the first user now?"):
            if "cognito_user_pool_id" in outputs:
                pool_id = outputs["cognito_user_pool_id"]["value"]
                email = input(f"{Colors.CYAN}Enter email for first user: {Colors.END}").strip()
                if email:
                    run_cmd(
                        [
                            "aws",
                            "cognito-idp",
                            "admin-create-user",
                            "--user-pool-id",
                            pool_id,
                            "--username",
                            email,
                            "--user-attributes",
                            f"Name=email,Value={email}",
                            "--desired-delivery-mediums",
                            "EMAIL",
                        ],
                        profile=profile,
                    )
                    success(f"User {email} created - they will receive an email with temporary password")
        else:
            info("You can create users later via AWS Console or CLI")


def walkthrough_final_steps(env: str) -> None:
    """Show final deployment status and next steps."""
    header("Deployment Complete!")

    print(f"{Colors.GREEN}{'=' * 60}{Colors.END}")
    print(f"{Colors.GREEN}  Shifter {env.upper()} environment is now deployed!{Colors.END}")
    print(f"{Colors.GREEN}{'=' * 60}{Colors.END}")

    print(f"""
{Colors.BOLD}What's Running:{Colors.END}
  ✓ ECR repositories (empty, will be populated by CI/CD)
  ✓ Range VPC with Network Firewall
  ✓ Portal VPC with RDS, EC2, ALB
  ✓ Cognito authentication
  ✓ All IAM roles and policies

{Colors.BOLD}To Complete Setup:{Colors.END}
  1. Wait for ACM certificate validation (~5 min after DNS)
  2. Push code to 'main' branch to trigger first CI/CD run
  3. CI/CD will build and deploy the portal container

{Colors.BOLD}Verify Deployment:{Colors.END}
  - Check GitHub Actions for CI/CD status
  - Once complete, visit https://your-domain.com
  - Log in with the Cognito user you created

{Colors.BOLD}Troubleshooting:{Colors.END}
  - ACM stuck? Check DNS propagation: dig CNAME _xxx.your-domain.com
  - CI/CD failing? Check GitHub Actions logs
  - Portal not loading? Check EC2 instance logs in CloudWatch
""")


def full_deployment(env: str, profile: str, dry_run: bool = False) -> None:
    """Run complete deployment with interactive walkthrough."""
    header(f"Full {env.upper()} Deployment")

    print("""
This will guide you through a complete Shifter deployment:

  1. Bootstrap AWS account (S3, DynamoDB, IAM)
  2. Configure GitHub secrets (automated with gh CLI or manual)
  3. Write instance Terraform backend configuration (outside the repo)
  4. Set up GitHub Actions runners (optional - for self-hosted CI/CD)
  5. Deploy infrastructure (Core → Range → Portal)
  6. Configure DNS and SSL certificate (manual - external DNS)
  7. Create first user

Automated steps will ask for confirmation:
  [y] yes - run automatically
  [n] no - abort (all steps are required)
  [m] manual - show instructions and wait

Estimated time: 30-45 minutes (mostly waiting for RDS and ACM)
""")

    if not dry_run and not confirm("Ready to begin?"):
        warn("Deployment cancelled")
        return

    if dry_run:
        info("[DRY-RUN] Showing what would happen...")

    # Phase 1: Bootstrap
    config = BootstrapConfig(env=env)
    bootstrap_result = bootstrap_account(config, profile, dry_run=dry_run)

    # Phase 2: GitHub Secrets
    walkthrough_github_secrets(bootstrap_result, dry_run=dry_run)

    # Phase 3: Backend Configuration
    walkthrough_backend_config(bootstrap_result, dry_run=dry_run)

    # Phase 4: GitHub Actions Runner Setup (optional)
    runner_result = None
    if RUNNER_AVAILABLE:
        runner_config = get_runner_config(
            env=env,
            region=config.region,
            github_org=config.github_org,
            github_repo=config.github_repo,
            aws_profile=profile,
        )
        runner_result = walkthrough_runner_setup(runner_config, dry_run=dry_run)
        if runner_result:
            # Store app_id for terraform vars if needed
            info(f"Runner App ID: {runner_result.get('app_id', 'N/A')}")
    else:
        warn("Runner module not available - skipping GitHub runner setup")

    # Phase 5: Terraform Deployment
    if not dry_run and not confirm("Continue with Terraform deployment?"):
        print("\nYou can resume later with:")
        code_block(f"./scripts/bootstrap/deploy.py terraform --env {env} --profile {profile}")
        return

    outputs = terraform_deploy(
        env,
        profile,
        dry_run=dry_run,
        bucket_name=bootstrap_result.get("bucket_name"),
    )

    if not dry_run and outputs:
        # Phase 6: ACM Validation
        walkthrough_acm_validation(outputs, dry_run=dry_run)

        # Phase 7: DNS Setup
        walkthrough_dns_setup(outputs, dry_run=dry_run)

        # Phase 8: First User
        walkthrough_cognito_user(outputs, env, profile, dry_run=dry_run)

    # Final Summary
    walkthrough_final_steps(env)


def _missing_dependency_lines(commands: dict[str, str]) -> list[str]:
    """Return formatted '  - cmd: desc' lines for each command not found on PATH."""
    return [f"  - {cmd}: {desc}" for cmd, desc in commands.items() if not shutil.which(cmd)]


def check_dependencies(command: str | None = None):
    """Check command-specific dependencies before starting."""
    required = {"git": "Git - https://git-scm.com/downloads"}

    if command in {None, "bootstrap", "terraform", "full"}:
        required.update(
            {
                "aws": "AWS CLI - https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html",
                "terraform": "Terraform - https://developer.hashicorp.com/terraform/downloads",
            }
        )

    if command == "gdc-bootstrap":
        required.update(
            {
                "gcloud": "Google Cloud CLI - https://cloud.google.com/sdk/docs/install",
                "ssh-keygen": "OpenSSH client tools - https://www.openssh.com/",
                "terraform": "Terraform - https://developer.hashicorp.com/terraform/downloads",
                "docker": "Docker - https://docs.docker.com/engine/install/",
                "kubectl": "kubectl - https://kubernetes.io/docs/tasks/tools/",
                "helm": "Helm - https://helm.sh/docs/intro/install/",
            }
        )

    optional = {"gh": "GitHub CLI - https://cli.github.com/ (recommended for automating GitHub secrets)"}

    missing_required = _missing_dependency_lines(required)
    missing_optional = _missing_dependency_lines(optional)

    if missing_required:
        error("Missing required dependencies:")
        for item in missing_required:
            print(item)
        sys.exit(1)

    if missing_optional:
        warn("Missing optional dependencies (some automation features will be unavailable):")
        for item in missing_optional:
            print(item)
        print()


def main():
    parser = argparse.ArgumentParser(
        description="Shifter deployment CLI - interactive deployment guide",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Preview full deployment (no changes)
  ./scripts/bootstrap/deploy.py full --env prod --profile my-prod-profile --dry-run

  # Run full interactive deployment
  ./scripts/bootstrap/deploy.py full --env prod --profile my-prod-profile

  # Just bootstrap AWS account
  ./scripts/bootstrap/deploy.py bootstrap --env prod --profile my-prod-profile

  # Just run terraform (after bootstrap)
  ./scripts/bootstrap/deploy.py terraform --env prod --profile my-prod-profile

  # Bootstrap a repeatable Google Distributed Cloud VM Runtime cluster
  ./scripts/bootstrap/deploy.py gdc-bootstrap --project-id prod-rwctxzl6shxk --cluster-id cluster1
        """,
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # Bootstrap command
    bootstrap_parser = subparsers.add_parser("bootstrap", help="Bootstrap AWS account (S3, DynamoDB, IAM)")
    bootstrap_parser.add_argument("--env", required=True, choices=AWS_ENVIRONMENTS, help="Environment")
    bootstrap_parser.add_argument("--profile", required=True, help=HELP_AWS_PROFILE)
    bootstrap_parser.add_argument("--dry-run", action="store_true", help=HELP_DRY_RUN)

    # Terraform command
    tf_parser = subparsers.add_parser("terraform", help="Deploy Terraform infrastructure")
    tf_parser.add_argument("--env", required=True, choices=AWS_ENVIRONMENTS, help="Environment")
    tf_parser.add_argument("--profile", required=True, help=HELP_AWS_PROFILE)
    tf_parser.add_argument("--dry-run", action="store_true", help=HELP_DRY_RUN)

    # Full command
    full_parser = subparsers.add_parser("full", help="Full interactive deployment (bootstrap + config + terraform)")
    full_parser.add_argument("--env", required=True, choices=AWS_ENVIRONMENTS, help="Environment")
    full_parser.add_argument("--profile", required=True, help=HELP_AWS_PROFILE)
    full_parser.add_argument("--dry-run", action="store_true", help=HELP_DRY_RUN)

    gdc_parser = subparsers.add_parser(
        "gdc-bootstrap",
        help="Bootstrap a repeatable Google Distributed Cloud VM Runtime evaluation cluster",
    )
    gdc_parser.add_argument(
        "--project-id",
        default=get_default_gdc_project_id(),
        help="GCP project ID (defaults to PANW_GCP_DEV or repo-root .env)",
    )
    gdc_parser.add_argument("--cluster-id", default="cluster1", help="Cluster name / prefix")
    gdc_parser.add_argument("--region", default="us-central1", help="Cluster region")
    gdc_parser.add_argument("--zone", default="us-central1-a", help="Compute Engine zone")
    gdc_parser.add_argument("--google-account-email", help="Optional Google identity to grant cluster-admin")
    gdc_parser.add_argument(
        "--shifter-config",
        help=(
            "Path to the deployment's shifter.yaml; its settings.range_egress is rendered into "
            "range_egress.auto.tfvars before the control-plane apply (#1015). Defaults to "
            "$SHIFTER_CONFIG or ./shifter.yaml; a missing config fails the deploy."
        ),
    )
    gdc_parser.add_argument("--dry-run", action="store_true", help=HELP_DRY_RUN)

    args = parser.parse_args()
    check_dependencies(args.command)

    if args.command == "bootstrap":
        config = BootstrapConfig(env=args.env)
        result = bootstrap_account(config, args.profile, dry_run=args.dry_run)
        if not args.dry_run:
            walkthrough_github_secrets(result, dry_run=args.dry_run)
            walkthrough_backend_config(result, dry_run=args.dry_run)

    elif args.command == "terraform":
        outputs = terraform_deploy(args.env, args.profile, dry_run=args.dry_run)
        if not args.dry_run and outputs:
            walkthrough_acm_validation(outputs, dry_run=args.dry_run)
            walkthrough_dns_setup(outputs, dry_run=args.dry_run)
            walkthrough_cognito_user(outputs, args.env, args.profile, dry_run=args.dry_run)
            walkthrough_final_steps(args.env)

    elif args.command == "full":
        full_deployment(args.env, args.profile, dry_run=args.dry_run)

    elif args.command == "gdc-bootstrap":
        gdc_bootstrap_cluster(
            GDCBootstrapConfig(
                project_id=args.project_id,
                cluster_id=args.cluster_id,
                region=args.region,
                zone=args.zone,
                google_account_email=args.google_account_email,
                shifter_config_path=args.shifter_config,
            ),
            dry_run=args.dry_run,
        )


if __name__ == "__main__":
    main()
