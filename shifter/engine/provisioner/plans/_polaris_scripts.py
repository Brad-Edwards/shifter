"""Bash bootstrap-script templates for the POLARIS range plan.

Extracted from ``polaris_range_bootstrap.py`` (Sonar S104). These are the SSM
RunCommand script bodies that :class:`PolarisRangeBootstrapPlan` injects as its
setup steps; keeping the large embedded bash in its own module keeps the plan
module under the Sonar line budget. The plan module imports them back, so its
public surface is unchanged.
"""

# AWS-only fragments that POLARIS_RANGE_BOOTSTRAP_SCRIPT substitutes via the
# {{ aws_agent_setup_block }} / {{ aws_agent_compose_block }} template tokens
# (#1377). They are built in Python (not via nested {{ }} tokens) and injected as
# the *value* of a single template token each: _render_script only substitutes
# tokens present in the ORIGINAL template text, so a nested {{ region }} inside an
# injected block would never resolve -- hence the real region / model ids are
# baked in via render_aws_agent_blocks() below. Those values are allowlist-
# validated upstream (config.load_aws_polaris_agent_config, #1377 cycle-2 finding
# 3), so plain substitution into shell/YAML is safe. Because these are plain
# Python strings (not a bash-runtime `if`), GCP -- whose context supplies "" for
# both tokens -- renders byte-for-byte unchanged (see tests/test_bootstrap_plan.py
# TestPolarisAwsAgentSecurity byte-parity assertions).
#
# Durability (#1377 cycle-2 finding 2): everything a14-kali needs to consume the
# per-range STS credential is delivered by read-only bind mounts + the compose
# environment, materialized on the HOST *before* the override is written and the
# container starts. Nothing is written into the container layer, so a
# `docker compose up --force-recreate a14-kali` preserves the whole provider
# configuration, not just the mounted credentials.
_AWS_AGENT_RUN_DIR_SETUP_TEMPLATE = r"""# AWS-only (#1377): materialize all durable a14-kali agent config on the host
# BEFORE the compose override is written and the container starts, so it
# survives docker compose up --force-recreate. Delivered to a14-kali by
# read-only bind mounts + the compose environment (see the compose block),
# never written into the mutable container layer.
mkdir -p /run/shifter-agent
chown root:root /run/shifter-agent
chmod 711 /run/shifter-agent

# credential_process reader: emits the host-refreshed STS JSON the SDK
# consumes. On the mounted dir so a recreate keeps it.
cat > /run/shifter-agent/credential-process.sh <<'READER_EOF'
#!/bin/sh
set -eu
exec cat /run/shifter-agent/credentials.json
READER_EOF
chmod 755 /run/shifter-agent/credential-process.sh
chown root:root /run/shifter-agent/credential-process.sh

# AWS profile pointing at the credential_process reader. a14-kali receives
# AWS_CONFIG_FILE=/run/shifter-agent/aws-config via the compose environment.
cat > /run/shifter-agent/aws-config <<'AWSCFG_EOF'
[default]
credential_process = /run/shifter-agent/credential-process.sh
region = __AWS_REGION__
AWSCFG_EOF
chmod 644 /run/shifter-agent/aws-config
chown root:root /run/shifter-agent/aws-config

# Login-shell env for the participant's interactive Claude Code session.
# docker exec inherits the compose environment, but SSH login shells do not,
# so the Claude/Bedrock exports are ALSO delivered here and bind-mounted to
# /etc/profile.d/claude-bedrock.sh (compose block) so they survive recreate.
cat > /run/shifter-agent/claude-bedrock.sh <<'PROFILE_EOF'
# Managed by polaris_range_bootstrap (#1377) - do not edit manually.
export CLAUDE_CODE_USE_BEDROCK=1
export AWS_SDK_LOAD_CONFIG=1
export AWS_REGION=__AWS_REGION__
export AWS_CONFIG_FILE=/run/shifter-agent/aws-config
export ANTHROPIC_MODEL=__MAIN_MODEL__
export ANTHROPIC_SMALL_FAST_MODEL=__SMALL_MODEL__
PROFILE_EOF
chmod 644 /run/shifter-agent/claude-bedrock.sh
chown root:root /run/shifter-agent/claude-bedrock.sh

# Resolve the Bedrock VPC-endpoint private IP host-side and publish it to the
# compose .env so a14-kali's extra_hosts entry resolves the FQDN to the
# endpoint on every up/recreate -- durable, unlike an /etc/hosts write into the
# container layer. a14-kali cannot resolve it itself (no IMDS/public egress and
# its scenario DNS does not serve AWS FQDNs).
_BEDROCK_FQDN="bedrock-runtime.__AWS_REGION__.amazonaws.com"
_BEDROCK_IP="$(getent hosts "$_BEDROCK_FQDN" | awk '{print $1; exit}')"
case "$_BEDROCK_IP" in
  10.*|172.1[6-9].*|172.2[0-9].*|172.3[0-1].*|192.168.*) : ;;
  *)
    if command -v dig >/dev/null 2>&1; then
      _BEDROCK_IP="$(dig +short @169.254.169.253 "$_BEDROCK_FQDN" | grep -E '^10\.' | head -n1 || true)"
    fi
    ;;
esac
if [ -z "$_BEDROCK_IP" ]; then
  echo "polaris bootstrap: could not resolve $_BEDROCK_FQDN to a private IP" >&2
  exit 1
fi
_ENV_FILE="/opt/polaris/scenario-dev/polaris/build/.env"
touch "$_ENV_FILE"
grep -v '^SHIFTER_BEDROCK_IP=' "$_ENV_FILE" > "$_ENV_FILE.tmp" 2>/dev/null || true
echo "SHIFTER_BEDROCK_IP=$_BEDROCK_IP" >> "$_ENV_FILE.tmp"
mv "$_ENV_FILE.tmp" "$_ENV_FILE"
"""

# Appended after a14-kali's environment: block in the compose override. Adds the
# Bedrock/Claude env (for docker exec + PID-1 processes), the read-only
# /run/shifter-agent mount, a read-only bind of the profile.d env shim (for SSH
# login shells), and an extra_hosts entry that resolves the Bedrock FQDN to the
# VPC-endpoint IP published in the compose .env by the setup block above. The
# \${SHIFTER_BEDROCK_IP} is escaped so the unquoted COMPOSE_EOF heredoc writes it
# literally for docker compose to substitute on every up/recreate.
_AWS_AGENT_COMPOSE_TEMPLATE = (
    '\n      CLAUDE_CODE_USE_BEDROCK: "1"'
    '\n      AWS_SDK_LOAD_CONFIG: "1"'
    '\n      AWS_REGION: "__AWS_REGION__"'
    '\n      ANTHROPIC_MODEL: "__MAIN_MODEL__"'
    '\n      ANTHROPIC_SMALL_FAST_MODEL: "__SMALL_MODEL__"'
    '\n      AWS_CONFIG_FILE: "/run/shifter-agent/aws-config"'
    "\n    volumes:"
    "\n      - /run/shifter-agent:/run/shifter-agent:ro"
    "\n      - /run/shifter-agent/claude-bedrock.sh:/etc/profile.d/claude-bedrock.sh:ro"
    "\n    extra_hosts:"
    '\n      - "bedrock-runtime.__AWS_REGION__.amazonaws.com:\\${SHIFTER_BEDROCK_IP}"'
)


def render_aws_agent_blocks(region: str, main_model: str, small_model: str) -> tuple[str, str]:
    """Return (setup_block, compose_block) with real values baked in.

    ``region`` / ``main_model`` / ``small_model`` are allowlist-validated by
    ``config.load_aws_polaris_agent_config`` before reaching here (#1377 cycle-2
    finding 3), so substituting them into the shell/YAML fragments is safe.
    Building the strings in Python (rather than leaving ``{{ region }}`` tokens
    inside the fragments) is required because these are injected as the value of
    a single template token and ``_render_script`` does not resolve tokens that
    appear only inside an injected value.
    """

    def _fill(template: str) -> str:
        return (
            template.replace("__AWS_REGION__", region)
            .replace("__MAIN_MODEL__", main_model)
            .replace("__SMALL_MODEL__", small_model)
        )

    return _fill(_AWS_AGENT_RUN_DIR_SETUP_TEMPLATE), _fill(_AWS_AGENT_COMPOSE_TEMPLATE)


# Bash run on the polaris VM Ubuntu host via SSM. Rewrites the bake-time
# docker-compose.override.yml with this range's DC IP + per-instance kali
# pubkey, then force-recreates the dns + a14-kali containers so their
# entrypoints pick up the new env vars and re-render their internal state.
POLARIS_RANGE_BOOTSTRAP_SCRIPT = """#!/bin/bash
set -euo pipefail

DC_IP="{{ dc_ip }}"
KALI_PUBKEY="{{ public_key }}"

if [[ -z "$DC_IP" ]]; then
  echo "polaris bootstrap: DC_IP is empty, refusing to rewrite override" >&2
  exit 1
fi
if [[ -z "$KALI_PUBKEY" ]]; then
  echo "polaris bootstrap: KALI_PUBKEY is empty, refusing to rewrite override" >&2
  exit 1
fi
{{ aws_agent_setup_block }}
cd /opt/polaris/scenario-dev/polaris/build

# Per-range Ed25519 keypair for the A9 splice-relay credential gate
# (#707). The private half is staged on a14-kali via the entrypoint
# (`KALI_SPLICE_PRIVATE_KEY_B64`, base64 so the value stays single-line
# inside the compose override); the public half is installed into
# a9-splice's /root/.ssh/authorized_keys via A9_AUTHORIZED_KEY. A9's
# sshd has PasswordAuthentication off (Dockerfile change), so this key
# is the only path to the Bunker OT controllers. Per-range generation
# means an exfil from one participant's a14-kali cannot be used to
# attack another range — even though ranges are network-isolated, the
# key is treated as scenario credential material with least exposure.
SPLICE_KEY_DIR="$(mktemp -d)"
chmod 700 "$SPLICE_KEY_DIR"
ssh-keygen -t ed25519 -N "" -C "splice-relay@$(date -u +%Y%m%dT%H%M%SZ)" \
    -f "$SPLICE_KEY_DIR/splice_relay" -q
SPLICE_PRIVATE_KEY_B64="$(base64 -w0 < "$SPLICE_KEY_DIR/splice_relay")"
SPLICE_PUBLIC_KEY="$(cat "$SPLICE_KEY_DIR/splice_relay.pub")"
shred -u "$SPLICE_KEY_DIR/splice_relay" "$SPLICE_KEY_DIR/splice_relay.pub" 2>/dev/null \
    || rm -f "$SPLICE_KEY_DIR/splice_relay" "$SPLICE_KEY_DIR/splice_relay.pub"
rmdir "$SPLICE_KEY_DIR"

# Atomic rewrite via tmp + mv so docker compose never sees a partial file.
cat > docker-compose.override.yml.new <<COMPOSE_EOF
services:
  a14-kali:
    ports:
      - "22:22"
      - "3389:3389"
    environment:
      KALI_AUTHORIZED_KEY: "$KALI_PUBKEY"
      KALI_SPLICE_PRIVATE_KEY_B64: "$SPLICE_PRIVATE_KEY_B64"{{ aws_agent_compose_block }}
  a9-splice:
    environment:
      A9_AUTHORIZED_KEY: "$SPLICE_PUBLIC_KEY"
  dns:
    environment:
      DC01_IP: "$DC_IP"
COMPOSE_EOF
mv docker-compose.override.yml.new docker-compose.override.yml

# Force-recreate only the containers whose env vars changed. The other
# 14 stay running undisturbed. a9-splice was added in #707 because the
# A9 entrypoint now consumes A9_AUTHORIZED_KEY.
docker compose up -d --force-recreate dns a14-kali a9-splice

# The baked compose attaches a14-kali to splice-link at container start
# (legacy pre-gate wiring). Strip that here — the splice landing is gated
# on flag 19 via the polaris-splice-watcher systemd service, which will
# reattach a14-kali once A5 reports runaway_complete. Docker compose
# prefixes network names with the project name (here: "build"), so the
# actual name is "build_splice-link"; discover by suffix to stay robust
# against project-name changes. Non-fatal if already disconnected.
splice_net_name=$(docker network ls --format '{{.Name}}' | grep -E '(^|_)splice-link$' | head -n1 || true)
if [[ -n "$splice_net_name" ]]; then
  docker network disconnect "$splice_net_name" a14-kali 2>/dev/null || true
fi

# Wait up to 60s for the three recreated containers to be Up before
# declaring success.
# `docker ps --format` uses Go template syntax (e.g. .Names, .Status)
# inside double-brace delimiters. The orchestrator's render pass uses a
# regex that requires word characters between the delimiters, so Go
# template tokens with a leading dot pass through untouched. (Don't
# describe Jinja-style placeholders inline in this comment — the
# renderer would see them too and demand a substitution variable.)
for attempt in 1 2 3 4 5 6 7 8 9 10 11 12; do
  ps_out=$(docker ps --format '{{.Names}} {{.Status}}' || true)
  a14_up=$(echo "$ps_out" | grep -c '^a14-kali .*Up' || true)
  dns_up=$(echo "$ps_out" | grep -c '^dns .*Up' || true)
  a9_up=$(echo "$ps_out" | grep -c '^a9-splice .*Up' || true)
  if [[ "$a14_up" == "1" && "$dns_up" == "1" && "$a9_up" == "1" ]]; then
    echo "polaris bootstrap: a14-kali + dns + a9-splice up after attempt $attempt"
    break
  fi
  sleep 5
done

# Verify the kali container actually has the per-instance pubkey written
# (the a14 entrypoint reads $KALI_AUTHORIZED_KEY and writes the file).
for attempt in 1 2 3 4 5; do
  if docker exec a14-kali test -s /home/kali/.ssh/authorized_keys 2>/dev/null; then
    echo "polaris bootstrap: kali authorized_keys present"
    break
  fi
  sleep 3
done

# Verify the splice key staging (#707): private key on a14-kali, public
# key in a9-splice. The Bunker chain depends on both.
for attempt in 1 2 3 4 5; do
  splice_priv_ok=0
  splice_pub_ok=0
  docker exec a14-kali test -s /home/kali/.ssh/splice_relay 2>/dev/null && splice_priv_ok=1
  docker exec a9-splice test -s /root/.ssh/authorized_keys 2>/dev/null && splice_pub_ok=1
  if [[ "$splice_priv_ok" == "1" && "$splice_pub_ok" == "1" ]]; then
    echo "polaris bootstrap: splice key staged on a14-kali and a9-splice"
    break
  fi
  sleep 3
done

echo "polaris bootstrap: complete"
exit 0
"""

# Pulls the latest scenario-dev/polaris/tests/ tree out of S3 and unpacks it
# under /opt/polaris/scenario-dev/polaris/tests/. The polaris-vm AMI bakes
# only the build/ subtree (docker compose stack), not the tests/ subtree,
# so the organizer-facing smoketest harness otherwise isn't available on
# freshly provisioned ranges. This step materialises it on every range so
# `bash /opt/polaris/scenario-dev/polaris/tests/run-all-smoketests.sh`
# works out-of-the-box without any per-range manual upload.
#
# The bucket + prefix are rendered from the provisioner environment. By default
# this uses AGENT_S3_BUCKET because the dev-range-range-instance IAM role is
# already granted GetObject there. A new tarball is uploaded by the operator
# whenever the test harness or an individual smoketest is fixed; the download
# is idempotent so re-runs pick up the latest.
FETCH_POLARIS_TESTS_SCRIPT = """#!/bin/bash
set -euo pipefail

BUCKET="{{ polaris_tests_bucket }}"
KEY="{{ polaris_tests_key }}"
DEST_ROOT="/opt/polaris/scenario-dev/polaris"
TARBALL="/tmp/polaris-tests.tar.gz"

mkdir -p "$DEST_ROOT"

# aws cli is preinstalled on the polaris-vm AMI base. It picks up the
# EC2 instance profile automatically via IMDSv2, so no explicit creds.
aws s3 cp "s3://$BUCKET/$KEY" "$TARBALL" --region us-east-2

if [[ ! -s "$TARBALL" ]]; then
  echo "polaris tests fetch: downloaded tarball is empty" >&2
  exit 1
fi

# Clear any stale tests/ from a previous bootstrap before extracting,
# so removed test files don't linger.
rm -rf "$DEST_ROOT/tests"

tar xzf "$TARBALL" -C "$DEST_ROOT"

if [[ ! -x "$DEST_ROOT/tests/run-all-smoketests.sh" ]]; then
  echo "polaris tests fetch: run-all-smoketests.sh missing after extract" >&2
  ls -la "$DEST_ROOT/tests" >&2 || true
  exit 1
fi

# Make every script in tests/ executable (tar may not preserve +x on a
# subset of *.py files depending on how the tarball was built).
find "$DEST_ROOT/tests" -type f \\( -name '*.sh' -o -name '*.py' \\) -exec chmod +x {} +

echo "polaris tests fetch: tests/ tree materialised at $DEST_ROOT/tests"
ls "$DEST_ROOT/tests/smoketests" | wc -l | xargs -I{} echo "polaris tests fetch: {} smoketest files available"
exit 0
"""

# Installs the splice watcher as a systemd service. The watcher polls
# A5's /api/status for `runaway_complete` and, when the participant
# earns flag 19 (generator meltdown), attaches a14-kali to the
# splice-link docker network so the A14 -> A9 pivot opens. At range
# start A14 is NOT on splice-link (the preceding bootstrap step runs
# `docker network disconnect splice-link a14-kali` to strip the baked
# compose pre-wiring), so until the watcher fires the bunker-ot path
# is sealed.
#
# Both the watcher script and the systemd unit are written from here —
# nothing is read from the baked build/ tree — so pushing provisioner
# code changes propagates to every new range without an AMI rebake.
# Idempotent: safe to re-run on bootstrap retries.
INSTALL_SPLICE_WATCHER_SCRIPT = """#!/bin/bash
set -euo pipefail

WATCHER="/usr/local/bin/polaris-splice-watcher.sh"
UNIT="/etc/systemd/system/polaris-splice-watcher.service"

# Quoted heredoc delimiter prevents host-side shell expansion — the
# watcher's shell vars and command substitutions are interpreted at
# watcher runtime, not now. Uses Go template tokens (docker --format);
# those all carry a leading dot, so the orchestrator's Jinja regex
# (which matches word-chars-only between the double-brace delimiters)
# leaves them untouched. No bare word-only tokens appear inside the
# braces anywhere in this heredoc — those would be matched and
# treated as missing template variables by the renderer.
cat > "$WATCHER" <<'WATCHER_EOF'
#!/bin/bash
# polaris-splice-watcher: poll A5 HMI state; when the generator goes
# into thermal runaway (flag 19 earned), attach a14-kali to the
# splice-link docker network so the participant can reach a9-splice.
set -euo pipefail

# A5 container_name in scenario-dev/polaris/build/docker-compose.yml is
# "a5-scada" — verified empirically. Earlier "a5-scada-generator" default
# was the source of a silent failure at BSides Ottawa (2026-04): the
# watcher polled a non-existent container, never observed
# runaway_complete=true, never attached A14 to splice-link, and
# operators had to manually `docker network connect` per participant.
A5_CONTAINER="${A5_CONTAINER:-a5-scada}"
KALI_CONTAINER="${KALI_CONTAINER:-a14-kali}"
# Compose network name is "<project>_splice-link"; the compose project
# lives at /opt/polaris/scenario-dev/polaris/build so project name is
# "build" by default. Allow env override for local testing.
SPLICE_NETWORK="${SPLICE_NETWORK:-build_splice-link}"
SPLICE_IP="${SPLICE_IP:-172.20.60.140}"
POLL_INTERVAL_S="${POLL_INTERVAL_S:-10}"

poll_runaway_complete() {
  local body
  local py_script='import urllib.request
print(urllib.request.urlopen("http://127.0.0.1:8080/api/status", timeout=5).read().decode())'
  body=$(docker exec "$A5_CONTAINER" python3 -c "$py_script" 2>/dev/null) || return 1
  [[ "$body" == *'"runaway_complete": true'* ]] || [[ "$body" == *'"runaway_complete":true'* ]]
}

is_connected() {
  # Dot-prefixed Go template fields pass through the orchestrator's
  # Jinja regex untouched.
  docker inspect "$KALI_CONTAINER" \
    --format '{{json .NetworkSettings.Networks}}' 2>/dev/null \
    | grep -q "\\"$SPLICE_NETWORK\\""
}

connect_splice() {
  echo "polaris-splice-watcher: connecting $KALI_CONTAINER to $SPLICE_NETWORK ($SPLICE_IP)"
  docker network connect --ip "$SPLICE_IP" "$SPLICE_NETWORK" "$KALI_CONTAINER"
}

echo "polaris-splice-watcher: starting (network=$SPLICE_NETWORK, container=$KALI_CONTAINER)"

while true; do
  if poll_runaway_complete; then
    if ! is_connected; then
      if connect_splice; then
        echo "polaris-splice-watcher: splice established"
      else
        echo "polaris-splice-watcher: connect failed, will retry" >&2
      fi
    fi
  fi
  sleep "$POLL_INTERVAL_S"
done
WATCHER_EOF
chmod +x "$WATCHER"

cat > "$UNIT" <<UNIT_EOF
[Unit]
Description=Polaris splice watcher (attaches a14-kali to splice-link on flag 19)
After=docker.service
Requires=docker.service

[Service]
Type=simple
ExecStart=$WATCHER
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
UNIT_EOF

systemctl daemon-reload
systemctl enable polaris-splice-watcher.service
systemctl restart polaris-splice-watcher.service

echo "polaris splice watcher: installed and started"
exit 0
"""

# AWS-only (#1377 slice 5). Durable replacement for the old IMDS-hop-limit
# path: instead of raising the instance's IMDSv2 hop limit so a14-kali could
# reach the host's IMDS token (and therefore the shared, SSM/S3-capable host
# operations role -- see docs/architecture/polaris-aws-agent-credentials-
# preflight-1377.md), this drops every forwarded container request to IMDS
# outright. The a14-kali container never needs IMDS at all any more: its
# Bedrock credential is delivered host-side via STS (KALI_BEDROCK_SHARD_SCRIPT).
# Scoped to exactly 169.254.169.254/32 (and its IPv6 counterpart
# fd00:ec2::254/128); 169.254.169.253, the Amazon VPC DNS resolver, is never
# targeted by any rule here.
#
# The rule lives in DOCKER-USER (the one chain Docker guarantees it will
# never flush or reorder itself, unlike FORWARD) so it survives a `docker
# compose` container recreate. It is installed by BOTH a standalone systemd
# unit (covers host boot) and a docker.service drop-in `ExecStartPost` (covers
# `systemctl restart docker`, which rewrites Docker's own iptables chains and
# would otherwise silently drop this rule) -- a one-time `iptables` mutation
# during bootstrap is exactly the anti-pattern this replaces. Fails closed:
# the step exits non-zero, failing provisioning, unless the rule and the
# restore unit are actually present and active after install.
INSTALL_IMDS_FIREWALL_SCRIPT = """#!/bin/bash
set -euo pipefail

FIREWALL_SCRIPT="/usr/local/bin/shifter-block-imds.sh"
FIREWALL_SERVICE="/etc/systemd/system/shifter-block-imds.service"
DOCKER_DROPIN_DIR="/etc/systemd/system/docker.service.d"
DOCKER_DROPIN="$DOCKER_DROPIN_DIR/99-shifter-block-imds.conf"

# Idempotent: safe to (re-)install and (re-)start on every bootstrap retry.
cat > "$FIREWALL_SCRIPT" <<'FW_EOF'
#!/bin/bash
set -euo pipefail
# Drop forwarded container traffic to IMDS. Scoped to exactly this /32 so
# every other 169.254.169.0/24 address (including the VPC DNS resolver) is
# left completely untouched by this or any other rule here.
if ! iptables -C DOCKER-USER -d 169.254.169.254/32 -j DROP 2>/dev/null; then
  iptables -I DOCKER-USER -d 169.254.169.254/32 -j DROP
fi

# Block the IPv6 IMDS endpoint (fd00:ec2::254) the same way, when the host
# has an ip6tables DOCKER-USER chain at all. Not a skip when IPv6 is absent
# entirely -- there is no IPv6 IMDS surface to block in that case.
if command -v ip6tables >/dev/null 2>&1 && ip6tables -L DOCKER-USER >/dev/null 2>&1; then
  if ! ip6tables -C DOCKER-USER -d fd00:ec2::254/128 -j DROP 2>/dev/null; then
    ip6tables -I DOCKER-USER -d fd00:ec2::254/128 -j DROP
  fi
fi
FW_EOF
chmod 755 "$FIREWALL_SCRIPT"
chown root:root "$FIREWALL_SCRIPT"

# Apply immediately so the rule is live before a14-kali ever starts.
"$FIREWALL_SCRIPT"

# Restore unit: reapplies on every boot. After/Requires/PartOf docker.service
# so it only ever runs once Docker's own DOCKER-USER chain exists, and is
# torn down/rebuilt alongside Docker rather than drifting independently.
cat > "$FIREWALL_SERVICE" <<UNIT_EOF
[Unit]
Description=Shifter IMDS metadata firewall for polaris range containers
After=docker.service
Requires=docker.service
PartOf=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=$FIREWALL_SCRIPT

[Install]
WantedBy=multi-user.target
UNIT_EOF

# Docker daemon restart rewrites/flushes its managed chains; an ExecStartPost
# drop-in re-applies the rule every time dockerd (re)starts -- the only way
# to survive `systemctl restart docker` without a one-time bootstrap-only
# mutation.
mkdir -p "$DOCKER_DROPIN_DIR"
cat > "$DOCKER_DROPIN" <<DROPIN_EOF
[Service]
ExecStartPost=$FIREWALL_SCRIPT
DROPIN_EOF

systemctl daemon-reload
systemctl enable --now shifter-block-imds.service

# Fail closed: the range must not proceed unless the rule is actually
# present and the restore unit is enabled and active.
if ! iptables -C DOCKER-USER -d 169.254.169.254/32 -j DROP 2>/dev/null; then
  echo "polaris imds firewall: DOCKER-USER drop rule is not present after install" >&2
  exit 1
fi
if ! systemctl is-enabled --quiet shifter-block-imds.service; then
  echo "polaris imds firewall: shifter-block-imds.service is not enabled" >&2
  exit 1
fi
if ! systemctl is-active --quiet shifter-block-imds.service; then
  echo "polaris imds firewall: shifter-block-imds.service is not active" >&2
  exit 1
fi

echo "polaris imds firewall: DOCKER-USER drop rule installed, restore unit enabled and active"
exit 0
"""

# Configures Claude Code on the a14-kali container to talk to Bedrock using a
# per-range STS-assumed identity instead of IMDS (#1377 slice 5 -- this is
# the replacement for the old hop-limit-2 IMDS pass-through). Three things
# happen here, in order:
#
#   1. Host-side STS credential refresh. A root-owned refresh script assumes
#      the per-range Bedrock agent role using the HOST's own instance-profile
#      credentials (never a static key) and atomically writes the response,
#      in the AWS SDK `credential_process` JSON shape, under
#      /run/shifter-agent/ (mounted read-only into a14-kali by
#      POLARIS_RANGE_BOOTSTRAP_SCRIPT's AWS-only compose block). A systemd
#      timer re-runs it before expiry; the FIRST run happens here,
#      synchronously, as a bare statement under `set -euo pipefail` -- a
#      failed assume-role aborts this whole step (and therefore provisioning)
#      rather than warning and continuing. No `set -x`; the response is
#      validated for shape without ever being echoed.
#   2. a14-kali gets a tiny `credential_process` reader (just cats the
#      mounted JSON) and an AWS profile pointing at it, so the SDK re-invokes
#      it before each call needing fresh creds and honors the embedded
#      Expiration for auto-refresh -- the same mechanism GCP's per-range
#      Vertex key injection achieves via Secret Manager instead of STS.
#   3. The existing Bedrock VPC-endpoint /etc/hosts resolution (a14-kali is
#      on a bridge network that can't otherwise reach the private
#      bedrock-runtime endpoint) and the Claude Code env vars, parameterized
#      by the configured region instead of a hardcoded one.
#
# Smoke test: the AWS-only verify step (VERIFY_POLARIS_BOOTSTRAP_SCRIPT_AWS)
# invokes Bedrock from inside a14-kali and checks its caller identity. If
# Bedrock isn't reachable or creds aren't flowing, the range is reported as
# failed instead of marking it ready and handing a broken environment to a
# participant.
KALI_BEDROCK_SHARD_SCRIPT = """#!/bin/bash
set -euo pipefail

ROLE_ARN="{{ role_arn }}"
REGION="{{ region }}"
STS_SESSION_DURATION_SECONDS="{{ sts_session_duration_seconds }}"
REFRESH_WINDOW_SECONDS="{{ refresh_window_seconds }}"
RANGE_ID="{{ range_id }}"
ENVIRONMENT_NAME="{{ environment }}"

if [[ -z "$ROLE_ARN" || -z "$REGION" ]]; then
  echo "polaris kali bedrock shard: role_arn and region are required" >&2
  exit 2
fi

RUN_DIR="/run/shifter-agent"
CONFIG_DIR="/etc/shifter-agent"
CONFIG_FILE="$CONFIG_DIR/agent-role.env"
REFRESH_SCRIPT="/usr/local/bin/shifter-refresh-bedrock-creds.sh"
SESSION_NAME="${ENVIRONMENT_NAME}-range-${RANGE_ID}"

# 1a. Root-owned runtime dir for the STS response (idempotent -- the shared
# compose-rewrite step already creates this, but this step must not depend
# on step ordering across retries). 0711 so a14-kali's non-root user can
# traverse to the credentials file the refresh script writes at 0644; see
# docs/architecture/polaris-aws-agent-credentials-preflight-1377.md
# ("the participant is expected to be able to read ... that is its intended
# identity") -- root-only WRITE is the control that matters here.
umask 077
mkdir -p "$RUN_DIR"
chown root:root "$RUN_DIR"
chmod 711 "$RUN_DIR"

# 1b. Non-secret refresh config (role ARN, region, duration, session name).
# Written via a plain heredoc since none of this is a credential; the
# refresh script below sources it so its own body stays 100% static and
# never needs to be regenerated per range.
mkdir -p "$CONFIG_DIR"
chmod 755 "$CONFIG_DIR"
cat > "$CONFIG_FILE" <<CONFIG_EOF
ROLE_ARN=$ROLE_ARN
REGION=$REGION
DURATION_SECONDS=$STS_SESSION_DURATION_SECONDS
SESSION_NAME=$SESSION_NAME
RUN_DIR=$RUN_DIR
CRED_FILE=$RUN_DIR/credentials.json
CONFIG_EOF
chmod 644 "$CONFIG_FILE"
chown root:root "$CONFIG_FILE"

# 1c. The refresh script itself: static body (quoted heredoc, no bootstrap-
# time substitution) so it never needs rewriting after install. Assumes the
# per-range agent role using the HOST's OWN instance-profile credentials,
# validates the response shape without echoing it, and writes it atomically
# (temp file in the same directory + mv) in the credential_process JSON
# shape. Never traces its own execution.
cat > "$REFRESH_SCRIPT" <<'REFRESH_EOF'
#!/bin/bash
set -euo pipefail
umask 077

CONFIG_FILE="/etc/shifter-agent/agent-role.env"
if [[ ! -f "$CONFIG_FILE" ]]; then
  echo "shifter-refresh-bedrock-creds: missing $CONFIG_FILE" >&2
  exit 1
fi
# shellcheck disable=SC1090
source "$CONFIG_FILE"
if [[ -z "${ROLE_ARN:-}" || -z "${REGION:-}" || -z "${DURATION_SECONDS:-}" \\
      || -z "${RUN_DIR:-}" || -z "${CRED_FILE:-}" ]]; then
  echo "shifter-refresh-bedrock-creds: agent-role.env is incomplete" >&2
  exit 1
fi

RESPONSE_FILE="$(mktemp "$RUN_DIR/.sts-response.XXXXXX")"
ERR_FILE="$(mktemp)"
chmod 600 "$RESPONSE_FILE" "$ERR_FILE"
cleanup() {
  shred -u "$RESPONSE_FILE" "$ERR_FILE" 2>/dev/null || rm -f "$RESPONSE_FILE" "$ERR_FILE"
}
trap cleanup EXIT

if ! aws sts assume-role \\
    --role-arn "$ROLE_ARN" \\
    --role-session-name "$SESSION_NAME" \\
    --duration-seconds "$DURATION_SECONDS" \\
    --region "$REGION" \\
    --output json > "$RESPONSE_FILE" 2>"$ERR_FILE"; then
  echo "shifter-refresh-bedrock-creds: sts assume-role failed" >&2
  head -c 300 "$ERR_FILE" >&2 || true
  exit 1
fi

TMP_CRED_FILE="$(mktemp "$RUN_DIR/.credentials.json.XXXXXX")"
chmod 644 "$TMP_CRED_FILE"
if ! python3 - "$RESPONSE_FILE" "$TMP_CRED_FILE" <<'PYEOF'
import json
import sys

resp_path, out_path = sys.argv[1], sys.argv[2]
with open(resp_path, encoding="utf-8") as f:
    resp = json.load(f)
creds = resp.get("Credentials") or {}
required = ("AccessKeyId", "SecretAccessKey", "SessionToken", "Expiration")
if any(not creds.get(k) for k in required):
    sys.exit(1)
out = {
    "Version": 1,
    "AccessKeyId": creds["AccessKeyId"],
    "SecretAccessKey": creds["SecretAccessKey"],
    "SessionToken": creds["SessionToken"],
    "Expiration": creds["Expiration"],
}
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(out, f)
PYEOF
then
  echo "shifter-refresh-bedrock-creds: assume-role response failed shape validation" >&2
  rm -f "$TMP_CRED_FILE"
  exit 1
fi

mv "$TMP_CRED_FILE" "$CRED_FILE"
echo "shifter-refresh-bedrock-creds: credentials refreshed"
REFRESH_EOF
chmod 700 "$REFRESH_SCRIPT"
chown root:root "$REFRESH_SCRIPT"

# 1d. Run the refresh once, right now, as a bare statement -- `set -e`
# aborts this whole step (and therefore provisioning) if assume-role fails.
# This is deliberately NOT `"$REFRESH_SCRIPT" || echo warn` -- the old
# hop-limit path warned and continued on failure; this must not.
"$REFRESH_SCRIPT"

# 1e. Systemd timer: re-runs the refresh before the session expires. Period
# is the session duration minus the configured refresh window so there is
# always a live credential file. Also runs shortly after boot, since /run is
# tmpfs and does not survive a reboot.
cat > /etc/systemd/system/shifter-refresh-bedrock-creds.service <<UNIT_EOF
[Unit]
Description=Shifter per-range Bedrock STS credential refresh

[Service]
Type=oneshot
ExecStart=$REFRESH_SCRIPT
UNIT_EOF

REFRESH_PERIOD_SECONDS=$((STS_SESSION_DURATION_SECONDS - REFRESH_WINDOW_SECONDS))
if [[ "$REFRESH_PERIOD_SECONDS" -lt 60 ]]; then
  REFRESH_PERIOD_SECONDS=60
fi
cat > /etc/systemd/system/shifter-refresh-bedrock-creds.timer <<TIMER_EOF
[Unit]
Description=Periodic refresh of the per-range Bedrock STS credential

[Timer]
OnBootSec=30s
OnUnitActiveSec=${REFRESH_PERIOD_SECONDS}s
AccuracySec=10s

[Install]
WantedBy=timers.target
TIMER_EOF

systemctl daemon-reload
systemctl enable --now shifter-refresh-bedrock-creds.timer

# Fail closed: the range must not proceed unless the timer is actually wired
# up to keep refreshing (the .service unit is a oneshot triggered by the
# timer, so it is expected to be "inactive (dead)" between runs -- the timer
# itself is the thing that must be enabled and active).
if ! systemctl is-enabled --quiet shifter-refresh-bedrock-creds.timer; then
  echo "polaris kali bedrock shard: shifter-refresh-bedrock-creds.timer is not enabled" >&2
  exit 1
fi
if ! systemctl is-active --quiet shifter-refresh-bedrock-creds.timer; then
  echo "polaris kali bedrock shard: shifter-refresh-bedrock-creds.timer is not active" >&2
  exit 1
fi

# 2. a14-kali's credential_process reader, AWS profile (AWS_CONFIG_FILE), the
# Claude/Bedrock login env, and the Bedrock VPC-endpoint hosts entry are all
# delivered DURABLY by the shared polaris_range_bootstrap step, NOT written into
# the a14-kali container layer here: the reader / aws-config / profile.d env shim
# are written to the host under /run/shifter-agent and bind-mounted read-only,
# and AWS_CONFIG_FILE + the Bedrock env + the extra_hosts entry (resolved to the
# VPC-endpoint IP published in the compose .env) come from the compose override
# (see AWS_AGENT_RUN_DIR_SETUP_TEMPLATE / AWS_AGENT_COMPOSE_TEMPLATE in this
# module). A docker compose up --force-recreate a14-kali therefore keeps the
# whole provider configuration, not just the mounted credentials (#1377 cycle-2
# finding 2). This step owns only the host-side STS credential lifecycle above.
echo "polaris kali bedrock shard: STS refresh installed (per-range role, host-side credential_process, IMDS blocked)"
"""


# DC. If any check fails, the plan is reported as failed and the range
# provisioner aborts. The dig query runs from inside a14-kali because
# the alpine `bind` package on the dns container ships only the daemon
# (named) — `dig` lives in the separate `bind-tools` package and is not
# installed there. a14-kali has dig + ldap-utils + smbclient pre-baked
# and points its /etc/resolv.conf at the dns container by default, so
# `docker exec a14-kali dig` exercises the real participant resolution
# path end-to-end.
#
# Shared by both providers (checks 1-6). #1377 slice 5 adds AWS-only checks
# 7-11 (metadata firewall, STS refresh, per-range caller identity, Bedrock
# smoke invocation, IMDS denial) as a *separate* provider-selected constant
# (VERIFY_POLARIS_BOOTSTRAP_SCRIPT_AWS below) built from this shared body, so
# the GCP verify step -- this constant -- never changes by a single byte.
_VERIFY_POLARIS_BOOTSTRAP_COMMON = """#!/bin/bash
set -euo pipefail

DC_IP="{{ dc_ip }}"

# 1. a14-kali container is running.
if ! docker ps --format '{{.Names}}' | grep -qx 'a14-kali'; then
  echo "polaris verify: a14-kali is not running" >&2
  exit 1
fi

# 2. dns container is running.
if ! docker ps --format '{{.Names}}' | grep -qx 'dns'; then
  echo "polaris verify: dns is not running" >&2
  exit 1
fi

# 3. dns container resolves dc01.boreas.local to THIS range's DC IP.
#    Query from inside a14-kali because the alpine `bind` package on the
#    dns container does not include dig (it's in the separate `bind-tools`
#    package). a14-kali points at the dns container via docker compose's
#    bridge DNS, so this exercises the real participant lookup path.
resolved=$(docker exec a14-kali dig +short dc01.boreas.local || true)
if [[ "$resolved" != "$DC_IP" ]]; then
  echo "polaris verify: dc01.boreas.local resolved to '$resolved', expected '$DC_IP'" >&2
  exit 1
fi

# 4. a14-kali has the per-instance kali pubkey installed.
if ! docker exec a14-kali test -s /home/kali/.ssh/authorized_keys; then
  echo "polaris verify: a14-kali /home/kali/.ssh/authorized_keys is missing or empty" >&2
  exit 1
fi

# 4a. Splice-relay credential gate (#707): private key staged on a14-kali
#     and matching pubkey installed on a9-splice. Without both halves the
#     Bunker chain (flags 31-36) is unreachable post-splice. Mode is also
#     checked on the private key — wrong perms invite client refusal at
#     ssh-time, which masquerades as the original P0 symptom.
if ! docker exec a14-kali test -s /home/kali/.ssh/splice_relay; then
  echo "polaris verify: splice_relay private key missing on a14-kali" >&2
  exit 1
fi
splice_mode=$(docker exec a14-kali stat -c '%a' /home/kali/.ssh/splice_relay 2>/dev/null || echo "")
if [[ "$splice_mode" != "600" ]]; then
  echo "polaris verify: splice_relay private key has wrong mode '$splice_mode' (expected 600)" >&2
  exit 1
fi
if ! docker exec a9-splice test -s /root/.ssh/authorized_keys; then
  echo "polaris verify: a9-splice /root/.ssh/authorized_keys is missing or empty" >&2
  exit 1
fi

# 5. a14-kali is NOT on splice-link at range start (the watcher attaches
#    it only after flag 19 is earned). Inspect the container directly
#    with a dot-prefixed Go template so the orchestrator's Jinja
#    placeholder regex does not collide (see comments above).
a14_nets=$(docker inspect a14-kali --format '{{json .NetworkSettings.Networks}}' 2>/dev/null || true)
if echo "$a14_nets" | grep -q '"[a-z0-9_-]*splice-link"'; then
  echo "polaris verify: a14-kali is already on splice-link at boot (should attach only after flag 19)" >&2
  exit 1
fi

# 6. splice watcher service is active.
if ! systemctl is-active --quiet polaris-splice-watcher.service; then
  echo "polaris verify: polaris-splice-watcher.service is not active" >&2
  systemctl status polaris-splice-watcher.service --no-pager >&2 || true
  exit 1
fi
"""

VERIFY_POLARIS_BOOTSTRAP_SCRIPT = (
    _VERIFY_POLARIS_BOOTSTRAP_COMMON
    + """
echo "polaris verify: dc01 -> $resolved, kali key installed, splice gated, watcher active"
exit 0
"""
)

# AWS-only (#1377 slice 5). Extends the shared checks above with the fail-
# closed security controls this slice adds: the DOCKER-USER metadata
# firewall is present, the host STS refresh actually produced a credentials
# file, a14-kali's assumed identity resolves to THIS range's agent role (not
# the shared host role), a minimal Bedrock invocation succeeds through that
# identity, and IMDS is unreachable from inside a14-kali. Every check exits
# non-zero on failure so a single bad SSM command result fails the whole
# step (SetupOrchestrator raises SetupError; see setup_orchestrator.py) --
# there is no warn-and-continue path here.
VERIFY_POLARIS_BOOTSTRAP_SCRIPT_AWS = (
    _VERIFY_POLARIS_BOOTSTRAP_COMMON
    + """
# 7. AWS-only: the DOCKER-USER metadata-drop rule is present.
if ! iptables -C DOCKER-USER -d 169.254.169.254/32 -j DROP 2>/dev/null; then
  echo "polaris verify: DOCKER-USER metadata drop rule is missing" >&2
  exit 1
fi

# 8. AWS-only: the host STS refresh succeeded and a14-kali can see the
#    resulting credentials file through its read-only mount.
if ! docker exec a14-kali test -s /run/shifter-agent/credentials.json; then
  echo "polaris verify: /run/shifter-agent/credentials.json is missing or empty inside a14-kali" >&2
  exit 1
fi

# 9. AWS-only: from inside a14-kali, the assumed-role identity resolves to
#    THIS range's per-range agent role -- not the shared host operations
#    role, and not another range's agent role.
ROLE_ARN="{{ role_arn }}"
AGENT_ROLE_NAME="${ROLE_ARN##*/}"
if [[ -z "$AGENT_ROLE_NAME" ]]; then
  echo "polaris verify: could not derive the agent role name from role_arn" >&2
  exit 1
fi
caller_identity=$(docker exec a14-kali aws sts get-caller-identity --output json 2>/dev/null || true)
if [[ "$caller_identity" != *"$AGENT_ROLE_NAME"* ]]; then
  echo "polaris verify: a14-kali caller identity does not resolve to the per-range agent role" >&2
  exit 1
fi

# 10. AWS-only: a minimal Bedrock invocation succeeds through that identity.
if ! docker exec a14-kali aws bedrock-runtime invoke-model \\
    --model-id "{{ small_model_id }}" \\
    --body '{"anthropic_version":"bedrock-2023-05-31","max_tokens":8,"messages":[{"role":"user","content":"ok"}]}' \\
    --cli-binary-format raw-in-base64-out \\
    --region "{{ region }}" \\
    /tmp/polaris-bedrock-smoke.json >/dev/null 2>&1; then
  echo "polaris verify: Bedrock smoke invocation from a14-kali failed" >&2
  exit 1
fi

# 11. AWS-only: IMDS is unreachable from a14-kali -- the metadata firewall
#     DROPs packets to 169.254.169.254, so a blocked container sees a connection
#     timeout, not an HTTP status. IMDSv2 answers a tokenless GET with 401 even
#     when fully reachable, so "non-2xx == blocked" is a false negative: a
#     participant can still PUT for a token and read the host role's credentials.
#     Probe the IMDSv2 token endpoint the way a participant would (PUT) and treat
#     ANY HTTP response as reachability -- curl exits 0 for 401/403 without
#     --fail; only a connection failure/timeout (curl non-zero) counts as denied.
if docker exec a14-kali curl -s -m 2 -o /dev/null \\
    -X PUT -H 'X-aws-ec2-metadata-token-ttl-seconds: 60' \\
    http://169.254.169.254/latest/api/token 2>/dev/null; then
  echo "polaris verify: IMDS reachable from a14-kali (IMDSv2 token endpoint responded; metadata firewall failed)" >&2
  exit 1
fi

echo "polaris verify: dc01 -> $resolved, kali key installed, splice gated, watcher active, aws agent secured"
exit 0
"""
)
