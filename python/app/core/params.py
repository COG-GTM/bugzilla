"""Request parameters the way Bugzilla::WebService::Server::REST builds them:
query string plus, for non-GET requests, the JSON body, with query values
winning. Also include_fields / exclude_fields filtering (WebService::Util)."""
import json
import re
from datetime import datetime, timezone

from fastapi import Request

from .errors import illegal_date, invalid_json


async def request_params(request: Request) -> dict:
    params = {}
    if request.method != "GET":
        raw = await request.body()
        request.state.raw_body = raw
        if raw.strip():
            try:
                body = json.loads(raw)
            except ValueError as exc:
                raise invalid_json(exc)
            if isinstance(body, dict):
                params.update(body)
    for key in request.query_params:
        values = request.query_params.getlist(key)
        params[key] = values if len(values) > 1 else values[0]
    return params


def as_list(value):
    """Scalars, lists and comma-separated strings all become a list."""
    if value is None:
        return []
    items = value if isinstance(value, (list, tuple)) else [value]
    out = []
    for item in items:
        out.extend(str(item).split(",") if isinstance(item, str) else [item])
    return [i for i in out if i != ""]


class FieldFilter:
    """filter_wants(): a field is returned if include_fields is empty or names
    it, and exclude_fields does not name it."""

    def __init__(self, params):
        self.include = set(as_list(params.get("include_fields")))
        self.exclude = set(as_list(params.get("exclude_fields")))

    def wants(self, field):
        if field in self.exclude or ("_all" in self.exclude and field not in self.include):
            return False
        return not self.include or field in self.include or bool(self.include & {"_all", "_default"})

    def apply(self, item: dict, detail_of=None):
        """``detail_of`` maps e.g. cc_detail -> cc: Perl emits the detail
        object whenever the plain field is wanted."""
        detail_of = detail_of or {}
        return {k: v for k, v in item.items()
                if self.wants(k) or (k in detail_of and k not in self.exclude and self.wants(detail_of[k]))}


_DATE = re.compile(r"^(\d{4})-(\d\d)-(\d\d)(?:[T ](\d\d):(\d\d)(?::(\d\d))?)?(Z|[+-]\d\d:?\d\d)?$")


def parse_datetime(value):
    """A REST date parameter as a naive UTC datetime; error 56 if unparsable."""
    match = _DATE.match(str(value).strip())
    if not match:
        raise illegal_date(value)
    text = str(value).strip().replace(" ", "T").replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        raise illegal_date(value)
    if parsed.tzinfo:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def iso(value):
    """Datetimes are rendered as UTC ISO 8601 with a Z suffix."""
    return value.strftime("%Y-%m-%dT%H:%M:%SZ") if value else None
