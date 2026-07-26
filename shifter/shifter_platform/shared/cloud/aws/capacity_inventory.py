"""AWS read-only capacity inventory: Service Quotas limits and CloudWatch usage (PLAT-201).

This adapter is the only place raw AWS quota and usage payloads exist. Three
properties matter more than completeness here:

- **Fail soft, never silent.** A throttle, timeout, denial, or malformed payload
  degrades to "unmeasured" with a bounded reason code. It never raises into the
  pre-spinup path and never substitutes a default number, because a fabricated
  zero-usage reading would read as free headroom and admit an event that cannot
  actually run.
- **Absent is not zero.** CloudWatch legitimately returns no datapoint for a
  metric nobody has exercised yet. That is an unmeasured metric, not an idle
  one, so it is reported as unavailable rather than as ``usage=0``.
- **The provider's timestamp is the freshness signal.** ``observed_at`` is the
  datapoint's own timestamp, never wall-clock at call time, so a stale metric
  stream cannot masquerade as a current reading.

Cross-account partitions are read through a constrained, per-partition
assumed role -- the portal/scheduler identity is never widened to reach another
account's quota surface.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from botocore.config import Config
from django.conf import settings

from shared.capacity import (
    CapacityReasonCode,
    MeasurementSource,
    MetricObservation,
    ObservationResult,
)

if TYPE_CHECKING:
    from shared.capacity import CapacityMetricSpec, PartitionRef

logger = logging.getLogger(__name__)

# Capacity reads sit on the pre-spinup path, so a stalled endpoint must fail
# fast rather than hold up an event's provisioning window (the #929 precedent).
_CONNECT_TIMEOUT_SECONDS = 2
_READ_TIMEOUT_SECONDS = 5
_MAX_ATTEMPTS = 2

#: Window of usage datapoints requested. Wide enough that a sparse metric still
#: produces a point, narrow enough that the newest point is meaningfully recent.
_USAGE_WINDOW_SECONDS = 900

_DEFAULT_ROLE_NAME = "shifter-capacity-read"


def _client_config() -> Config:
    """Bounded botocore config for capacity reads."""
    return Config(
        connect_timeout=_CONNECT_TIMEOUT_SECONDS,
        read_timeout=_READ_TIMEOUT_SECONDS,
        retries={"max_attempts": _MAX_ATTEMPTS, "mode": "standard"},
    )


def _default_client_factory(service: str, *, region: str, credentials: dict[str, str] | None = None) -> Any:
    """Build a boto3 client, optionally from assumed-role credentials."""
    import boto3

    kwargs: dict[str, Any] = {"region_name": region, "config": _client_config()}
    if credentials is not None:
        kwargs["aws_access_key_id"] = credentials["AccessKeyId"]
        kwargs["aws_secret_access_key"] = credentials["SecretAccessKey"]
        kwargs["aws_session_token"] = credentials["SessionToken"]
    return boto3.client(service, **kwargs)


def _coerce_positive_float(value: object) -> float | None:
    """Return ``value`` as a non-negative float, or ``None`` when it is not one.

    Rejects booleans (which are ``int`` in Python) and negatives so a malformed
    payload cannot become a usable limit.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    numeric = float(value)
    if numeric < 0:
        return None
    return numeric


class AWSCapacityInventory:
    """Read-only capacity observations from AWS Service Quotas and CloudWatch."""

    def __init__(
        self,
        client_factory: Any = None,
        home_account: str | None = None,
    ) -> None:
        self._client_factory = client_factory or _default_client_factory
        self._home_account = home_account if home_account is not None else str(getattr(settings, "AWS_ACCOUNT_ID", ""))

    def observe(self, spec: CapacityMetricSpec, partition: PartitionRef) -> ObservationResult:
        """Return the observed limit and usage for ``spec`` in ``partition``.

        Never raises: every failure path returns an unmeasured result carrying a
        bounded reason code.
        """
        if spec.provider_ref is None or not spec.provider_ref.limit_ref:
            return ObservationResult(reason_code=CapacityReasonCode.METRIC_UNSUPPORTED)

        try:
            credentials = self._credentials_for(partition)
        except Exception:
            # Bounded: the role ARN and provider error are deliberately omitted.
            logger.warning(
                "capacity: could not assume read role for partition %s metric %s",
                partition.name,
                spec.name,
            )
            return ObservationResult(reason_code=CapacityReasonCode.MEASUREMENT_UNAVAILABLE)

        limit = self._read_limit(spec, partition, credentials)
        if limit is None:
            return ObservationResult(reason_code=CapacityReasonCode.MEASUREMENT_UNAVAILABLE)

        usage = self._read_usage(spec, partition, credentials)
        if usage is None:
            return ObservationResult(reason_code=CapacityReasonCode.MEASUREMENT_UNAVAILABLE)

        usage_value, observed_at = usage
        return ObservationResult(
            observation=MetricObservation(
                limit=limit,
                usage=usage_value,
                observed_at=observed_at,
                source=MeasurementSource.PROVIDER_PROBE,
            )
        )

    def _credentials_for(self, partition: PartitionRef) -> dict[str, str] | None:
        """Assume the partition's read role when it lives in another account."""
        if not partition.account or partition.account == self._home_account:
            return None
        role_name = str(getattr(settings, "CAPACITY_INVENTORY_ROLE_NAME", _DEFAULT_ROLE_NAME))
        sts = self._client_factory("sts", region=partition.region)
        response = sts.assume_role(
            RoleArn=f"arn:aws:iam::{partition.account}:role/{role_name}",
            RoleSessionName="shifter-capacity-read",
        )
        return dict(response["Credentials"])

    def _read_limit(
        self,
        spec: CapacityMetricSpec,
        partition: PartitionRef,
        credentials: dict[str, str] | None,
    ) -> float | None:
        """Read the quota limit, returning ``None`` when it cannot be established."""
        assert spec.provider_ref is not None  # guarded by observe()
        service_code, _, quota_code = spec.provider_ref.limit_ref.partition("/")
        if not service_code or not quota_code:
            return None
        try:
            client = self._client_factory("service-quotas", region=partition.region, credentials=credentials)
            response = client.get_service_quota(ServiceCode=service_code, QuotaCode=quota_code)
        except Exception:
            logger.warning("capacity: quota read failed for metric %s in %s", spec.name, partition.name)
            return None
        if not isinstance(response, dict):
            return None
        quota = response.get("Quota")
        if not isinstance(quota, dict):
            return None
        return _coerce_positive_float(quota.get("Value"))

    def _read_usage(
        self,
        spec: CapacityMetricSpec,
        partition: PartitionRef,
        credentials: dict[str, str] | None,
    ) -> tuple[float, datetime] | None:
        """Read current usage plus the datapoint's own timestamp.

        Returns ``None`` when no datapoint exists -- an unexercised metric is
        unmeasured, not idle.
        """
        assert spec.provider_ref is not None  # guarded by observe()
        namespace, _, metric_name = spec.provider_ref.usage_ref.rpartition("/")
        if not namespace or not metric_name:
            return None
        try:
            client = self._client_factory("cloudwatch", region=partition.region, credentials=credentials)
            response = client.get_metric_data(**self._usage_query(namespace, metric_name))
        except Exception:
            logger.warning("capacity: usage read failed for metric %s in %s", spec.name, partition.name)
            return None
        return self._first_datapoint(response)

    @staticmethod
    def _usage_query(namespace: str, metric_name: str) -> dict[str, Any]:
        """Build the CloudWatch GetMetricData request for one usage metric."""
        from django.utils import timezone

        end = timezone.now()
        return {
            "MetricDataQueries": [
                {
                    "Id": "usage",
                    "MetricStat": {
                        "Metric": {"Namespace": namespace, "MetricName": metric_name},
                        "Period": _USAGE_WINDOW_SECONDS,
                        "Stat": "Maximum",
                    },
                    "ReturnData": True,
                }
            ],
            "StartTime": end - timedelta(seconds=_USAGE_WINDOW_SECONDS),
            "EndTime": end,
            "ScanBy": "TimestampDescending",
        }

    @staticmethod
    def _first_datapoint(response: object) -> tuple[float, datetime] | None:
        """Extract the newest (value, timestamp) pair, validating the shape."""
        if not isinstance(response, dict):
            return None
        results = response.get("MetricDataResults")
        if not isinstance(results, list) or not results:
            return None
        first = results[0]
        if not isinstance(first, dict):
            return None
        values = first.get("Values")
        timestamps = first.get("Timestamps")
        if not isinstance(values, list) or not isinstance(timestamps, list):
            return None
        if not values or not timestamps:
            return None
        value = _coerce_positive_float(values[0])
        stamp = timestamps[0]
        if value is None or not isinstance(stamp, datetime):
            return None
        return value, stamp
