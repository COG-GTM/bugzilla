"""Bug module business logic: the port of Bugzilla/WebService/Bug.pm and of
the parts of Bugzilla/Bug.pm, Search.pm, Comment.pm and Attachment.pm it
uses. Functions take the request session, the current user and the merged
request parameters, and return the JSON-ready result."""
import base64
import re

from sqlalchemy import and_, delete, exists, func, insert, not_, or_, select, update

from app.core.auth import issue_hash_token, load_user, require_login
from app.core.config import param
from app.core.db import db_now, t
from app.core.errors import BugzillaError, param_required
from app.core.fallback import NotPorted
from app.core.params import FieldFilter, as_list, iso, parse_datetime

# Bugzilla::Bug::FIELD_MAP: REST name -> database/activity name.
FIELD_MAP = {
    "blocks": "blocked", "creation_time": "creation_ts", "creator": "reporter", "depends_on": "dependson",
    "dupe_of": "dup_id", "id": "bug_id", "is_confirmed": "everconfirmed", "is_cc_accessible": "cclist_accessible",
    "is_creator_accessible": "reporter_accessible", "last_change_time": "delta_ts", "platform": "rep_platform",
    "severity": "bug_severity", "status": "bug_status", "summary": "short_desc", "url": "bug_file_loc",
    "whiteboard": "status_whiteboard",
}
API_NAME = {db: api for api, db in FIELD_MAP.items()} | {"bug_group": "groups"}
# field_descs used in error messages (template/en/default/global/field-descs.none.tmpl).
FIELD_DESC = {"product": "product", "version": "version", "op_sys": "OS", "rep_platform": "Hardware",
              "priority": "Priority", "bug_severity": "Severity", "resolution": "Resolution", "bug_status": "status"}
DETAIL_OF = {"assigned_to_detail": "assigned_to", "creator_detail": "creator",
             "qa_contact_detail": "qa_contact", "cc_detail": "cc"}
CMT_NORMAL, CMT_DUPE_OF, CMT_HAS_DUPE, CMT_ATTACHMENT_CREATED = 0, 1, 2, 5
MAX_COMMENT_LENGTH = 65535
MAX_SUMMARY_LENGTH = 255
LEGAL_CONTENT_TYPES = ("application", "audio", "image", "message", "model", "multipart", "text", "video")
CONTROL_DEFAULT, CONTROL_MANDATORY = 2, 3
# Perl still serves a request that uses any of these (see step_aside).
SITE_GATES = ("requirelogin", "webservice_email_filter", "strict_isolation", "ssl_redirect", "noresolveonopenblockers")
REQUEST_GATES = ("ids", "login", "Bugzilla_login", "quicksearch")
CREATE_GATES = ("groups", "keywords", "see_also", "flags", "resolution", "estimated_time", "remaining_time",
                "deadline", "work_time")
UPDATE_GATES = ("product", "groups", "keywords", "see_also", "flags", "alias", "reset_assigned_to",
                "reset_qa_contact", "comment_is_private", "work_time")
SEARCH_PARAMS = {"id", "alias", "summary", "whiteboard", "url", "status", "resolution", "product", "component",
                 "version", "op_sys", "platform", "priority", "severity", "target_milestone", "assigned_to",
                 "creator", "reporter", "qa_contact", "creation_time", "last_change_time", "limit", "offset",
                 "include_fields", "exclude_fields", "token", "api_key", "Bugzilla_token", "Bugzilla_api_key"}
TIMETRACKING_FIELDS = ("estimated_time", "remaining_time", "work_time", "deadline")


# ---------------------------------------------------------------- errors

def object_not_found(text):
    return BugzillaError(51, text)


def no_such_user(name):
    return object_not_found(f"There is no user named '{name}'. Either you mis-typed the name or that user "
                            "has not yet registered for a Bugzilla account.")


# ---------------------------------------------------------------- what Perl still serves

def step_aside(session, p, gates=()):
    """Hand the request to Perl (NotPorted) when the site configuration or
    the request uses something this port does not implement. Called first by
    every entry point, before any check or write."""
    if any(_truthy(param(name)) for name in SITE_GATES) or int(param("maxlocalattachment", 0) or 0) > 0:
        raise NotPorted("site parameter")
    f = t.fielddefs
    if session.execute(select(f.c.id).where(f.c.custom == 1).limit(1)).first():
        raise NotPorted("custom fields")
    for name in (*REQUEST_GATES, *gates):
        if name in p:
            raise NotPorted(name)
    if any(str(key).startswith("cf_") for key in p):
        raise NotPorted("custom field")
    if any("." in str(field) for key in ("include_fields", "exclude_fields") for field in as_list(p.get(key))):
        raise NotPorted("nested field selector")


def _step_aside_for_write(session, user, product_id, status_change=False):
    """Writes Perl gates on things this port does not model."""
    gcm, w = t.group_control_map, t.status_workflow
    if session.execute(select(gcm.c.group_id).where(gcm.c.product_id == product_id, gcm.c.canedit != 0)).first():
        raise NotPorted("product edit groups")
    if status_change and session.execute(select(w.c.new_status).where(w.c.require_comment != 0)).first():
        raise NotPorted("workflow comment requirement")


# ---------------------------------------------------------------- lookups

def _truthy(value):
    return str(value).lower() not in ("", "0", "false", "none") if value is not None else False


def _number(value):
    value = float(value or 0)
    return int(value) if value == int(value) else value


def _users(session, ids):
    ids = {i for i in ids if i}
    if not ids:
        return {}
    p = t.profiles
    rows = session.execute(select(p.c.userid, p.c.login_name, p.c.realname).where(p.c.userid.in_(ids)))
    return {r.userid: r for r in rows}


def _user_detail(row):
    return {"id": row.userid, "name": row.login_name, "email": row.login_name, "real_name": row.realname}


def _user_id(session, login, error=no_such_user):
    p = t.profiles
    found = session.execute(select(p.c.userid).where(p.c.login_name == str(login))).scalar()
    if found is None:
        raise error(login)
    return found


def _select_value(session, field, value):
    """_check_select_field: the value must exist in the field's value table."""
    value = "" if value is None else str(value).strip()
    if value == "" and field != "resolution":
        raise BugzillaError(50, f"You must select/enter a {FIELD_DESC[field]}.")
    table = getattr(t, field)
    found = session.execute(select(table.c.value).where(table.c.value == value)).scalar()
    if found is None:
        raise object_not_found(f"There is no {FIELD_DESC[field]} named '{value}'.")
    return found


def _in_product(session, table, column, value, product, what):
    found = session.execute(select(table.c[column], table.c.id).where(
        table.c.product_id == product.id, table.c[column] == str(value))).first()
    if found is None:
        raise object_not_found(f"There is no {what} named '{value}' in the '{product.name}' product.")
    return found


def _aliases(session, bug_id):
    a = t.bugs_aliases
    return list(session.scalars(select(a.c.alias).where(a.c.bug_id == bug_id).order_by(a.c.alias)))


def _bug_groups(session, bug_id):
    m, g = t.bug_group_map, t.groups
    return list(session.scalars(select(g.c.name).join(m, m.c.group_id == g.c.id)
                                .where(m.c.bug_id == bug_id).order_by(g.c.name)))


def _can_see(session, user, bug):
    """Bugzilla::User::can_see_bug."""
    if set(_bug_groups(session, bug.bug_id)) <= user.groups:
        return True
    if not user.id:
        return False
    if bug.assigned_to == user.id or (bug.reporter_accessible and bug.reporter == user.id):
        return True
    if _truthy(param("useqacontact")) and bug.qa_contact == user.id:
        return True
    cc = t.cc
    return bool(bug.cclist_accessible and session.execute(
        select(cc.c.who).where(cc.c.bug_id == bug.bug_id, cc.c.who == user.id)).first())


def check_bug(session, user, raw):
    """Bugzilla::Bug->check: an id or an alias, that exists and that the user may see."""
    raw = str(raw).strip()
    b = t.bugs
    if re.fullmatch(r"\d+", raw):
        bug_id = int(raw)
    else:
        a = t.bugs_aliases
        bug_id = session.execute(select(a.c.bug_id).where(a.c.alias == raw)).scalar()
        if bug_id is None:
            raise BugzillaError(100, f"'{raw}' is not a valid bug number nor an alias to a bug.")
    bug = session.execute(select(b).where(b.c.bug_id == bug_id)).first()
    if bug is None:
        raise BugzillaError(101, f"Bug #{bug_id} does not exist.")
    if not _can_see(session, user, bug):
        # Perl names the bug the way the client did, so an alias does not reveal the id.
        raise BugzillaError(102, f"You are not authorized to access bug #{raw}." + (
            "" if user.id else " To see this bug, you must first log in to an account with the appropriate permissions."))
    return bug


def _is_open(session, status):
    s = t.bug_status
    return bool(session.execute(select(s.c.is_open).where(s.c.value == status)).scalar())


# ---------------------------------------------------------------- bug -> JSON

def _flags(session, bug_id=None, attach_id=None):
    f, ft = t.flags, t.flagtypes
    where = f.c.attach_id == attach_id if attach_id else and_(f.c.bug_id == bug_id, f.c.attach_id.is_(None))
    rows = session.execute(select(f, ft.c.name).join(ft, ft.c.id == f.c.type_id).where(where).order_by(f.c.id)).all()
    users = _users(session, {r.setter_id for r in rows} | {r.requestee_id for r in rows})
    out = []
    for r in rows:
        item = {"id": r.id, "name": r.name, "type_id": r.type_id, "creation_date": iso(r.creation_date),
                "modification_date": iso(r.modification_date), "status": r.status,
                "setter": users[r.setter_id].login_name}
        if r.requestee_id:
            item["requestee"] = users[r.requestee_id].login_name
        out.append(item)
    return out


def bug_to_hash(session, user, bug, filt, client_ip=""):
    """WebService::Bug::_bug_to_hash."""
    cc, dep, dup = t.cc, t.dependencies, t.duplicates
    comp, prod, cls = t.components, t.products, t.classifications
    names = session.execute(
        select(comp.c.name.label("component"), prod.c.name.label("product"), cls.c.name.label("classification"))
        .select_from(comp).join(prod, prod.c.id == comp.c.product_id).join(cls, cls.c.id == prod.c.classification_id)
        .where(comp.c.id == bug.component_id)).one()
    cc_ids = list(session.scalars(select(cc.c.who).where(cc.c.bug_id == bug.bug_id)))
    users = _users(session, [bug.assigned_to, bug.reporter, bug.qa_contact, *cc_ids])
    cc_users = sorted((users[i] for i in cc_ids), key=lambda u: u.login_name)
    kw, kd, sa = t.keywords, t.keyworddefs, t.bug_see_also

    item = {
        "id": bug.bug_id,
        "alias": _aliases(session, bug.bug_id),
        "summary": bug.short_desc,
        "status": bug.bug_status,
        "resolution": bug.resolution,
        "product": names.product,
        "component": names.component,
        "classification": names.classification,
        "version": bug.version,
        "op_sys": bug.op_sys,
        "platform": bug.rep_platform,
        "priority": bug.priority,
        "severity": bug.bug_severity,
        "target_milestone": bug.target_milestone,
        "url": bug.bug_file_loc or "",
        "whiteboard": bug.status_whiteboard,
        "assigned_to": users[bug.assigned_to].login_name,
        "assigned_to_detail": _user_detail(users[bug.assigned_to]),
        "creator": users[bug.reporter].login_name,
        "creator_detail": _user_detail(users[bug.reporter]),
        "qa_contact": users[bug.qa_contact].login_name if bug.qa_contact else "",
        "cc": [u.login_name for u in cc_users],
        "cc_detail": [_user_detail(u) for u in cc_users],
        "depends_on": list(session.scalars(
            select(dep.c.dependson).where(dep.c.blocked == bug.bug_id).order_by(dep.c.dependson))),
        "blocks": list(session.scalars(
            select(dep.c.blocked).where(dep.c.dependson == bug.bug_id).order_by(dep.c.blocked))),
        "dupe_of": session.execute(select(dup.c.dupe_of).where(dup.c.dupe == bug.bug_id)).scalar(),
        "keywords": list(session.scalars(select(kd.c.name).join(kw, kw.c.keywordid == kd.c.id)
                                         .where(kw.c.bug_id == bug.bug_id).order_by(kd.c.name))),
        "see_also": list(session.scalars(select(sa.c.value).where(sa.c.bug_id == bug.bug_id).order_by(sa.c.id))),
        "groups": _bug_groups(session, bug.bug_id),
        "flags": _flags(session, bug_id=bug.bug_id),
        "creation_time": iso(bug.creation_ts),
        "last_change_time": iso(bug.delta_ts),
        "deadline": bug.deadline.strftime("%Y-%m-%d") if bug.deadline else None,
        "is_open": _is_open(session, bug.bug_status),
        "is_confirmed": bool(bug.everconfirmed),
        "is_cc_accessible": bool(bug.cclist_accessible),
        "is_creator_accessible": bool(bug.reporter_accessible),
    }
    if bug.qa_contact:
        item["qa_contact_detail"] = _user_detail(users[bug.qa_contact])
    if user.is_timetracker:
        wt = t.longdescs
        item["estimated_time"] = _number(bug.estimated_time)
        item["remaining_time"] = _number(bug.remaining_time)
        item["actual_time"] = _number(session.execute(
            select(func.coalesce(func.sum(wt.c.work_time), 0)).where(wt.c.bug_id == bug.bug_id)).scalar())
    if user.id:
        item["update_token"] = issue_hash_token(user, [bug.bug_id, bug.delta_ts], client_ip)
    return filt.apply(item, DETAIL_OF)


# ---------------------------------------------------------------- get / search

def get(session, user, raw_id, params, client_ip=""):
    step_aside(session, params)
    bug = check_bug(session, user, raw_id)
    return {"bugs": [bug_to_hash(session, user, bug, FieldFilter(params), client_ip)], "faults": []}


def _visible(user):
    """The WHERE clause Bugzilla::Search adds so that only visible bugs match."""
    b, m, g, cc = t.bugs, t.bug_group_map, t.groups, t.cc
    hidden = exists().where(m.c.bug_id == b.c.bug_id, m.c.group_id == g.c.id, not_(g.c.name.in_(user.groups or [""])))
    if not user.id:
        return not_(hidden)
    roles = [b.c.assigned_to == user.id,
             and_(b.c.reporter_accessible == 1, b.c.reporter == user.id),
             and_(b.c.cclist_accessible == 1, exists().where(cc.c.bug_id == b.c.bug_id, cc.c.who == user.id))]
    if _truthy(param("useqacontact")):
        roles.append(b.c.qa_contact == user.id)
    return or_(not_(hidden), *roles)


def _like_all_words(column, values):
    """Search type allwordssubstr: every word of a value is a substring."""
    alternatives = []
    for value in values:
        words = [w.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") for w in str(value).split()]
        alternatives.append(and_(*[column.like(f"%{w}%") for w in words]))
    return or_(*alternatives)


def search(session, user, params, client_ip=""):
    b = t.bugs
    where = [_visible(user)]
    step_aside(session, params)
    if set(params) - SEARCH_PARAMS:
        # Perl ignores unknown parameters but acts on many this port does not know.
        raise NotPorted("search parameter")
    given = lambda name: as_list(params.get(name)) if params.get(name) not in (None, "") else None

    if params.get("offset") not in (None, "") and params.get("limit") in (None, ""):
        raise param_required("limit", "Bug.search()")

    for name, column in (("version", b.c.version), ("op_sys", b.c.op_sys), ("platform", b.c.rep_platform),
                         ("priority", b.c.priority), ("severity", b.c.bug_severity),
                         ("resolution", b.c.resolution), ("target_milestone", b.c.target_milestone),
                         ("url", b.c.bug_file_loc)):
        if (values := given(name)) is not None:
            where.append(column.in_(values))
    if (values := given("id")) is not None:
        ids = [int(v) for v in values if re.fullmatch(r"\d+", str(v))]
        aliases = [str(v) for v in values if not re.fullmatch(r"\d+", str(v))]
        a = t.bugs_aliases
        where.append(or_(b.c.bug_id.in_(ids), b.c.bug_id.in_(select(a.c.bug_id).where(a.c.alias.in_(aliases)))))
    if (values := given("alias")) is not None:
        a = t.bugs_aliases
        where.append(b.c.bug_id.in_(select(a.c.bug_id).where(a.c.alias.in_(values))))
    if (values := given("status")) is not None:
        # Search.pm drops status values that do not exist instead of failing.
        s = t.bug_status
        legal = list(session.scalars(select(s.c.value).where(s.c.value.in_(values))))
        if legal:
            where.append(b.c.bug_status.in_(legal))
    if (values := given("product")) is not None:
        p = t.products
        where.append(b.c.product_id.in_(select(p.c.id).where(p.c.name.in_(values))))
    if (values := given("component")) is not None:
        c = t.components
        where.append(b.c.component_id.in_(select(c.c.id).where(c.c.name.in_(values))))
    p = t.profiles
    for name, column in (("assigned_to", b.c.assigned_to), ("creator", b.c.reporter),
                         ("reporter", b.c.reporter), ("qa_contact", b.c.qa_contact)):
        if (values := given(name)) is not None:
            where.append(column.in_(select(p.c.userid).where(p.c.login_name.in_(values))))
    for name, column in (("summary", b.c.short_desc), ("whiteboard", b.c.status_whiteboard)):
        if (values := given(name)) is not None:
            where.append(_like_all_words(column, values))
    if params.get("creation_time") not in (None, ""):
        where.append(b.c.creation_ts >= parse_datetime(params["creation_time"]))
    if params.get("last_change_time") not in (None, ""):
        where.append(b.c.delta_ts >= parse_datetime(params["last_change_time"]))

    query = select(b).where(*where).order_by(b.c.bug_id)
    max_results = int(param("max_search_results", 10000) or 10000)
    limit = params.get("limit")
    if str(limit) != "0":
        limit = int(limit) if limit not in (None, "") else max_results
        query = query.limit(min(limit, max_results)).offset(int(params.get("offset") or 0))
    filt = FieldFilter(params)
    return {"bugs": [bug_to_hash(session, user, bug, filt, client_ip) for bug in session.execute(query).all()]}


# ---------------------------------------------------------------- shared write helpers

def _clean_summary(value):
    # clean_text: runs of control characters become one space, then trim.
    summary = re.sub(r"[\x00-\x1f\x7f]+", " ", str(value if value is not None else "")).strip()
    if summary == "":
        raise BugzillaError(107, "You must enter a summary for this bug.")
    if len(summary) > MAX_SUMMARY_LENGTH:
        raise BugzillaError(107, f"The text you entered in the Summary field is too long "
                                 f"({len(summary)} characters, above the maximum length allowed of {MAX_SUMMARY_LENGTH} characters).")
    return summary


def _clean_comment(value):
    text = re.sub(r"\s+$", "", str(value if value is not None else "").replace("\r\n", "\n").replace("\r", "\n"))
    if len(text) > MAX_COMMENT_LENGTH:
        raise BugzillaError(114, f"Comments cannot be longer than {MAX_COMMENT_LENGTH} characters.")
    return text


def _private(user, value):
    """Only members of the insider group may mark comments or attachments private."""
    if not _truthy(value):
        return False
    if not user.is_insider:
        raise BugzillaError(113, "Sorry, but you are not allowed to (un)mark comments or attachments as private.")
    return True


def _log(session, bug_id, field, removed, added, who, when, attach_id=None):
    """LogActivityEntry."""
    f = t.fielddefs
    field_id = session.execute(select(f.c.id).where(f.c.name == field)).scalar_one()
    session.execute(insert(t.bugs_activity).values(
        bug_id=bug_id, attach_id=attach_id, who=who, bug_when=when, fieldid=field_id,
        removed=str(removed), added=str(added)))


def _add_comment(session, bug_id, who, when, text, private=False, type_=CMT_NORMAL, extra=None):
    result = session.execute(insert(t.longdescs).values(
        bug_id=bug_id, who=who, bug_when=when, thetext=text, isprivate=int(bool(private)),
        work_time=0, type=type_, extra_data=extra))
    return result.inserted_primary_key[0]


def _sync_fulltext(session, bug_id, new=False):
    """Bugzilla::Bug::_sync_fulltext: the row Perl's content search reads."""
    b, ld, ft = t.bugs, t.longdescs, t.bugs_fulltext
    summary = session.execute(select(b.c.short_desc).where(b.c.bug_id == bug_id)).scalar_one()
    rows = session.execute(select(ld.c.thetext, ld.c.isprivate).where(ld.c.bug_id == bug_id)
                           .order_by(ld.c.bug_when, ld.c.comment_id)).all()
    values = {"short_desc": summary, "comments": "\n".join(r.thetext for r in rows),
              "comments_noprivate": "\n".join(r.thetext for r in rows if not r.isprivate)}
    if new:
        session.execute(insert(ft).values(bug_id=bug_id, **values))
    else:
        session.execute(update(ft).where(ft.c.bug_id == bug_id).values(**values))


def _dependency_ids(session, user, values, self_id=None):
    ids = []
    for value in as_list(values):
        bug_id = check_bug(session, user, value).bug_id
        if bug_id == self_id:
            raise BugzillaError(116, "You can't make a bug block itself or depend on itself.")
        if bug_id not in ids:
            ids.append(bug_id)
    return ids


def _reachable(session, start, column_from, column_to):
    dep = t.dependencies
    seen, queue = set(), list(start)
    while queue:
        found = set(session.scalars(select(dep.c[column_to]).where(dep.c[column_from].in_(queue)))) - seen
        seen |= found
        queue = list(found)
    return seen


def _check_dependency_loop(session, depends_on, blocks):
    """A bug may not be both upstream and downstream of this bug."""
    upstream = set(depends_on) | _reachable(session, depends_on, "blocked", "dependson")
    downstream = set(blocks) | _reachable(session, blocks, "dependson", "blocked")
    both = sorted(upstream & downstream)
    if both:
        raise BugzillaError(116, "The following bug(s) would appear on both the \"depends on\" and \"blocks\" "
                                 "parts of the dependency tree if these changes are committed: "
                                 + ", ".join(str(i) for i in both)
                                 + ". This would create a circular dependency, which is not allowed.")


# ---------------------------------------------------------------- create

def create(session, user, p):
    step_aside(session, p, CREATE_GATES)
    require_login(user)
    if not user.in_group("editbugs") and (p.get("depends_on") or p.get("blocks")):
        raise NotPorted("dependencies set by a user without editbugs")
    b = t.bugs
    prod, comp = t.products, t.components

    # check_required_create_fields / run_create_validators, product first.
    name = str(p.get("product") or "").strip()
    if not name:
        raise BugzillaError(50, "You must select/enter a product.")
    product = session.execute(select(prod).where(prod.c.name == name)).first()
    if product is None or not _can_enter(session, user, product):
        raise BugzillaError(106, f"Sorry, either the product {name} does not exist or you aren't authorized "
                                 "to enter a bug into it.")
    name = str(p.get("component") or "").strip()
    if not name:
        raise BugzillaError(105, "To file this bug, you must first choose a component. If necessary, just guess.")
    component = session.execute(select(comp).where(comp.c.product_id == product.id, comp.c.name == name)).first()
    if component is None:
        raise object_not_found(f"There is no component named '{name}' in the '{product.name}' product.")
    summary = _clean_summary(p.get("summary"))

    if str(p.get("version") or "").strip() == "":
        raise BugzillaError(50, "You must select/enter a version.")
    version = _in_product(session, t.versions, "value", p["version"], product, "version").value
    editbugs = user.in_group("editbugs")
    values = {
        "op_sys": _select_value(session, "op_sys", p.get("op_sys", param("defaultopsys"))),
        "rep_platform": _select_value(session, "rep_platform", p.get("platform", param("defaultplatform"))),
        "priority": _select_value(session, "priority", p.get("priority", param("defaultpriority"))
                                  if _truthy(param("letsubmitterchoosepriority")) else param("defaultpriority")),
        "bug_severity": _select_value(session, "bug_severity", p.get("severity", param("defaultseverity"))),
    }
    status = _initial_status(session, user, product, p.get("status"))
    _step_aside_for_write(session, user, product.id, status_change=True)
    if not _is_open(session, status):
        raise NotPorted("bug filed as closed")
    milestone = product.defaultmilestone
    if p.get("target_milestone") not in (None, "") and editbugs:
        milestone = _in_product(session, t.milestones, "value", p["target_milestone"], product, "milestone").value

    assignee = component.initialowner
    if p.get("assigned_to") not in (None, "") and editbugs:
        assignee = _user_id(session, p["assigned_to"])
    qa_contact = component.initialqacontact if _truthy(param("useqacontact")) else None
    if p.get("qa_contact") not in (None, "") and editbugs and _truthy(param("useqacontact")):
        qa_contact = _user_id(session, p["qa_contact"])
    ccc = t.component_cc
    cc_ids = set(session.scalars(select(ccc.c.user_id).where(ccc.c.component_id == component.id)))
    for login in as_list(p.get("cc")):
        cc_ids.add(_user_id(session, login, lambda n: BugzillaError(
            504, f"The name {n} is not a valid username. Either you misspelled it, "
                 "or the person has not registered for a Bugzilla account.")))
    aliases = _check_aliases(session, p.get("alias"))
    depends_on = _dependency_ids(session, user, p.get("depends_on"))
    blocks = _dependency_ids(session, user, p.get("blocks"))
    _check_dependency_loop(session, depends_on, blocks)
    comment = _clean_comment(p.get("description", p.get("comment")))
    groups = _default_groups(session, user, product)
    private = _private(user, p.get("comment_is_private"))

    now = db_now(session)
    bug_id = session.execute(insert(b).values(
        short_desc=summary, product_id=product.id, component_id=component.id, version=version,
        bug_status=status, resolution="", everconfirmed=0 if status == "UNCONFIRMED" else 1,
        target_milestone=milestone, assigned_to=assignee, qa_contact=qa_contact, reporter=user.id,
        bug_file_loc=str(p.get("url") or ""), status_whiteboard=str(p.get("whiteboard") or ""),
        creation_ts=now, delta_ts=now, **values)).inserted_primary_key[0]
    for group_id in groups:
        session.execute(insert(t.bug_group_map).values(bug_id=bug_id, group_id=group_id))
    for who in cc_ids:
        session.execute(insert(t.cc).values(bug_id=bug_id, who=who))
    # Perl records the reverse side of each dependency on the other bug.
    for other in depends_on:
        session.execute(insert(t.dependencies).values(blocked=bug_id, dependson=other))
        _log(session, other, "blocked", "", bug_id, user.id, now)
        session.execute(update(b).where(b.c.bug_id == other).values(delta_ts=now))
    for other in blocks:
        session.execute(insert(t.dependencies).values(blocked=other, dependson=bug_id))
        _log(session, other, "dependson", "", bug_id, user.id, now)
        session.execute(update(b).where(b.c.bug_id == other).values(delta_ts=now))
    _add_comment(session, bug_id, user.id, now, comment,
                 private=private)
    for alias in aliases:
        session.execute(insert(t.bugs_aliases).values(alias=alias, bug_id=bug_id))
    _sync_fulltext(session, bug_id, new=True)
    session.commit()
    return {"id": bug_id}


def _can_enter(session, user, product):
    """Bugzilla::User::can_enter_product: active, and in every entry group."""
    if not product.isactive:
        return False
    gcm, g = t.group_control_map, t.groups
    entry = session.scalars(select(g.c.name).join(gcm, gcm.c.group_id == g.c.id)
                            .where(gcm.c.product_id == product.id, gcm.c.entry != 0))
    return set(entry) <= user.groups


def _default_groups(session, user, product):
    """The groups a new bug is put in when the client names none (_check_groups)."""
    gcm, g = t.group_control_map, t.groups
    rows = session.execute(select(g.c.id, g.c.name, gcm.c.membercontrol, gcm.c.othercontrol)
                           .join(gcm, gcm.c.group_id == g.c.id)
                           .where(gcm.c.product_id == product.id, g.c.isactive == 1, g.c.isbuggroup == 1)).all()
    out = []
    for row in rows:
        control = row.membercontrol if row.name in user.groups else row.othercontrol
        if control in (CONTROL_DEFAULT, CONTROL_MANDATORY):
            out.append(row.id)
    return out


def _initial_status(session, user, product, wanted):
    """_check_bug_status for a new bug."""
    s, w = t.bug_status, t.status_workflow
    available = list(session.scalars(
        select(s.c.value).join(w, w.c.new_status == s.c.id)
        .where(w.c.old_status.is_(None), s.c.isactive == 1).order_by(s.c.sortkey, s.c.value)))
    if not product.allows_unconfirmed:
        available = [v for v in available if v != "UNCONFIRMED"]
    if user.in_group("editbugs") or user.in_group("canconfirm"):
        status = wanted or next((v for v in available if v != "UNCONFIRMED"), available[0])
    else:
        status = "UNCONFIRMED" if "UNCONFIRMED" in available else available[0]
    status = _select_value(session, "bug_status", status)
    if status not in available:
        raise BugzillaError(123, f"You are not allowed to file new bugs with the {status} status.")
    return status


def _check_aliases(session, value):
    aliases = [str(a).strip() for a in as_list(value) if str(a).strip()]
    a = t.bugs_aliases
    for alias in aliases:
        if len(alias) > 40:
            raise BugzillaError(103, "Bug aliases cannot be longer than 40 characters. Please choose a shorter alias.")
        if re.fullmatch(r"\d+", alias):
            raise BugzillaError(103, f"You tried to give this bug the alias {alias}, but aliases cannot be "
                                     "merely numbers, since they could then be confused with bug IDs. "
                                     "Please choose an alias containing at least one letter.")
        if re.search(r"[, ]", alias):
            raise BugzillaError(103, f"The alias you entered, {alias}, contains one or more commas or spaces. "
                                     "Aliases cannot contain commas or spaces because those characters are used "
                                     "to separate aliases from each other in lists of aliases.")
        other = session.execute(select(a.c.bug_id).where(a.c.alias == alias)).scalar()
        if other is not None:
            raise BugzillaError(103, f"Bug {other} has already taken the alias {alias}. Please choose another alias.")
    return aliases


# ---------------------------------------------------------------- update

SIMPLE = {  # REST name -> bugs column, for fields compared and stored as plain strings
    "url": "bug_file_loc", "whiteboard": "status_whiteboard",
}
SELECTS = {"priority": "priority", "severity": "bug_severity", "op_sys": "op_sys", "platform": "rep_platform"}


def update_bugs(session, user, raw_id, p):
    step_aside(session, p, UPDATE_GATES)
    require_login(user)
    if not user.in_group("editbugs"):
        # Perl decides field by field what a reporter, assignee or other user may change.
        raise NotPorted("update by a user without editbugs")
    if str(p.get("resolution") or "") == "DUPLICATE" and p.get("dupe_of") in (None, ""):
        raise NotPorted("DUPLICATE without a target")
    bugs = [check_bug(session, user, raw_id)]
    for bug in bugs:
        _step_aside_for_write(session, user, bug.product_id, status_change="status" in p or "dupe_of" in p)
    now = db_now(session)
    results = []
    for bug in bugs:
        changes = _update_one(session, user, bug, p, now)
        b = t.bugs
        delta = session.execute(select(b.c.delta_ts).where(b.c.bug_id == bug.bug_id)).scalar_one()
        results.append({"id": bug.bug_id, "alias": _aliases(session, bug.bug_id),
                        "last_change_time": iso(delta), "changes": changes})
    session.commit()
    return {"bugs": results}


def _set_changes(spec):
    spec = spec if isinstance(spec, dict) else {"set": spec}
    return spec


def _update_one(session, user, bug, p, now):
    b, dep, cc = t.bugs, t.dependencies, t.cc
    bug_id = bug.bug_id
    product = session.execute(select(t.products).where(t.products.c.id == bug.product_id)).one()
    new = {}                      # bugs columns to write
    activity = []                 # (activity field, removed, added) on this bug
    other_activity = []           # (bug id, activity field, removed, added) on other bugs
    comments = []                 # (bug id, text, private, type, extra)

    def change(column, value, field=None, shown=None):
        old = getattr(bug, column)
        if (old if old is not None else "") != (value if value is not None else ""):
            new[column] = value
            removed, added = shown or (old, value)
            activity.append((field or column, "" if removed is None else removed, "" if added is None else added))

    if "summary" in p:
        change("short_desc", _clean_summary(p["summary"]))
    for api, column in SELECTS.items():
        if api in p:
            change(column, _select_value(session, column, p[api]))
    for api, column in SIMPLE.items():
        if api in p:
            change(column, str(p[api] if p[api] is not None else ""))
    if "version" in p:
        change("version", _in_product(session, t.versions, "value", p["version"], product, "version").value)
    if "target_milestone" in p:
        change("target_milestone",
               _in_product(session, t.milestones, "value", p["target_milestone"], product, "milestone").value)
    if "component" in p:
        comp = t.components
        found = _in_product(session, comp, "name", p["component"], product, "component")
        ccc = t.component_cc
        if found.id != bug.component_id and session.execute(
                select(ccc.c.user_id).where(ccc.c.component_id == found.id)).first():
            raise NotPorted("component with default CC")
        if found.id != bug.component_id:
            old_name = session.execute(select(comp.c.name).where(comp.c.id == bug.component_id)).scalar_one()
            change("component_id", found.id, "component", (old_name, found.name))
    for api, column in (("assigned_to", "assigned_to"), ("qa_contact", "qa_contact")):
        if api in p:
            new_id = _user_id(session, p[api]) if p[api] not in (None, "") else None
            if api == "assigned_to" and new_id is None:
                raise BugzillaError(50, "You must select/enter a AssignedTo.")
            if new_id != getattr(bug, column):
                users = _users(session, [new_id, getattr(bug, column)])
                login = lambda i: users[i].login_name if i else ""
                change(column, new_id, column, (login(getattr(bug, column)), login(new_id)))
    for api, column in (("is_cc_accessible", "cclist_accessible"), ("is_creator_accessible", "reporter_accessible")):
        if api in p:
            change(column, int(_truthy(p[api])))
    if "deadline" in p and user.is_timetracker:
        value = parse_datetime(p["deadline"]).date() if p["deadline"] not in (None, "") else None
        if value != (bug.deadline.date() if bug.deadline else None):
            change("deadline", value, "deadline", (bug.deadline.strftime("%Y-%m-%d") if bug.deadline else "",
                                                   value.isoformat() if value else ""))
    for api in ("estimated_time", "remaining_time"):
        if api in p and user.is_timetracker and float(p[api] or 0) != float(getattr(bug, api)):
            # Perl logs the stored value as removed and the client's own text as added.
            change(api, float(p[api] or 0), api, (f"{float(getattr(bug, api)):.2f}", str(p[api])))

    # Comment (must be known before the duplicate handling, which re-types it).
    comment = p.get("comment")
    if comment is not None:
        body = comment.get("body", comment.get("comment")) if isinstance(comment, dict) else comment
        private = _private(user, comment.get("is_private")) if isinstance(comment, dict) else False
        text = _clean_comment(body)
        if text != "":
            comments.append([bug_id, text, private, CMT_NORMAL, None])

    # Status, resolution, duplicate (set_bug_status / set_resolution / set_dup_id).
    status, resolution = bug.bug_status, bug.resolution
    dup = t.duplicates
    old_dupe = session.execute(select(dup.c.dupe_of).where(dup.c.dupe == bug_id)).scalar()
    new_dupe = old_dupe
    wanted_resolution = p.get("resolution")
    if p.get("dupe_of") not in (None, ""):
        target = check_bug(session, user, p["dupe_of"])
        if target.bug_id != old_dupe:
            seen, current = set(), target.bug_id
            while current is not None and current not in seen:
                if current == bug_id:
                    raise BugzillaError(118, f"You cannot mark bug {bug_id} as a duplicate of " + (
                        "itself." if target.bug_id == bug_id
                        else f"bug {target.bug_id}, because it would create a duplicate loop."))
                seen.add(current)
                current = session.execute(select(dup.c.dupe_of).where(dup.c.dupe == current)).scalar()
            new_dupe = target.bug_id
            if "status" not in p and resolution != "DUPLICATE":
                p = dict(p, status=param("duplicate_or_move_bug_status", "RESOLVED"))
            wanted_resolution = "DUPLICATE"
            # The reporter is copied to the target only if they may see it.
            reporter_user = load_user(session, bug.reporter)
            if (target.reporter != bug.reporter and reporter_user and _can_see(session, reporter_user, target)
                    and not session.execute(select(cc.c.who).where(
                        cc.c.bug_id == target.bug_id, cc.c.who == bug.reporter)).first()):
                reporter = _users(session, [bug.reporter])[bug.reporter].login_name
                session.execute(insert(cc).values(bug_id=target.bug_id, who=bug.reporter))
                other_activity.append((target.bug_id, "cc", "", reporter))
            comments.append([target.bug_id, "", False, CMT_HAS_DUPE, str(bug_id)])
            normal = [c for c in comments if c[0] == bug_id and c[3] == CMT_NORMAL]
            if normal:
                normal[-1][3], normal[-1][4] = CMT_DUPE_OF, str(new_dupe)
            else:
                comments.append([bug_id, "", False, CMT_DUPE_OF, str(new_dupe)])
    if "status" in p:
        status = _select_value(session, "bug_status", p["status"])
        if status != bug.bug_status:
            s, w = t.bug_status, t.status_workflow
            old_s, new_s = s.alias(), s.alias()
            allowed = session.execute(
                select(w.c.new_status).join(old_s, old_s.c.id == w.c.old_status).join(new_s, new_s.c.id == w.c.new_status)
                .where(old_s.c.value == bug.bug_status, new_s.c.value == status, new_s.c.isactive == 1)).first()
            if not allowed:
                raise BugzillaError(123, "You are not allowed to change the bug status from "
                                         f"{bug.bug_status} to {status}.")
    is_open = _is_open(session, status)
    everconfirmed = bug.everconfirmed
    if "status" in p:
        if is_open:
            everconfirmed = 0 if status == "UNCONFIRMED" else 1
            resolution, wanted_resolution, new_dupe = "", None, None
            comments = [c for c in comments if c[3] not in (CMT_DUPE_OF, CMT_HAS_DUPE)]
        elif wanted_resolution is None:
            wanted_resolution = resolution
    if wanted_resolution is not None:
        wanted_resolution = str(wanted_resolution).strip()
        if not wanted_resolution and not is_open:
            raise BugzillaError(121, f"A valid resolution is required to mark bugs as {status.upper()}.")
        resolution = _select_value(session, "resolution", wanted_resolution)
        if is_open and resolution:
            raise BugzillaError(122, "You cannot set a resolution for open bugs.")
    if resolution != "DUPLICATE":
        new_dupe = None
    change("bug_status", status)
    change("resolution", resolution)
    change("everconfirmed", everconfirmed, shown=(str(int(bug.everconfirmed)), str(int(everconfirmed))))
    if not is_open and status != bug.bug_status and float(bug.remaining_time) != 0:
        change("remaining_time", 0, shown=(f"{float(bug.remaining_time):.2f}", "0.00"))

    # CC.
    cc_added, cc_removed = [], []
    if isinstance(p.get("cc"), dict):
        current = set(session.scalars(select(cc.c.who).where(cc.c.bug_id == bug_id)))
        add = {_user_id(session, login) for login in as_list(p["cc"].get("add"))} - current
        remove = {_user_id(session, login) for login in as_list(p["cc"].get("remove"))} & current
        users = _users(session, add | remove)
        cc_added = sorted(users[i].login_name for i in add)
        cc_removed = sorted(users[i].login_name for i in remove - add)
        for who in add:
            session.execute(insert(cc).values(bug_id=bug_id, who=who))
        if remove - add:
            session.execute(delete(cc).where(cc.c.bug_id == bug_id, cc.c.who.in_(remove - add)))
        if cc_added or cc_removed:
            activity.append(("cc", ", ".join(cc_removed), ", ".join(cc_added)))

    # Dependencies: each side is also logged on the other bug.
    old_deps = {"dependson": set(session.scalars(select(dep.c.dependson).where(dep.c.blocked == bug_id))),
                "blocked": set(session.scalars(select(dep.c.blocked).where(dep.c.dependson == bug_id)))}
    new_deps = {k: set(v) for k, v in old_deps.items()}
    for api, field in (("depends_on", "dependson"), ("blocks", "blocked")):
        if api not in p:
            continue
        spec = _set_changes(p[api])
        if "set" in spec:
            new_deps[field] = set(_dependency_ids(session, user, spec["set"], bug_id))
        new_deps[field] |= set(_dependency_ids(session, user, spec.get("add"), bug_id))
        new_deps[field] -= set(_dependency_ids(session, user, spec.get("remove"), bug_id))
    if new_deps != old_deps:
        _check_dependency_loop(session, new_deps["dependson"], new_deps["blocked"])
    for field, reverse in (("dependson", "blocked"), ("blocked", "dependson")):
        added, removed = sorted(new_deps[field] - old_deps[field]), sorted(old_deps[field] - new_deps[field])
        pair = (lambda other: {"blocked": bug_id, "dependson": other}) if field == "dependson" \
            else (lambda other: {"blocked": other, "dependson": bug_id})
        for other in added:
            session.execute(insert(dep).values(**pair(other)))
            other_activity.append((other, reverse, "", bug_id))
        for other in removed:
            row = pair(other)
            session.execute(delete(dep).where(dep.c.blocked == row["blocked"], dep.c.dependson == row["dependson"]))
            other_activity.append((other, reverse, bug_id, ""))
        if added or removed:
            activity.append((field, ", ".join(map(str, removed)), ", ".join(map(str, added))))

    # Write.
    if new_dupe != old_dupe:
        session.execute(delete(dup).where(dup.c.dupe == bug_id))
        if new_dupe is not None:
            session.execute(insert(dup).values(dupe_of=new_dupe, dupe=bug_id))
    else:
        comments = [c for c in comments if c[3] != CMT_HAS_DUPE]
        for c in comments:
            if c[3] == CMT_DUPE_OF:
                c[3], c[4] = CMT_NORMAL, None
        comments = [c for c in comments if c[1] != "" or c[3] != CMT_NORMAL]
    touched = {bug_id} if (activity or new or any(c[0] == bug_id for c in comments)) else set()
    if new:
        session.execute(update(b).where(b.c.bug_id == bug_id).values(**new))
    for field, removed, added in activity:
        _log(session, bug_id, field, removed, added, user.id, now)
    for other, field, removed, added in other_activity:
        _log(session, other, field, removed, added, user.id, now)
        touched.add(other)
    for target, text, private, type_, extra in comments:
        _add_comment(session, target, user.id, now, text, private, type_, extra)
        touched.add(target)
    if touched:
        session.execute(update(b).where(b.c.bug_id.in_(touched)).values(delta_ts=now))
    if "short_desc" in new or comments:
        for target in {bug_id} | {c[0] for c in comments}:
            _sync_fulltext(session, target)
    changes = {API_NAME.get(field, field): {"removed": str(removed), "added": str(added)}
               for field, removed, added in activity}
    if new_dupe != old_dupe:
        # Reported to the client, but Perl writes no bugs_activity row for it.
        changes["dupe_of"] = {"removed": str(old_dupe or ""), "added": str(new_dupe or "")}
    return changes


# ---------------------------------------------------------------- comments

def _comment_text(session, row):
    """Bugzilla::Comment::body_full for the comment types Perl generates text for."""
    if row.type == CMT_DUPE_OF:
        return f"{row.thetext}\n\n*** This bug has been marked as a duplicate of bug {row.extra_data} ***"
    if row.type == CMT_HAS_DUPE:
        return f"*** Bug {row.extra_data} has been marked as a duplicate of this bug. ***"
    if row.type == CMT_ATTACHMENT_CREATED:
        a = t.attachments
        description = session.execute(select(a.c.description).where(a.c.attach_id == int(row.extra_data))).scalar()
        return f"Created attachment {row.extra_data}\n{description}" + (f"\n\n{row.thetext}" if row.thetext else "")
    return row.thetext


def _comment_to_hash(session, row, count, login, filt):
    tags = t.longdescs_tags
    is_attachment = row.type in (CMT_ATTACHMENT_CREATED, 6)
    return filt.apply({
        "id": row.comment_id,
        "bug_id": row.bug_id,
        "creator": login,
        "time": iso(row.bug_when),
        "creation_time": iso(row.bug_when),
        "is_private": bool(row.isprivate),
        "text": _comment_text(session, row),
        "attachment_id": int(row.extra_data) if is_attachment and row.extra_data else None,
        "count": count,
        "tags": list(session.scalars(select(tags.c.tag).where(tags.c.comment_id == row.comment_id).order_by(tags.c.tag))),
    })


def _bug_comments(session, bug_id):
    ld = t.longdescs
    return session.execute(select(ld).where(ld.c.bug_id == bug_id).order_by(ld.c.bug_when, ld.c.comment_id)).all()


def comments(session, user, params, bug_ids=(), comment_ids=()):
    step_aside(session, params)
    filt = FieldFilter(params)
    since = parse_datetime(params["new_since"]) if params.get("new_since") not in (None, "") else None
    bugs = {}
    for raw in bug_ids:
        bug = check_bug(session, user, raw)
        rows = _bug_comments(session, bug.bug_id)
        users = _users(session, {r.who for r in rows})
        bugs[str(bug.bug_id)] = {"comments": [
            _comment_to_hash(session, row, count, users[row.who].login_name, filt)
            for count, row in enumerate(rows)
            if (user.is_insider or not row.isprivate) and (since is None or row.bug_when > since)]}
    found = {}
    ld = t.longdescs
    for raw in comment_ids:
        if not re.fullmatch(r"\d+", str(raw)):
            raise BugzillaError(52, "Invalid parameter passed to Bugzilla::Comment::new_from_list: It must be numeric.")
        row = session.execute(select(ld).where(ld.c.comment_id == int(raw))).first()
        if row is None:
            raise BugzillaError(111, f"{raw} is not a valid comment id.")
        check_bug(session, user, row.bug_id)
        if row.isprivate and not user.is_insider:
            raise BugzillaError(110, f"Comment id {raw} is private.")
        count = [r.comment_id for r in _bug_comments(session, row.bug_id)].index(row.comment_id)
        login = _users(session, [row.who])[row.who].login_name
        found[str(row.comment_id)] = _comment_to_hash(session, row, count, login, filt)
    return {"bugs": bugs, "comments": found}


def add_comment(session, user, raw_id, p):
    step_aside(session, p, ("work_time",))
    require_login(user)
    if p.get("comment") is None or str(p["comment"]).strip() == "":
        raise param_required("comment")
    bug = check_bug(session, user, raw_id)
    text = _clean_comment(p["comment"])
    _step_aside_for_write(session, user, bug.product_id)
    # "private" is the older name of is_private; Perl accepts both.
    private = _private(user, p.get("is_private", p.get("private")))
    now = db_now(session)
    comment_id = _add_comment(session, bug.bug_id, user.id, now, text, private=private)
    b = t.bugs
    session.execute(update(b).where(b.c.bug_id == bug.bug_id).values(delta_ts=now))
    _sync_fulltext(session, bug.bug_id)
    session.commit()
    return {"id": comment_id}


# ---------------------------------------------------------------- history

def history(session, user, raw_ids, params):
    step_aside(session, params)
    since = parse_datetime(params["new_since"]) if params.get("new_since") not in (None, "") else None
    act, f, p, att = t.bugs_activity, t.fielddefs, t.profiles, t.attachments
    out = []
    for raw in raw_ids:
        bug = check_bug(session, user, raw)
        query = (select(act.c.bug_when, act.c.removed, act.c.added, act.c.attach_id, f.c.name, p.c.login_name)
                 .join(f, f.c.id == act.c.fieldid).join(p, p.c.userid == act.c.who)
                 .outerjoin(att, att.c.attach_id == act.c.attach_id)
                 .where(act.c.bug_id == bug.bug_id).order_by(act.c.bug_when, act.c.id))
        if since is not None:
            query = query.where(act.c.bug_when > since)
        if not user.is_insider:
            query = query.where(func.coalesce(att.c.isprivate, 0) == 0)
        if not user.is_timetracker:
            query = query.where(not_(f.c.name.in_(TIMETRACKING_FIELDS)))
        entries = []
        for row in session.execute(query):
            if not entries or (entries[-1]["when"], entries[-1]["who"]) != (iso(row.bug_when), row.login_name):
                entries.append({"when": iso(row.bug_when), "who": row.login_name, "changes": []})
            item = {"field_name": API_NAME.get(row.name, row.name), "removed": row.removed or "", "added": row.added or ""}
            if row.attach_id:
                item["attachment_id"] = row.attach_id
            entries[-1]["changes"].append(item)
        out.append({"id": bug.bug_id, "alias": _aliases(session, bug.bug_id), "history": entries})
    return {"bugs": out}


# ---------------------------------------------------------------- attachments

def _attachment_to_hash(session, row, login, filt):
    item = {
        "id": row.attach_id,
        "bug_id": row.bug_id,
        "file_name": row.filename,
        "summary": row.description,
        "content_type": row.mimetype,
        "is_private": int(row.isprivate),
        "is_obsolete": int(row.isobsolete),
        "is_patch": int(row.ispatch),
        "creator": login,
        "creation_time": iso(row.creation_ts),
        "last_change_time": iso(row.modification_time),
        "size": row.size or 0,
        "flags": _flags(session, attach_id=row.attach_id),
    }
    if filt.wants("data"):
        d = t.attach_data
        data = session.execute(select(d.c.thedata).where(d.c.id == row.attach_id)).scalar() or b""
        item["data"] = base64.b64encode(data).decode()
    return filt.apply(item)


def _attachment_query():
    a, d = t.attachments, t.attach_data
    return select(a, func.length(d.c.thedata).label("size")).outerjoin(d, d.c.id == a.c.attach_id)


def attachments(session, user, params, bug_ids=(), attachment_ids=()):
    step_aside(session, params)
    filt = FieldFilter(params)
    a = t.attachments
    visible = lambda row: not row.isprivate or user.is_insider or row.submitter_id == user.id
    bugs = {}
    for raw in bug_ids:
        bug = check_bug(session, user, raw)
        rows = session.execute(_attachment_query().where(a.c.bug_id == bug.bug_id).order_by(a.c.attach_id)).all()
        users = _users(session, {r.submitter_id for r in rows})
        bugs[str(bug.bug_id)] = [_attachment_to_hash(session, r, users[r.submitter_id].login_name, filt)
                                 for r in rows if visible(r)]
    found = {}
    for raw in attachment_ids:
        # Perl answers with nothing, not an error, for an id that does not exist.
        row = session.execute(_attachment_query().where(a.c.attach_id == int(raw))).first() \
            if re.fullmatch(r"\d+", str(raw)) else None
        if row is None:
            continue
        check_bug(session, user, row.bug_id)
        if not visible(row):
            raise BugzillaError(304, f"Sorry, you are not authorized to access attachment #{row.attach_id}.")
        login = _users(session, [row.submitter_id])[row.submitter_id].login_name
        found[str(row.attach_id)] = _attachment_to_hash(session, row, login, filt)
    return {"bugs": bugs, "attachments": found}


def add_attachment(session, user, raw_id, p):
    step_aside(session, p, ("flags",))
    require_login(user)
    if p.get("data") is None:
        raise param_required("data")
    bugs = [check_bug(session, user, raw_id)]
    for bug in bugs:
        _step_aside_for_write(session, user, bug.product_id)

    summary = " ".join(str(p.get("summary") or "").split())
    if not summary:
        raise BugzillaError(604, "You must enter a description for the attachment.")
    try:
        data = base64.b64decode(str(p["data"]), validate=False)
    except ValueError:
        data = b""
    if not data:
        raise BugzillaError(606, "The file you are trying to attach is empty, does not exist, or you don't "
                                 "have permission to read it.")
    max_kb = int(param("maxattachmentsize", 1000) or 0)
    if len(data) > max_kb * 1024:
        raise BugzillaError(600, f"The file you are trying to attach is {len(data) // 1024} kilobytes (KB) in size. "
                                 f"Attachments cannot be more than {max_kb} KB.")
    file_name = re.split(r"[/\\]", str(p.get("file_name") or ""))[-1][:255]
    if not file_name:
        raise BugzillaError(603, "You did not specify a file to attach.")
    is_patch = _truthy(p.get("is_patch"))
    content_type = "text/plain" if is_patch else str(p.get("content_type") or "")
    if not re.fullmatch(rf"(?:{'|'.join(LEGAL_CONTENT_TYPES)})/[^=?;,\s]+", content_type):
        raise BugzillaError(601, " ".join(
            f"The content type {content_type} is invalid. Valid types must be of the form foo/bar where foo is one of "
            f"{', '.join(LEGAL_CONTENT_TYPES)} and bar must not contain any special characters "
            "(such as \"=\", \"?\", ...).".split()))
    private = _private(user, p.get("is_private"))
    comment = _clean_comment(p.get("comment"))

    now = db_now(session)
    ids = []
    for bug in bugs:
        attach_id = session.execute(insert(t.attachments).values(
            bug_id=bug.bug_id, creation_ts=now, modification_time=now, description=summary, mimetype=content_type,
            ispatch=int(is_patch), filename=file_name, submitter_id=user.id, isobsolete=0,
            isprivate=int(private))).inserted_primary_key[0]
        session.execute(insert(t.attach_data).values(id=attach_id, thedata=data))
        _add_comment(session, bug.bug_id, user.id, now, comment, private, CMT_ATTACHMENT_CREATED, str(attach_id))
        b = t.bugs
        session.execute(update(b).where(b.c.bug_id == bug.bug_id).values(delta_ts=now))
        _sync_fulltext(session, bug.bug_id)
        ids.append(attach_id)
    session.commit()
    return {"ids": ids}
