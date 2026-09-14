"""Operator-only readback of the preparation installation's cloud resources.

No credentials or Secret payloads are read by these APIs. Project and named
resource policy checks cover this installation; organization administrators
retain their existing authority over the tenant and its inherited IAM policies.
"""

import json

from shared.cloud.preparation_cloud_installation import PERMISSIONS, cloud_bindings, role_name, workload_member
from shared.cloud.preparation_installation import PreparationInstallation


class CloudInstallationReader:
    """Bounded reads at fixed Google API origins under the installing operator."""

    def __init__(self, configuration):
        from shared.cloud.gcp.base import import_google_module

        auth = import_google_module("google.auth")
        transport = import_google_module("google.auth.transport.requests")
        credentials, _ = auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        self.session = transport.AuthorizedSession(credentials)
        self.configuration = configuration

    def __call__(self, key):
        method, url, body, params = self._request_spec(key)
        result = self._request(method, url, body, params)
        if key not in {"firewalls", "routes", "routers"}:
            return result
        items = list(result.get("items", []))
        seen = set()
        while result.get("nextPageToken"):
            token = result["nextPageToken"]
            if not isinstance(token, str) or len(token) > 2048 or token in seen or len(seen) >= 16:
                raise ValueError("invalid preparation installation pagination")
            seen.add(token)
            result = self._request(method, url, body, dict(params, pageToken=token))
            items.extend(result.get("items", []))
            if len(items) > 2048:
                raise ValueError("preparation installation resources exceed the readback bound")
        return {"items": items}

    def _request(self, method, url, body, params):
        response = self.session.request(
            method, url, json=body, params=params, timeout=30, allow_redirects=False, stream=True
        )
        try:
            if response.status_code != 200:
                raise ValueError("preparation cloud installation readback failed")
            data = bytearray()
            for chunk in response.iter_content(chunk_size=65536):
                data.extend(chunk)
                if len(data) > 2 * 1024**2:
                    raise ValueError("preparation installation readback exceeds its bound")
            value = json.loads(data)
            if not isinstance(value, dict):
                raise ValueError("invalid preparation cloud installation observation")
            return value
        finally:
            response.close()

    def _request_spec(self, key):
        configuration = self.configuration
        grant = configuration.grant
        project = f"projects/{grant.project_id}"
        region = grant.zone.rsplit("-", 1)[0]
        network = project + "/global/networks/" + grant.subnetwork.rsplit("/", 1)[1]
        compute = "https://compute.googleapis.com/compute/v1/"
        iam = "https://iam.googleapis.com/v1/"
        method, body, params = "GET", None, {}
        if key == "project-policy":
            method, body = "POST", {"options": {"requestedPolicyVersion": 3}}
            url = "https://cloudresourcemanager.googleapis.com/v1/" + project + ":getIamPolicy"
        elif key.startswith("role:"):
            url = iam + role_name(configuration, key.split(":", 1)[1])
        elif key.startswith(("identity:", "identity-policy:")):
            url = iam + project + "/serviceAccounts/" + configuration.service_accounts[key.split(":", 1)[1]]
            if key.startswith("identity-policy:"):
                url += ":getIamPolicy"
                method, body = "POST", {"options": {"requestedPolicyVersion": 3}}
        elif key.startswith("secret-policy:"):
            secret = configuration.controller_secret_ids[key.split(":", 1)[1]]
            url = "https://secretmanager.googleapis.com/v1/" + project + "/secrets/" + secret + ":getIamPolicy"
            params = {"options.requestedPolicyVersion": 3}
        elif key == "cluster":
            url = (
                "https://container.googleapis.com/v1/"
                + project
                + "/locations/"
                + configuration.cluster_location
                + "/clusters/"
                + configuration.cluster_name
            )
        elif key in {"network", "subnetwork", "subnetwork-policy"}:
            url = compute + (network if key == "network" else grant.subnetwork)
            if key == "subnetwork-policy":
                url += "/getIamPolicy"
                params = {"optionsRequestedPolicyVersion": 3}
        elif key in {"firewalls", "routes", "routers"}:
            scope = f"regions/{region}" if key == "routers" else "global"
            url = compute + project + "/" + scope + "/" + key
            params = {"maxResults": 100, "filter": f'network = "https://www.googleapis.com/compute/v1/{network}"'}
        else:
            raise ValueError("unknown preparation installation observation")
        return method, url, body, params


def verify_cloud_installation(configuration: PreparationInstallation, read=None) -> str:
    """Require installed IAM, Workload Identity and isolated preparation networking."""
    reader = read or CloudInstallationReader(configuration)
    for key, permissions in PERMISSIONS.items():
        observed = reader("role:" + key) or {}
        if (
            set(observed.get("includedPermissions", [])) != set(permissions)
            or observed.get("deleted")
            or observed.get("stage") != "GA"
        ):
            raise ValueError("preparation cloud role does not match the installed authority")
    _verify_identities(configuration, reader)
    _verify_network(configuration, reader)
    cluster = reader("cluster") or {}
    if cluster.get("workloadIdentityConfig", {}).get(
        "workloadPool"
    ) != configuration.grant.project_id + ".svc.id.goog" or not (
        cluster.get("networkConfig", {}).get("datapathProvider") == "ADVANCED_DATAPATH"
        or cluster.get("networkPolicy", {}).get("enabled") is True
    ):
        raise ValueError("preparation cluster requires Workload Identity and enforced NetworkPolicy")
    return configuration.digest


def _policy_for(policy, members):
    if not isinstance(policy, dict):
        raise ValueError("preparation IAM policy is unavailable")
    return sorted(
        (member, binding["role"], json.dumps(binding.get("condition"), sort_keys=True))
        for binding in policy.get("bindings", [])
        for member in binding.get("members", [])
        if member in members
    )


def _verify_identities(configuration, read):
    members = {"serviceAccount:" + email for email in configuration.service_accounts.values()}
    expected = []
    for actor, key, condition in cloud_bindings(configuration).values():
        expected.append(
            (
                "serviceAccount:" + configuration.service_accounts[actor],
                role_name(configuration, key),
                json.dumps(condition, sort_keys=True),
            )
        )
    if _policy_for(read("project-policy"), members) != sorted(expected):
        raise ValueError("preparation project IAM differs from the installed role and condition bindings")
    for actor, email in configuration.service_accounts.items():
        observed = read("identity:" + actor) or {}
        policy = read("identity-policy:" + actor) or {}
        if (
            observed.get("email") != email
            or observed.get("disabled")
            or policy.get("bindings")
            != [{"role": "roles/iam.workloadIdentityUser", "members": [workload_member(configuration, actor)]}]
        ):
            raise ValueError("preparation identity or its Workload Identity binding does not match")
    expected_secret = [
        ("serviceAccount:" + configuration.service_accounts["controller"], "roles/secretmanager.secretAccessor", "null")
    ]
    for key in configuration.controller_secret_ids:
        if _policy_for(read("secret-policy:" + key), members) != expected_secret:
            raise ValueError("preparation runtime secret access does not match its named controller grant")
    expected_subnet = sorted(
        ("serviceAccount:" + configuration.service_accounts[actor], role_name(configuration, "subnet_use"), "null")
        for actor in ("builder", "verifier")
    )
    if _policy_for(read("subnetwork-policy"), members) != expected_subnet:
        raise ValueError("preparation subnet use does not match its worker grants")


def _relative(reference):
    return (
        str(reference)
        .removeprefix("https://www.googleapis.com/compute/v1/")
        .removeprefix("https://compute.googleapis.com/compute/v1/")
    )


def _verify_network(configuration, read):
    grant = configuration.grant
    reference = f"projects/{grant.project_id}/global/networks/" + grant.subnetwork.rsplit("/", 1)[1]
    network, subnet = read("network") or {}, read("subnetwork") or {}
    if (
        network.get("autoCreateSubnetworks") is not False
        or network.get("peerings")
        or network.get("routingConfig", {}).get("routingMode") != "REGIONAL"
        or _relative(subnet.get("network")) != reference
        or subnet.get("ipCidrRange") != configuration.network_cidr
        or subnet.get("privateIpGoogleAccess")
        or subnet.get("stackType", "IPV4_ONLY") != "IPV4_ONLY"
        or subnet.get("secondaryIpRanges")
    ):
        raise ValueError("preparation network is not the configured isolated subnet")
    for kind in ("routes", "routers", "firewalls"):
        observation = read(kind)
        if not isinstance(observation, dict):
            raise ValueError("preparation network observation is unavailable")
        items = [item for item in observation.get("items", []) if _relative(item.get("network")) == reference]
        if kind == "routers" and items:
            raise ValueError("preparation network must not have routers or NAT")
        if kind == "routes" and (
            len(items) != 1
            or items[0].get("destRange") != configuration.network_cidr
            or _relative(items[0].get("nextHopNetwork")) != reference
        ):
            raise ValueError("preparation network must have only its local subnet route")
        if kind == "firewalls" and (len(items) != 1 or not _probe_firewall(items[0])):
            raise ValueError("preparation network must have only its contained boot-probe firewall")


def _probe_firewall(item):
    return (
        item.get("direction") == "INGRESS"
        and item.get("priority") == 1000
        and not item.get("disabled")
        and item.get("sourceTags") == ["shifter-preparation"]
        and item.get("targetTags") == ["shifter-preparation"]
        and item.get("allowed") == [{"IPProtocol": "tcp", "ports": ["8080"]}]
        and not any(
            item.get(key)
            for key in ("sourceRanges", "destinationRanges", "sourceServiceAccounts", "targetServiceAccounts", "denied")
        )
    )
