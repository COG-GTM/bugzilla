"""include_fields / exclude_fields filtering (Bugzilla::WebService::Util::filter)."""

from __future__ import annotations

from typing import Any, Iterable

# field -> category (_default is implicit for anything not listed)
EXTRA_FIELDS: dict[str, dict[str, str]] = {
    "bug": {},  # Bug.get fields are all _default except custom fields (_custom)
}


def filter_wants(params: dict, field: str, prefix: str = "", *, types: Iterable[str] = ("default",)) -> bool:
    include = _as_list(params.get("include_fields"))
    exclude = _as_list(params.get("exclude_fields"))
    name = f"{prefix}{field}" if prefix else field
    type_tags = {f"_{t}" for t in types} | {"_all"}

    def matches(spec: list[str]) -> bool:
        for s in spec:
            if s == name or s == field or s in type_tags:
                return True
            if prefix and s.startswith(prefix) and s[len(prefix):] == field:
                return True
        return False

    if exclude and matches(exclude):
        return False
    if not include:
        return "default" in types or "all" in types
    if matches(include):
        return True
    # include_fields=_default still returns default fields
    return False


def filter_fields(params: dict, data: dict[str, Any], prefix: str = "", field_types: dict[str, str] | None = None) -> dict[str, Any]:
    out = {}
    for key, value in data.items():
        types = (field_types or {}).get(key, "default")
        if filter_wants(params, key, prefix, types=(types,)):
            out[key] = value
    return out


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    out: list[str] = []
    for v in value:
        out.extend(_as_list(v))
    return out
