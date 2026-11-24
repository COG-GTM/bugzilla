"""The only place where volatile values in REST responses are normalised.

Tests compare ``normalize(response_json)`` with literal expected JSON, so
both the shape and every non-volatile value are checked.

Rules:
- ISO timestamps become ``<TIMESTAMP>``.
- Login tokens and bug ``update_token`` values become ``<TOKEN>``.
- The attachment id in "Created attachment N" comment text becomes ``<ID>``.
- The changes inside one history entry are sorted by ``field_name``: Perl
  returns them in an order that varies from one update to the next.
- Auto-increment ids become ``<ID>``. Bug ids that the test names through
  ``bugs={bug_id: "A"}`` become ``<BUG:A>`` instead, wherever a bug id can
  appear (id fields, dependency lists, object keys, change strings), so that
  references between bugs are still compared.
"""
import re

TIMESTAMP = "<TIMESTAMP>"
TOKEN = "<TOKEN>"
ID = "<ID>"

_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_INT_RE = re.compile(r"^\d+$")
# Bugzilla writes the new attachment id into the comment it adds.
_ATTACHMENT_TEXT_RE = re.compile(r"^Created attachment \d+")

TOKEN_KEYS = {"token", "update_token"}
ID_KEYS = {"id", "bug_id", "attachment_id", "dupe_of"}
ID_LIST_KEYS = {"ids", "depends_on", "blocks"}
# Fields whose "added"/"removed" change strings are comma-separated bug ids.
BUG_REF_FIELDS = {"depends_on", "blocks", "dependson", "blocked", "dupe_of"}


def _bug_ref(value, bugs):
    if isinstance(value, bool) or not isinstance(value, int):
        return value
    return f"<BUG:{bugs[value]}>" if value in bugs else ID


def _bug_ref_string(text, bugs):
    parts = [p.strip() for p in text.split(",")] if text else []
    return ", ".join(_bug_ref(int(p), bugs) if _INT_RE.match(p) else p for p in parts)


def _change(change, bugs):
    return {k: _bug_ref_string(v, bugs) if k in ("added", "removed") and isinstance(v, str) else normalize(v, bugs)
            for k, v in change.items()}


def normalize(obj, bugs=None):
    bugs = bugs or {}
    if isinstance(obj, list):
        items = [normalize(item, bugs) for item in obj]
        if items and all(isinstance(i, dict) and "field_name" in i for i in items):
            items.sort(key=lambda i: i["field_name"])
        return items
    if isinstance(obj, str):
        if _TIMESTAMP_RE.match(obj):
            return TIMESTAMP
        return _ATTACHMENT_TEXT_RE.sub(f"Created attachment {ID}", obj)
    if not isinstance(obj, dict):
        return obj

    if obj.get("field_name") in BUG_REF_FIELDS:
        return _change(obj, bugs)

    out = {}
    for key, value in obj.items():
        # Objects keyed by id, e.g. {"bugs": {"12": {...}}}.
        new_key = _bug_ref(int(key), bugs) if _INT_RE.match(key) else key
        if key in TOKEN_KEYS and isinstance(value, str):
            out[new_key] = TOKEN
        elif key in ID_KEYS:
            out[new_key] = _bug_ref(value, bugs)
        elif key in ID_LIST_KEYS and isinstance(value, list):
            out[new_key] = [_bug_ref(v, bugs) for v in value]
        elif key in BUG_REF_FIELDS and isinstance(value, dict):
            out[new_key] = _change(value, bugs)
        else:
            out[new_key] = normalize(value, bugs)
    return out
