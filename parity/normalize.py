"""Normalisation of volatile values in Bugzilla REST responses.

The parity suite compares the *live* response of one backend (Perl or Python)
against expectations written from the Perl behaviour. Timestamps, login tokens
and auto-increment ids differ between runs and deployments, so they are
replaced with placeholders in exactly one place: here. Everything else is
compared verbatim (JSON shape and values).
"""
import re
from datetime import datetime

DATETIME_RE = re.compile(r"\A\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])T([01]\d|2[0-3]):[0-5]\d:[0-5]\dZ\Z")
TOKEN_RE = re.compile(r"\A\d+-[A-Za-z0-9]+\Z")



def is_datetime(value):
    """True for a real calendar instant in Bugzilla's REST format (rejects e.g. 2023-02-29)."""
    if not (isinstance(value, str) and DATETIME_RE.match(value)):
        return False
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return False
    return True


DATETIME = "<datetime>"
TOKEN = "<token>"
ID = "<id>"

DATETIME_KEYS = {
    "creation_time", "last_change_time", "time", "when", "web_time", "web_time_utc",
    "db_time", "last_audit_time", "last_seen_date",
}
TOKEN_KEYS = {"token"}
ID_KEYS = {
    "id", "ids", "bug_id", "attachment_id", "comment_id", "dupe_of", "blocks", "depends_on",
    "creator_id", "assigned_to_id", "product_id", "component_id",
}


class NormalizeError(AssertionError):
    pass


def _norm_id(value):
    if value is None:
        return None
    if isinstance(value, bool):
        raise NormalizeError(f"id field holds a boolean: {value!r}")
    if isinstance(value, int):
        return ID
    if isinstance(value, list):
        return [_norm_id(v) for v in value]
    raise NormalizeError(f"id field holds a non-integer: {value!r}")


def normalize(obj, key=None, *, id_keys=ID_KEYS, numeric_dict_keys=True):
    """Return a copy of ``obj`` with volatile values replaced by placeholders.

    * values under DATETIME_KEYS must look like ``YYYY-MM-DDTHH:MM:SSZ`` -> ``<datetime>``
    * values under TOKEN_KEYS must look like ``<userid>-<cookie>`` -> ``<token>``
    * integer values (or lists of integers) under ``id_keys`` -> ``<id>``
    * dict keys that are purely numeric (``{"bugs": {"22": ...}}``) -> ``<id>`` when
      ``numeric_dict_keys`` is true.

    Shape errors (a timestamp that is not ISO-8601 UTC, an id that is not an
    integer) raise ``NormalizeError`` so they fail the test instead of being hidden.
    """
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            nk = k
            if numeric_dict_keys and isinstance(k, str) and k.isdigit():
                nk = ID
                if nk in out:
                    raise NormalizeError("more than one numeric key in a dict; normalise each entry separately")
            out[nk] = normalize(v, k, id_keys=id_keys, numeric_dict_keys=numeric_dict_keys)
        return out
    if isinstance(obj, list):
        if key in id_keys:
            return _norm_id(obj)
        return [normalize(v, key, id_keys=id_keys, numeric_dict_keys=numeric_dict_keys) for v in obj]
    if key in DATETIME_KEYS and obj is not None:
        if not is_datetime(obj):
            raise NormalizeError(f"{key}={obj!r} is not a YYYY-MM-DDTHH:MM:SSZ timestamp")
        return DATETIME
    if key in TOKEN_KEYS and obj is not None:
        if not (isinstance(obj, str) and TOKEN_RE.match(obj)):
            raise NormalizeError(f"{key}=<redacted> is not a <userid>-<cookie> token")
        return TOKEN
    if key in id_keys:
        return _norm_id(obj)
    return obj


def shape(obj):
    """Type skeleton of a JSON value: dicts keep keys, lists keep the first element's shape."""
    if isinstance(obj, dict):
        return {k: shape(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [shape(obj[0])] if obj else []
    if obj is None:
        return "null"
    if isinstance(obj, bool):
        return "bool"
    if isinstance(obj, int):
        return "int"
    if isinstance(obj, float):
        return "float"
    return "str"


def sorted_by(items, key):
    return sorted(items, key=lambda x: x[key])
