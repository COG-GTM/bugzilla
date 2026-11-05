"""Port of Bugzilla::WebService::Bugzilla."""
import re

from sqlalchemy import text

from app.core.auth import User
from app.core.config import settings
from app.core.filter import Filter
from app.core.params import params as bz_params
from app.core.rest import Ctx
from app.core.util import iso_z, now_utc

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


def version() -> dict:
    return {"version": settings.version}


def extensions() -> dict:
    return {"extensions": {}}


def timezone() -> dict:
    return {"timezone": "+0000"}


def time(ctx: Ctx) -> dict:
    db_time = ctx.db.execute(text("SELECT LOCALTIMESTAMP(0)")).scalar()
    now = iso_z(now_utc())
    return {"db_time": iso_z(db_time), "web_time": now, "web_time_utc": now,
            "tz_name": "UTC", "tz_offset": "+0000", "tz_short_name": "UTC"}


def last_audit_time(ctx: Ctx) -> dict:
    classes = [c for c in ctx.param_list("class") if CLASS_RE.match(str(c))]
    sql = "SELECT MAX(at_time) FROM audit_log"
    binds = {}
    if classes:
        sql += " WHERE class IN (" + ",".join(f":c{i}" for i in range(len(classes))) + ")"
        binds = {f"c{i}": c for i, c in enumerate(classes)}
    return {"last_audit_time": iso_z(ctx.db.execute(text(sql), binds).scalar())}


def parameters(ctx: Ctx) -> dict:
    user: User = ctx.user
    all_params = bz_params()
    if user.in_group("tweakparams"):
        names = list(all_params)
    elif user.is_logged_in:
        names = PARAMETERS_LOGGED_IN
    else:
        names = PARAMETERS_LOGGED_OUT
    f: Filter = ctx.filter
    return {"parameters": {n: _as_string(all_params.get(n)) for n in names if f.wants(n)}}


def _as_string(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, dict)):
        import json
        return json.dumps(value)
    return str(value)
