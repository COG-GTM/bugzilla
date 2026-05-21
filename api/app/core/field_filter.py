"""Reusable ``include_fields`` / ``exclude_fields`` filtering utility.

Mirrors the ``filter`` and ``filter_wants`` helpers from
``Bugzilla/WebService/Util.pm``.  Applied to response dicts before
serialisation so any endpoint can opt in.
"""

from typing import Any


def filter_fields(
    data: dict[str, Any],
    include_fields: list[str] | None = None,
    exclude_fields: list[str] | None = None,
) -> dict[str, Any]:
    """Return a copy of *data* with only the requested fields.

    * If ``include_fields`` is provided, only those keys are kept.
    * If ``exclude_fields`` is provided, those keys are removed.
    * ``include_fields`` takes precedence when both are supplied.
    """
    if include_fields is not None:
        allowed = set(include_fields)
        return {k: v for k, v in data.items() if k in allowed}
    if exclude_fields is not None:
        blocked = set(exclude_fields)
        return {k: v for k, v in data.items() if k not in blocked}
    return data


def filter_wants(
    include_fields: list[str] | None,
    exclude_fields: list[str] | None,
    field: str,
) -> bool:
    """Return ``True`` if *field* should appear in the response."""
    if include_fields is not None:
        return field in include_fields
    if exclude_fields is not None:
        return field not in exclude_fields
    return True
