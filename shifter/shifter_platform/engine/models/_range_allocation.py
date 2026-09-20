"""Range allocation methods kept separate from the persistent range schema."""

from __future__ import annotations

from typing import Any

from django.db import transaction


class RangeAllocationMixin:
    """Provide serialized subnet and VPN-pool allocation to the Range model."""

    SUBNET_INDEX_MIN = 1
    SUBNET_INDEX_MAX = 4048

    @classmethod
    def allocate_subnet_index(cls: type[Any]) -> int:
        """Allocate the first free subnet index while holding the range-table lock."""
        from django.db import connection

        with transaction.atomic():
            if connection.vendor != "sqlite":
                with connection.cursor() as cursor:
                    cursor.execute("LOCK TABLE mission_control_range IN EXCLUSIVE MODE")
            used_indices = set(
                cls.objects.exclude(status__in=[cls.Status.DESTROYED, cls.Status.FAILED])
                .exclude(subnet_index__isnull=True)
                .values_list("subnet_index", flat=True)
            )
            for index in range(cls.SUBNET_INDEX_MIN, cls.SUBNET_INDEX_MAX + 1):
                if index not in used_indices:
                    return index
            raise ValueError(
                f"No subnet indices available. Maximum {cls.SUBNET_INDEX_MAX} "
                "concurrent ranges supported. Destroy some ranges first."
            )

    @classmethod
    def allocate_vpn_gateway_slot(cls: type[Any]) -> int:
        """Reserve the first free OpenVPN gateway slot while holding the range-table lock."""
        from django.conf import settings
        from django.db import connection

        pool_size = int(getattr(settings, "VPN_GATEWAY_POOL_SIZE", 0))
        if pool_size <= 0:
            raise ValueError("VPN_GATEWAY_POOL_SIZE must be a positive integer to provision OpenVPN ranges")
        with transaction.atomic():
            if connection.vendor != "sqlite":
                with connection.cursor() as cursor:
                    cursor.execute("LOCK TABLE mission_control_range IN EXCLUSIVE MODE")
            used_slots = set(
                cls.objects.exclude(status__in=[cls.Status.DESTROYED, cls.Status.FAILED])
                .exclude(vpn_gateway_pool_slot__isnull=True)
                .values_list("vpn_gateway_pool_slot", flat=True)
            )
            for slot in range(pool_size):
                if slot not in used_slots:
                    return slot
            raise ValueError(
                f"OpenVPN gateway pool exhausted. Maximum {pool_size} concurrent OpenVPN "
                "ranges supported; increase VPN_GATEWAY_POOL_SIZE (and the Terraform pool) "
                "or destroy some ranges first."
            )
