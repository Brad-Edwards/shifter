"""Bounded metadata pagination shared by credential management services."""


def credential_page(queryset, *, offset: int = 0, limit: int = 50, project=lambda value: value):
    """Fetch at most one page; no verifier or raw credential belongs in a page."""
    if type(offset) is not int or type(limit) is not int or not 0 <= offset <= 100000 or not 1 <= limit <= 200:
        raise ValueError("Invalid credential pagination")
    count = queryset.count()
    return {
        "count": count,
        "next_offset": offset + limit if offset + limit < count else None,
        "results": [project(item) for item in queryset[offset : offset + limit]],
    }
