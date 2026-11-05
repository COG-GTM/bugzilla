"""Normalise volatile values in Bugzilla REST payloads.

This is the ONLY place where parity tests are allowed to blur data: everything
that legitimately differs between two runs or two engines (timestamps, login
tokens, auto-increment ids, per-run identifiers, ETags) is replaced with a
stable placeholder here, so that the remaining JSON can be compared
byte-for-byte between the Perl and the Python implementation.
"""

from __future__ import annotations

import json
import re
from typing import Any

ISO_DATETIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
SQL_DATETIME = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
LOGIN_TOKEN = re.compile(r"^\d+-[A-Za-z0-9]+$")

# Keys whose integer values are database auto-increment ids.
ID_KEYS = {
    "id", "ids", "bug_id", "attachment_id", "comment_id", "dupe_of", "depends_on",
    "blocks", "creator_id", "attacher_id", "setter_id", "requestee_id", "type_id",
    "flag_id", "field_id", "product_id", "component_id", "user_id",
}
# Keys that always hold a timestamp.
TIME_KEYS = {
    "creation_time", "last_change_time", "when", "time", "web_time", "web_time_utc",
    "db_time", "last_audit_time", "modification_time", "bug_when", "last_seen_date",
    "creation_ts", "delta_ts",
}
# Keys whose values are secrets or opaque per-session values.
SECRET_KEYS = {"token", "api_key", "etag", "update_token"}
# Lists of scalars whose order is not part of the contract.
UNORDERED_LIST_KEYS = {"cc", "groups", "keywords", "see_also", "alias", "depends_on", "blocks", "ids"}

ID_LIST = re.compile(r"^\d+(,\s*\d+)*$")

ID_PLACEHOLDER = "<id>"
TIME_PLACEHOLDER = "<datetime>"
TOKEN_PLACEHOLDER = "<token>"
RUN_PLACEHOLDER = "<run>"


def normalize(value: Any, run_id: str | None = None, *, key: str | None = None) -> Any:
    """Return a copy of ``value`` with volatile data replaced by placeholders."""
    if isinstance(value, dict):
        # Bug.comments / Bug.attachments key their maps by id ("bugs": {"17": ...}).
        return {
            (ID_PLACEHOLDER if isinstance(k, str) and k.isdigit() else k): normalize(v, run_id, key=k)
            for k, v in sorted(value.items())
        }

    if isinstance(value, list):
        items = [normalize(v, run_id, key=key) for v in value]
        if key in UNORDERED_LIST_KEYS and all(not isinstance(i, (dict, list)) for i in items):
            items = sorted(items, key=json.dumps)
        if key == "changes":
            # Bug.history lists the fields changed in one update in hash order.
            items = sorted(items, key=lambda i: json.dumps(i, sort_keys=True))
        return items

    if isinstance(value, bool) or value is None:
        return value

    if isinstance(value, int):
        if key in ID_KEYS:
            return ID_PLACEHOLDER
        return value

    if isinstance(value, str):
        if key in TIME_KEYS or ISO_DATETIME.match(value) or SQL_DATETIME.match(value):
            return TIME_PLACEHOLDER
        if key in SECRET_KEYS or (key == "token" and LOGIN_TOKEN.match(value)):
            return TOKEN_PLACEHOLDER
        if key in ("added", "removed") and ID_LIST.match(value):
            # Bug.update "changes" and Bug.history report ids as strings ("17" or "17, 18").
            return ID_PLACEHOLDER
        if run_id:
            value = value.replace(run_id, RUN_PLACEHOLDER)
        if key == "message":
            # Error messages embed ids chosen at run time ("Bug #1234 does not exist",
            # "Bug 17 has already taken the alias ...").
            value = re.sub(r"\d+", "<n>", value)
        return value

    return value


def normalize_response(status_code: int, body: Any, run_id: str | None = None) -> dict[str, Any]:
    """Shape used for golden snapshots: HTTP status plus normalised body."""
    return {"status": status_code, "body": normalize(body, run_id)}


def dumps(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
