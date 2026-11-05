"""Port of Bugzilla::WebService::Bugzilla (version, extensions, timezone, time, last_audit_time, parameters)."""
import os
import re

from sqlalchemy import text

from app.core.auth import User
from app.core.config import settings
from app.core.filter import Filter
from app.core.params import params as bz_params
from app.core.rest import Ctx
from app.core.util import iso_z, now_utc

# Bugzilla::WebService::Bugzilla::PARAMETERS_LOGGED_OUT / PARAMETERS_LOGGED_IN
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
# last_audit_time only accepts class names shaped like Bugzilla::Foo::Bar; anything else is dropped silently.
CLASS_RE = re.compile(r"^Bugzilla(::[a-zA-Z0-9_]+)*$")

# Bugzilla::Install::Util::_extension_paths / extension_code_files: bz_locations()->{extensionsdir}
# plus the package names listed in data/extensions/additional.
EXTENSIONS_DIR = os.environ.get("BZ_EXTENSIONS_DIR", "/var/www/html/extensions")
ADDITIONAL_FILE = os.path.join(os.path.dirname(settings.params_path), "extensions", "additional")
# where `additional` packages (Bugzilla::Extension::Foo -> Bugzilla/Extension/Foo.pm) are looked up
LIB_DIRS = [d for d in os.environ.get("BZ_LIB_DIRS", "").split(":") if d] or [
    os.path.dirname(EXTENSIONS_DIR), os.path.join(os.path.dirname(EXTENSIONS_DIR), "lib")]

_NAME_RE = re.compile(r"^\s*use\s+constant\s+NAME\s*=>\s*(['\"])([^'\"]+)\1", re.MULTILINE)
_VERSION_CONST_RE = re.compile(r"^\s*use\s+constant\s+VERSION\s*=>\s*(\S+?)\s*;", re.MULTILINE)
_VERSION_VAR_RE = re.compile(r"^\s*our\s+\$VERSION\s*=\s*(['\"])([^'\"]*)\1", re.MULTILINE)


def version() -> dict:
    return {"version": settings.version}


def extensions() -> dict:
    retval = {}
    for name, ext_version in _installed_extensions():
        retval[name] = {"version": str(ext_version or 0)}
    return {"extensions": retval}


def _installed_extensions() -> list[tuple[str, str]]:
    """Bugzilla->extensions: every extensions/<X>/ (or <X>.pm) without a `disabled` file, then
    data/extensions/additional packages not already loaded. (version `0` when the package has none)."""
    found: list[tuple[str, str]] = []
    if os.path.isdir(EXTENSIONS_DIR):
        for item in sorted(os.listdir(EXTENSIONS_DIR)):
            if item == "CVS" or item.startswith("."):
                continue
            path = os.path.join(EXTENSIONS_DIR, item)
            if os.path.isdir(path):
                if os.path.exists(os.path.join(path, "disabled")):
                    continue
                sources = [os.path.join(path, f) for f in ("Config.pm", "Extension.pm")]
                sources = [s for s in sources if os.path.exists(s)]
                if not sources:
                    continue
            elif item.endswith(".pm"):
                sources = [path]
            else:
                continue
            name, ext_version = _read_name_and_version(sources)
            if name:
                found.append((name, ext_version))
    if os.path.exists(ADDITIONAL_FILE):
        with open(ADDITIONAL_FILE) as fh:
            for line in fh:
                package = line.strip()
                if not package:
                    continue
                name, ext_version = _read_additional_package(package)
                if all(n != name for n, _ in found):
                    found.append((name, ext_version))
    return found


def _read_additional_package(package: str) -> tuple[str, str]:
    """NAME/VERSION of a package listed in data/extensions/additional, read from its .pm when it can be
    found on the lib path; otherwise the package's last component and version 0."""
    rel = os.path.join(*package.split("::")) + ".pm"
    # like @INC: only the first match is the one Perl loads
    source = next((p for p in (os.path.join(d, rel) for d in LIB_DIRS) if os.path.exists(p)), None)
    name, ext_version = _read_name_and_version([source]) if source else (None, "0")
    return name or package.rsplit("::", 1)[-1], ext_version


def _read_name_and_version(sources: list[str]) -> tuple[str | None, str]:
    name, ext_version = None, "0"
    for src in sources:
        with open(src, encoding="utf-8", errors="replace") as fh:
            code = fh.read()
        if name is None and (m := _NAME_RE.search(code)):
            name = m.group(2)
        if m := _VERSION_VAR_RE.search(code):
            ext_version = m.group(2)
        elif m := _VERSION_CONST_RE.search(code):
            raw = m.group(1)
            ext_version = settings.version if raw == "BUGZILLA_VERSION" else raw.strip("'\"")
    return name, ext_version


def timezone() -> dict:
    return {"timezone": "+0000"}


def time(ctx: Ctx) -> dict:
    db_time = ctx.db.execute(text("SELECT LOCALTIMESTAMP(0)")).scalar()
    now = iso_z(now_utc())
    return {"db_time": iso_z(db_time), "web_time": now, "web_time_utc": now,
            "tz_name": "UTC", "tz_offset": "+0000", "tz_short_name": "UTC"}


def last_audit_time(ctx: Ctx) -> dict:
    # validate(@_, 'class'): a scalar becomes a one-element list; Perl does not split on commas here.
    raw = ctx.params.get("class")
    values = raw if isinstance(raw, list) else ([] if raw is None else [raw])
    classes = [str(c) for c in values if isinstance(c, str) and CLASS_RE.match(c)]
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


def _as_string(value) -> str | None:
    # $self->type('string', $value): undef stays null, everything else is stringified
    if value is None:
        return None
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, list):
        return ",".join(str(v) for v in value)
    return str(value)
