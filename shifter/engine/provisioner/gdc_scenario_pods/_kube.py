"""Kubernetes client construction and Pod apply/wait helpers for GDC scenario Pods."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import yaml

logger = logging.getLogger(__name__)

_NETWORK_STATUS_ANNOTATION = "k8s.v1.cni.cncf.io/network-status"
_POLL_INTERVAL_SECONDS = 5
_READY_TIMEOUT_SECONDS = 600
_DELETE_TIMEOUT_SECONDS = 300


def _import_kubernetes_modules():
    try:
        from kubernetes import client, config
        from kubernetes.client.exceptions import ApiException
    except ImportError as exc:
        raise RuntimeError("GDC scenario Pod lifecycle requires the kubernetes Python client") from exc

    return client, config, ApiException


def _build_kube_api_client(kubeconfig_yaml: str):
    # Late-bound call to ``gdc_scenario_pods._import_kubernetes_modules`` so
    # test patches applied at the package level still apply here.
    import gdc_scenario_pods as _pkg

    client, config, _ = _pkg._import_kubernetes_modules()
    kubeconfig_dict = yaml.safe_load(kubeconfig_yaml)
    if not isinstance(kubeconfig_dict, dict):
        raise RuntimeError("GDC kubeconfig secret did not decode into a kubeconfig document")

    loader = config.kube_config.KubeConfigLoader(config_dict=kubeconfig_dict)
    configuration = client.Configuration()
    loader.load_and_set(configuration)
    return client.ApiClient(configuration=configuration)


def _apply_pod(core_api, namespace: str, body: dict[str, Any], api_exception) -> None:
    name = body["metadata"]["name"]
    try:
        core_api.create_namespaced_pod(namespace=namespace, body=body)
        logger.info("Created scenario Pod %s/%s", namespace, name)
    except api_exception as exc:
        if exc.status != 409:
            raise
        core_api.patch_namespaced_pod(name=name, namespace=namespace, body=body)
        logger.info("Updated scenario Pod %s/%s", namespace, name)


def _extract_network_status_ip(pod: dict[str, Any], network_name: str, namespace: str) -> str:
    annotations = pod.get("metadata", {}).get("annotations") or {}
    raw_status = annotations.get(_NETWORK_STATUS_ANNOTATION)
    if not raw_status:
        return ""

    try:
        network_status = json.loads(raw_status)
    except json.JSONDecodeError:
        return ""

    expected_names = {network_name, f"{namespace}/{network_name}"}
    for attachment in network_status:
        if not isinstance(attachment, dict):
            continue
        if attachment.get("name") not in expected_names and attachment.get("interface") != "net1":
            continue
        ips = attachment.get("ips") or []
        if ips:
            return str(ips[0]).split("/", 1)[0]
    return ""


def _wait_for_pod_ready(
    core_api,
    namespace: str,
    pod_name: str,
    expected_ip: str,
    network_name: str,
    api_exception,
) -> None:
    deadline = time.monotonic() + _READY_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        try:
            pod = core_api.read_namespaced_pod(name=pod_name, namespace=namespace).to_dict()
        except api_exception as exc:
            if exc.status == 404:
                time.sleep(_POLL_INTERVAL_SECONDS)
                continue
            raise

        phase = str(((pod.get("status") or {}).get("phase")) or "").lower()
        assigned_ip = _extract_network_status_ip(pod, network_name, namespace)
        if phase == "running" and assigned_ip == expected_ip:
            return
        if phase == "failed":
            raise RuntimeError(f"Scenario Pod {namespace}/{pod_name} entered phase=Failed")
        time.sleep(_POLL_INTERVAL_SECONDS)

    raise RuntimeError(f"Timed out waiting for scenario Pod {namespace}/{pod_name} to become ready")


def _wait_for_pod_deleted(core_api, namespace: str, pod_name: str, api_exception) -> None:
    deadline = time.monotonic() + _DELETE_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        try:
            core_api.read_namespaced_pod(name=pod_name, namespace=namespace)
        except api_exception as exc:
            if exc.status == 404:
                return
            raise
        time.sleep(_POLL_INTERVAL_SECONDS)

    raise RuntimeError(f"Timed out waiting for scenario Pod {namespace}/{pod_name} to delete")


def _is_pod_ready(
    core_api,
    *,
    namespace: str,
    pod_name: str,
    expected_ip: str,
    network_name: str,
    api_exception,
) -> bool:
    try:
        pod = core_api.read_namespaced_pod(name=pod_name, namespace=namespace).to_dict()
    except api_exception as exc:
        if exc.status == 404:
            return False
        raise

    phase = str(((pod.get("status") or {}).get("phase")) or "").lower()
    assigned_ip = _extract_network_status_ip(pod, network_name, namespace)
    return phase == "running" and assigned_ip == expected_ip
