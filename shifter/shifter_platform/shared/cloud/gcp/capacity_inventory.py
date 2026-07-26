"""GCP read-only capacity inventory via Cloud Monitoring quota metrics (PLAT-201).

GCP publishes both sides of a quota as Cloud Monitoring time series
(``serviceruntime.googleapis.com/quota/limit`` and
``.../quota/allocation/usage``), so one client answers both halves of a
reading. The defensive contract matches the AWS adapter deliberately -- a
partition should behave the same way whichever provider backs it:

- validate the series shape before any value reaches policy;
- treat an absent point as unmeasured, never as zero usage;
- carry the point's own timestamp so policy judges real freshness;
- degrade to a bounded reason code instead of raising into pre-spinup;
- keep project identifiers and provider error text out of the logs.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any

from shared.capacity import (
    CapacityReasonCode,
    MeasurementSource,
    MetricObservation,
    ObservationResult,
)

if TYPE_CHECKING:
    from shared.capacity import CapacityMetricSpec, PartitionRef

logger = logging.getLogger(__name__)


class _DefaultMonitoringClient:
    """Thin wrapper over the Cloud Monitoring time-series API.

    Imported lazily through the shared GCP helper so AWS-only deployments never
    require the Google libraries at import time.
    """

    def list_time_series(self, *, metric_type: str, project: str, region: str) -> list[Any]:
        from shared.cloud.gcp.base import import_google_module

        monitoring = import_google_module("google.cloud.monitoring_v3")
        client = monitoring.MetricServiceClient()
        request = {
            "name": f"projects/{project}",
            "filter": f'metric.type = "{metric_type}" AND resource.labels.location = "{region}"',
            "view": monitoring.ListTimeSeriesRequest.TimeSeriesView.FULL,
        }
        return list(client.list_time_series(request=request))


def _series_point(series: Any) -> tuple[float, datetime] | None:
    """Extract (value, timestamp) from one time series, validating the shape.

    Accepts both the lightweight shape used by the adapter's tests and the real
    ``TimeSeries`` protobuf, whose newest point carries the value in a typed
    union and the timestamp on its interval.
    """
    value = getattr(series, "value", None)
    when = getattr(series, "when", None)

    if value is None or when is None:
        points = getattr(series, "points", None)
        if not points:
            return None
        point = points[0]
        typed = getattr(point, "value", None)
        value = getattr(typed, "int64_value", None) or getattr(typed, "double_value", None)
        interval = getattr(point, "interval", None)
        end_time = getattr(interval, "end_time", None)
        when = end_time.replace(tzinfo=end_time.tzinfo) if isinstance(end_time, datetime) else None

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    numeric = float(value)
    if numeric < 0 or not isinstance(when, datetime):
        return None
    return numeric, when


class GCPCapacityInventory:
    """Read-only capacity observations from GCP Cloud Monitoring."""

    def __init__(self, monitoring_client: Any = None) -> None:
        self._client = monitoring_client or _DefaultMonitoringClient()

    def observe(self, spec: CapacityMetricSpec, partition: PartitionRef) -> ObservationResult:
        """Return the observed limit and usage for ``spec`` in ``partition``.

        Never raises: every failure returns an unmeasured result with a bounded
        reason code.
        """
        ref = spec.provider_ref
        if ref is None or not ref.limit_ref or not ref.usage_ref:
            return ObservationResult(reason_code=CapacityReasonCode.METRIC_UNSUPPORTED)

        limit = self._read(ref.limit_ref, spec, partition)
        if limit is None:
            return ObservationResult(reason_code=CapacityReasonCode.MEASUREMENT_UNAVAILABLE)

        usage = self._read(ref.usage_ref, spec, partition)
        if usage is None:
            return ObservationResult(reason_code=CapacityReasonCode.MEASUREMENT_UNAVAILABLE)

        return ObservationResult(
            observation=MetricObservation(
                limit=limit[0],
                usage=usage[0],
                observed_at=usage[1],
                source=MeasurementSource.PROVIDER_PROBE,
            )
        )

    def _read(
        self,
        metric_type: str,
        spec: CapacityMetricSpec,
        partition: PartitionRef,
    ) -> tuple[float, datetime] | None:
        """Read one metric type's newest point, or ``None`` when unmeasurable."""
        try:
            series = self._client.list_time_series(
                metric_type=metric_type,
                project=partition.account,
                region=partition.region,
            )
        except Exception:
            # Bounded: the project id and provider error text are omitted.
            logger.warning("capacity: monitoring read failed for metric %s in %s", spec.name, partition.name)
            return None
        if not isinstance(series, list) or not series:
            return None
        return _series_point(series[0])
