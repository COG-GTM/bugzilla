"""Port of Bugzilla/WebService/Bugzilla.pm."""

from __future__ import annotations

import re
from datetime import datetime, timezone

from sqlalchemy import text

from app.core.fields import filter_wants
from app.core.request import Ctx

BUGZILLA_VERSION = "5.2+"  # Bugzilla::Constants::BUGZILLA_VERSION

PARAMETERS_LOGGED_OUT = ["maintainer", "requirelogin"]
PARAMETERS_LOGGED_IN = [
    "allowemailchange", "attachment_base", "commentonchange_resolution", "commentonduplicate", "cookiepath",
    "defaultopsys", "defaultplatform", "defaultpriority", "defaultseverity", "duplicate_or_move_bug_status",
    "emailregexpdesc", "emailsuffix", "letsubmitterchoosemilestone", "letsubmitterchoosepriority", "mailfrom",
    "maintainer", "maxattachmentsize", "maxlocalattachment", "musthavemilestoneonaccept",
    "noresolveonopenblockers", "password_complexity", "rememberlogin", "requirelogin",
    "search_allow_no_criteria", "urlbase", "use_see_also", "useclassification", "usemenuforusers",
    "useqacontact", "usestatuswhiteboard", "usetargetmilestone",
]
CLASS_RE = re.compile(r"^Bugzilla(::[a-zA-Z0-9_]+)*$")


def iso(dt: datetime | None) -> str | None:
    """WebService dateTime type: UTC, second precision, trailing Z."""
    if dt is None:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def version() -> dict:
    return {"version": BUGZILLA_VERSION}


def extensions() -> dict:
    return {"extensions": {}}


def timezone_() -> dict:
    return {"timezone": "+0000"}


def time_(ctx: Ctx) -> dict:
    db_time = ctx.session.execute(text("SELECT LOCALTIMESTAMP(0)")).scalar_one()
    now = datetime.now(timezone.utc)
    return {
        "db_time": iso(db_time),
        "web_time": iso(now),
        "web_time_utc": iso(now),
        "tz_name": "UTC",
        "tz_offset": "+0000",
        "tz_short_name": "UTC",
    }


def last_audit_time(ctx: Ctx) -> dict:
    classes = ctx.params.get("class")
    if isinstance(classes, str):
        classes = [classes]
    classes = [c for c in (classes or []) if CLASS_RE.match(c)]
    sql = "SELECT MAX(at_time) FROM audit_log"
    binds = {f"c{i}": c for i, c in enumerate(classes)}
    if binds:
        sql += " WHERE class IN (" + ", ".join(f":{k}" for k in binds) + ")"
    value = ctx.session.execute(text(sql), binds).scalar()
    return {"last_audit_time": iso(value)}


def parameters(ctx: Ctx) -> dict:
    params = ctx.bz_params
    if ctx.user.in_group("tweakparams"):
        names = list(params)
    elif ctx.user.is_logged_in:
        names = PARAMETERS_LOGGED_IN
    else:
        names = PARAMETERS_LOGGED_OUT
    out = {}
    for name in names:
        if not filter_wants(ctx.params, name):
            continue
        value = params.get(name)
        out[name] = "" if value is None else str(value)
    return {"parameters": out}
