"""Read side of Bugzilla::WebService::Bug (get/search/comments/history/attachments/fields/legal_values)
plus the shared Bug lookup / visibility helpers used by writes.py."""
import base64
import re
from datetime import date, datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.auth import User
from app.core.errors import BugzillaError, param_required, params_required
from app.core.params import param
from app.core.rest import Ctx
from app.core.util import as_list, iso_z, to_int

# Bugzilla::Bug::FIELD_MAP  (API name -> DB column)
FIELD_MAP = {
    "blocks": "blocked", "commentprivacy": "comment_is_private", "creation_time": "creation_ts",
    "creator": "reporter", "description": "comment", "depends_on": "dependson", "dupe_of": "dup_id",
    "id": "bug_id", "is_confirmed": "everconfirmed", "is_cc_accessible": "cclist_accessible",
    "is_creator_accessible": "reporter_accessible", "last_change_time": "delta_ts",
    "platform": "rep_platform", "severity": "bug_severity", "status": "bug_status",
    "summary": "short_desc", "url": "bug_file_loc", "whiteboard": "status_whiteboard",
}
API_NAME = {v: k for k, v in FIELD_MAP.items()}
API_NAME["bug_group"] = "groups"

PRODUCT_SPECIFIC_FIELDS = ("version", "target_milestone", "component")
ABNORMAL_SELECTS = ("classification", "component", "product", "target_milestone", "version")
TIMETRACKING_FIELDS = ("estimated_time", "remaining_time", "work_time", "actual_time", "percentage_complete")
# field-descs.none.tmpl names used by object_does_not_exist
CLASS_DESC = {"priority": "Priority", "bug_severity": "Severity", "bug_status": "status", "op_sys": "OS",
              "rep_platform": "Hardware", "resolution": "Resolution", "version": "version",
              "component": "component", "target_milestone": "milestone", "product": "product",
              "user": "user", "field": "field", "keyword": "keyword"}
CMT_NORMAL, CMT_DUPE_OF, CMT_HAS_DUPE, CMT_ATTACHMENT_CREATED, CMT_ATTACHMENT_UPDATED = 0, 1, 2, 5, 6
IDENT_RE = re.compile(r"^[a-z_][a-z0-9_]*$")


# ---------------------------------------------------------------- errors
def object_does_not_exist(kind: str, name, product: str | None = None, by_id: bool = False) -> BugzillaError:
    desc = CLASS_DESC.get(kind, kind)
    what = f"with the id '{name}'" if by_id else f"named '{name}'"
    msg = f"There is no {desc} {what}" + (f" in the '{product}' product" if product else "") + "."
    if kind == "user":
        msg += " Either you mis-typed the name or that user has not yet registered for a Bugzilla account."
    return BugzillaError("object_does_not_exist", msg)


def object_not_specified(kind: str) -> BugzillaError:
    return BugzillaError("object_not_specified", f"You must select/enter a {CLASS_DESC.get(kind, kind)}.")


def bug_does_not_exist(bug_id) -> BugzillaError:
    return BugzillaError("bug_id_does_not_exist", f"Bug #{bug_id} does not exist.")


def improper_bug_id(value) -> BugzillaError:
    return BugzillaError("improper_bug_id_field_value",
                         f"'{value}' is not a valid bug number nor an alias to a bug.")


# ---------------------------------------------------------------- lookups
BUG_SELECT = """
SELECT b.*, p.name AS product_name, p.allows_unconfirmed, p.defaultmilestone, c.name AS component_name,
       cl.name AS classification_name, bs.is_open, bs.id AS status_id
  FROM bugs b
  JOIN products p ON p.id = b.product_id
  JOIN components c ON c.id = b.component_id
  JOIN classifications cl ON cl.id = p.classification_id
  JOIN bug_status bs ON bs.value = b.bug_status
"""


def bug_row_by_id(db: Session, bug_id: int):
    return db.execute(text(BUG_SELECT + " WHERE b.bug_id = :id"), {"id": bug_id}).mappings().first()


def find_bug(db: Session, id_or_alias, field: str | None = None):
    """Bugzilla::Bug->new / ->check without the visibility check."""
    value = "" if id_or_alias is None else str(id_or_alias).strip()
    if value.isdigit():
        row = bug_row_by_id(db, int(value))
        if not row:
            raise bug_does_not_exist(int(value))
        return row
    if value:
        row = db.execute(text(BUG_SELECT + " JOIN bugs_aliases ba ON ba.bug_id = b.bug_id WHERE ba.alias = :a"),
                         {"a": value}).mappings().first()
        if row:
            return row
    raise improper_bug_id(value)


def can_see_bug(db: Session, user: User, row) -> bool:
    groups = {r[0] for r in db.execute(text("SELECT group_id FROM bug_group_map WHERE bug_id = :b"),
                                       {"b": row["bug_id"]})}
    if groups <= user.group_ids:
        return True
    if not user.is_logged_in:
        return False
    if (row["reporter"] == user.id and row["reporter_accessible"]) or row["assigned_to"] == user.id \
            or row["qa_contact"] == user.id:
        return True
    if row["cclist_accessible"] and db.execute(
            text("SELECT 1 FROM cc WHERE bug_id = :b AND who = :u"), {"b": row["bug_id"], "u": user.id}).first():
        return True
    return False


def check_bug(ctx: Ctx, id_or_alias, field: str | None = None):
    """Bugzilla::Bug->check: lookup + visibility (skipped for dependency/dup fields like Perl)."""
    row = find_bug(ctx.db, id_or_alias, field)
    if field not in ("dependson", "blocked", "dup_id") and not can_see_bug(ctx.db, ctx.user, row):
        shown = id_or_alias if id_or_alias is not None else row["bug_id"]
        if ctx.user.is_logged_in:
            raise BugzillaError("bug_access_denied", f"You are not authorized to access bug {shown}.")
        raise BugzillaError("bug_access_query", f"You are not authorized to access bug #{shown}. To see this bug, "
                            "you must first log in to an account with the appropriate permissions.")
    return row


def visibility_sql(user: User) -> tuple[str, dict]:
    """WHERE fragment restricting bugs b to the ones ``user`` may see (Bugzilla::User::visible_bugs, AND mode)."""
    binds: dict = {}
    if user.group_ids:
        gids = ",".join(str(g) for g in sorted(user.group_ids))
        cond = f"NOT EXISTS (SELECT 1 FROM bug_group_map bgm WHERE bgm.bug_id = b.bug_id AND bgm.group_id NOT IN ({gids}))"
    else:
        cond = "NOT EXISTS (SELECT 1 FROM bug_group_map bgm WHERE bgm.bug_id = b.bug_id)"
    if user.is_logged_in:
        binds["vis_uid"] = user.id
        cond = (f"({cond} OR (b.reporter = :vis_uid AND b.reporter_accessible = 1) OR b.assigned_to = :vis_uid "
                "OR b.qa_contact = :vis_uid OR (b.cclist_accessible = 1 AND EXISTS "
                "(SELECT 1 FROM cc WHERE cc.bug_id = b.bug_id AND cc.who = :vis_uid)))")
    return cond, binds


def user_hash(db: Session, user_id: int | None) -> dict | None:
    if not user_id:
        return None
    row = db.execute(text("SELECT userid, login_name, realname FROM profiles WHERE userid = :u"),
                     {"u": user_id}).first()
    if not row:
        return None
    return {"id": row.userid, "real_name": row.realname or "", "name": row.login_name,
            "email": row.login_name + (param("emailsuffix") or "")}


def login_of(db: Session, user_id: int | None) -> str:
    if not user_id:
        return ""
    return db.execute(text("SELECT login_name FROM profiles WHERE userid = :u"), {"u": user_id}).scalar() or ""


def aliases_of(db: Session, bug_id: int) -> list[str]:
    return [r[0] for r in db.execute(text("SELECT alias FROM bugs_aliases WHERE bug_id = :b ORDER BY alias"),
                                     {"b": bug_id})]


def cc_users(db: Session, bug_id: int) -> list:
    return list(db.execute(text("SELECT p.userid, p.login_name, p.realname FROM cc JOIN profiles p ON p.userid = cc.who "
                                "WHERE cc.bug_id = :b ORDER BY p.login_name"), {"b": bug_id}))


def blocked_of(db: Session, bug_id: int) -> list[int]:
    return [r[0] for r in db.execute(text("SELECT blocked FROM dependencies WHERE dependson = :b ORDER BY blocked"),
                                     {"b": bug_id})]


def dependson_of(db: Session, bug_id: int) -> list[int]:
    return [r[0] for r in db.execute(text("SELECT dependson FROM dependencies WHERE blocked = :b ORDER BY dependson"),
                                     {"b": bug_id})]


def dup_id_of(db: Session, bug_id: int) -> int | None:
    return db.execute(text("SELECT dupe_of FROM duplicates WHERE dupe = :b"), {"b": bug_id}).scalar()


def keywords_of(db: Session, bug_id: int) -> list[str]:
    return [r[0] for r in db.execute(text("SELECT kd.name FROM keywords k JOIN keyworddefs kd ON kd.id = k.keywordid "
                                          "WHERE k.bug_id = :b ORDER BY kd.name"), {"b": bug_id})]


def num(value) -> int | float:
    """Perl's JSON prints 0.00 as 0; keep integral doubles as ints."""
    f = float(value or 0)
    return int(f) if f.is_integer() else f


def date_str(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.strftime("%Y-%m-%d")
    return str(value)[:10]


def bug_to_hash(ctx: Ctx, row) -> dict:
    db, f, user = ctx.db, ctx.filter, ctx.user
    bug_id = row["bug_id"]
    item = f.apply({
        "deadline": date_str(row["deadline"]), "id": bug_id, "is_confirmed": bool(row["everconfirmed"]),
        "op_sys": row["op_sys"], "platform": row["rep_platform"], "priority": row["priority"],
        "resolution": row["resolution"], "severity": row["bug_severity"], "status": row["bug_status"],
        "summary": row["short_desc"], "target_milestone": row["target_milestone"], "url": row["bug_file_loc"],
        "version": row["version"], "whiteboard": row["status_whiteboard"],
    })
    if f.wants("alias"):
        item["alias"] = aliases_of(db, bug_id)
    if f.wants("assigned_to"):
        u = user_hash(db, row["assigned_to"])
        item["assigned_to"] = u["name"]
        item["assigned_to_detail"] = f.apply(u, prefix="assigned_to")
    if f.wants("blocks"):
        item["blocks"] = blocked_of(db, bug_id)
    if f.wants("classification"):
        item["classification"] = row["classification_name"]
    if f.wants("component"):
        item["component"] = row["component_name"]
    if f.wants("cc"):
        ccs = cc_users(db, bug_id)
        item["cc"] = [c.login_name for c in ccs]
        suffix = param("emailsuffix") or ""
        item["cc_detail"] = [f.apply({"id": c.userid, "real_name": c.realname or "", "name": c.login_name,
                                      "email": c.login_name + suffix}, prefix="cc") for c in ccs]
    if f.wants("creation_time"):
        item["creation_time"] = iso_z(row["creation_ts"])
    if f.wants("creator"):
        u = user_hash(db, row["reporter"])
        item["creator"] = u["name"]
        item["creator_detail"] = f.apply(u, prefix="creator")
    if f.wants("depends_on"):
        item["depends_on"] = dependson_of(db, bug_id)
    if f.wants("dupe_of"):
        item["dupe_of"] = dup_id_of(db, bug_id)
    if f.wants("groups"):
        item["groups"] = [r[0] for r in db.execute(text(
            "SELECT g.name FROM bug_group_map m JOIN groups g ON g.id = m.group_id WHERE m.bug_id = :b ORDER BY g.name"),
            {"b": bug_id})]
    if f.wants("is_open"):
        item["is_open"] = bool(row["is_open"])
    if f.wants("keywords"):
        item["keywords"] = keywords_of(db, bug_id)
    if f.wants("last_change_time"):
        item["last_change_time"] = iso_z(row["delta_ts"])
    if f.wants("product"):
        item["product"] = row["product_name"]
    if f.wants("qa_contact"):
        u = user_hash(db, row["qa_contact"])
        item["qa_contact"] = u["name"] if u else ""
        if u:
            item["qa_contact_detail"] = f.apply(u, prefix="qa_contact")
    if f.wants("see_also"):
        item["see_also"] = [r[0] for r in db.execute(text(
            "SELECT value FROM bug_see_also WHERE bug_id = :b ORDER BY id"), {"b": bug_id})]
    if f.wants("flags"):
        item["flags"] = []
    if f.wants("tags", "extra"):
        item["tags"] = [r[0] for r in db.execute(text(
            "SELECT t.name FROM bug_tag bt JOIN tag t ON t.id = bt.tag_id WHERE bt.bug_id = :b AND t.user_id = :u "
            "ORDER BY t.name"), {"b": bug_id, "u": user.id})] if user.is_logged_in else []
    if user.is_timetracker:
        if f.wants("estimated_time"):
            item["estimated_time"] = num(row["estimated_time"])
        if f.wants("remaining_time"):
            item["remaining_time"] = num(row["remaining_time"])
        if f.wants("actual_time"):
            item["actual_time"] = num(db.execute(text(
                "SELECT SUM(work_time) FROM longdescs WHERE bug_id = :b"), {"b": bug_id}).scalar() or 0)
    if f.wants("is_cc_accessible"):
        item["is_cc_accessible"] = bool(row["cclist_accessible"])
    if f.wants("is_creator_accessible"):
        item["is_creator_accessible"] = bool(row["reporter_accessible"])
    return item


# ---------------------------------------------------------------- Bug.get / Bug.search
def get(ctx: Ctx, ids=None) -> dict:
    ids = ids if ids is not None else ctx.param_list("ids")
    if not ids and "ids" not in ctx.params:
        raise param_required("ids")
    bugs, faults = [], []
    for bug_id in ids:
        try:
            row = check_bug(ctx, bug_id)
        except BugzillaError as err:
            if ctx.params.get("permissive"):
                faults.append({"id": bug_id, "faultString": err.message, "faultCode": err.code})
                continue
            raise
        bugs.append(bug_to_hash(ctx, row))
    return {"bugs": bugs, "faults": faults}


IGNORED_SEARCH_PARAMS = {"include_fields", "exclude_fields", "callback", "_remote_ip", "Bugzilla_login",
                         "Bugzilla_password", "Bugzilla_api_key", "Bugzilla_token", "restrict_login", "ids"}
SIMPLE_EQ = {"status": "b.bug_status", "bug_status": "b.bug_status", "resolution": "b.resolution",
             "priority": "b.priority", "severity": "b.bug_severity", "bug_severity": "b.bug_severity",
             "op_sys": "b.op_sys", "platform": "b.rep_platform", "rep_platform": "b.rep_platform",
             "version": "b.version", "target_milestone": "b.target_milestone", "product": "p.name",
             "component": "c.name", "classification": "cl.name"}
SUBSTR = {"summary": "b.short_desc", "short_desc": "b.short_desc", "whiteboard": "b.status_whiteboard",
          "status_whiteboard": "b.status_whiteboard", "url": "b.bug_file_loc", "bug_file_loc": "b.bug_file_loc"}
ROLE = {"assigned_to": "pa.login_name", "creator": "pr.login_name", "reporter": "pr.login_name",
        "qa_contact": "pq.login_name"}


def _values(value) -> list[str]:
    return [str(v) for v in as_list(value)]


def search(ctx: Ctx) -> dict:
    p = {k: v for k, v in ctx.params.items() if k not in IGNORED_SEARCH_PARAMS}
    binds: dict = {}
    where: list[str] = []
    n = 0

    def bind(v):
        nonlocal n
        n += 1
        binds[f"v{n}"] = v
        return f":v{n}"

    limit = p.pop("limit", None)
    offset = p.pop("offset", None)
    if offset is not None and limit is None:
        raise param_required("limit", "The function Bug.search()")
    order = p.pop("order", None)
    for key, value in p.items():
        if key in ("id", "bug_id"):
            ids = [to_int(x) for v in _values(value) for x in v.split(",")]
            ids = [i for i in ids if i is not None]
            where.append("b.bug_id IN (" + ",".join(bind(i) for i in ids) + ")" if ids else "1=0")
        elif key == "alias":
            vals = _values(value)
            where.append("EXISTS (SELECT 1 FROM bugs_aliases ba WHERE ba.bug_id = b.bug_id AND ba.alias IN ("
                         + ",".join(bind(v) for v in vals) + "))")
        elif key in SIMPLE_EQ:
            vals = _values(value)
            where.append(f"{SIMPLE_EQ[key]} IN (" + ",".join(bind(v) for v in vals) + ")")
        elif key in SUBSTR:
            for word in " ".join(_values(value)).split():
                where.append(f"{SUBSTR[key]} LIKE {bind('%' + word + '%')}")
        elif key in ROLE:
            words = " ".join(_values(value)).split()
            if words:
                where.append("(" + " OR ".join(f"{ROLE[key]} LIKE {bind('%' + w + '%')}" for w in words) + ")")
        elif key == "cc":
            words = " ".join(_values(value)).split()
            if words:
                where.append("EXISTS (SELECT 1 FROM cc JOIN profiles pc ON pc.userid = cc.who WHERE cc.bug_id = b.bug_id AND ("
                             + " OR ".join(f"pc.login_name LIKE {bind('%' + w + '%')}" for w in words) + "))")
        elif key == "longdesc":
            words = " ".join(_values(value)).split()
            if words:
                where.append("EXISTS (SELECT 1 FROM longdescs ld WHERE ld.bug_id = b.bug_id AND ("
                             + " OR ".join(f"ld.thetext LIKE {bind('%' + w + '%')}" for w in words) + "))")
        elif key in ("creation_time", "creation_ts"):
            where.append(f"b.creation_ts >= {bind(_values(value)[0])}")
        elif key in ("last_change_time", "delta_ts"):
            where.append(f"b.delta_ts >= {bind(_values(value)[0])}")
        elif key == "keywords":
            for word in " ".join(_values(value)).split():
                where.append("EXISTS (SELECT 1 FROM keywords k JOIN keyworddefs kd ON kd.id = k.keywordid "
                             f"WHERE k.bug_id = b.bug_id AND kd.name = {bind(word)})")
        # anything else is silently ignored, like Bugzilla::Search does for unknown params
    vis, vis_binds = visibility_sql(ctx.user)
    where.append(vis)
    binds.update(vis_binds)
    sql = (BUG_SELECT + " JOIN profiles pa ON pa.userid = b.assigned_to JOIN profiles pr ON pr.userid = b.reporter "
           "LEFT JOIN profiles pq ON pq.userid = b.qa_contact WHERE " + " AND ".join(where))
    order_cols = {"bug_id": "b.bug_id", "id": "b.bug_id", "priority": "b.priority", "severity": "b.bug_severity",
                  "status": "b.bug_status", "last_change_time": "b.delta_ts", "creation_time": "b.creation_ts",
                  "summary": "b.short_desc", "assigned_to": "pa.login_name"}
    order_sql = []
    for part in (str(order).split(",") if order else []):
        name, _, direction = part.strip().partition(" ")
        if name in order_cols:
            order_sql.append(order_cols[name] + (" DESC" if direction.strip().upper() == "DESC" else ""))
    sql += " ORDER BY " + (", ".join(order_sql) + ", b.bug_id" if order_sql else "b.bug_id")
    max_results = to_int(param("max_search_results"), 10000)
    lim = to_int(limit)
    if lim is None or lim > max_results:
        lim = max_results
    if lim:
        sql += f" LIMIT {lim}"
        off = to_int(offset)
        if off:
            sql += f" OFFSET {off}"
    rows = ctx.db.execute(text(sql), binds).mappings().all()
    return {"bugs": [bug_to_hash(ctx, r) for r in rows]}


# ---------------------------------------------------------------- comments
def comment_text(db: Session, row) -> str:
    """Bugzilla::Comment::body_full (bug/format_comment.txt.tmpl)."""
    body = row["thetext"] or ""
    ctype = row["type"] or 0
    extra = row["extra_data"]
    if ctype == CMT_DUPE_OF:
        return f"\n{body}\n*** This bug has been marked as a duplicate of bug {extra} ***"
    if ctype == CMT_HAS_DUPE:
        return f"*** Bug {extra} has been marked as a duplicate of this bug. ***"
    if ctype == CMT_ATTACHMENT_CREATED:
        desc = db.execute(text("SELECT description FROM attachments WHERE attach_id = :a"), {"a": extra}).scalar() or ""
        return f"Created attachment {extra}\n{desc}" + (f"\n\n{body}" if body else "")
    if ctype == CMT_ATTACHMENT_UPDATED:
        return f"Comment on attachment {extra}\n" + (f"\n{body}" if body else "")
    return body


COMMENT_SELECT = ("SELECT l.comment_id, l.bug_id, l.who, p.login_name, l.bug_when, l.thetext, l.isprivate, l.type, "
                  "l.extra_data FROM longdescs l JOIN profiles p ON p.userid = l.who ")


def comment_hash(ctx: Ctx, row, count: int) -> dict:
    is_attach = row["type"] in (CMT_ATTACHMENT_CREATED, CMT_ATTACHMENT_UPDATED)
    item = {
        "id": row["comment_id"], "bug_id": row["bug_id"], "creator": row["login_name"],
        "time": iso_z(row["bug_when"]), "creation_time": iso_z(row["bug_when"]),
        "is_private": bool(row["isprivate"]), "text": comment_text(ctx.db, row),
        "attachment_id": to_int(row["extra_data"]) if is_attach else None, "count": count,
    }
    if param("comment_taggers_group"):
        item["tags"] = [r[0] for r in ctx.db.execute(text(
            "SELECT tag FROM longdescs_tags WHERE comment_id = :c ORDER BY tag"), {"c": row["comment_id"]})]
    return ctx.filter.apply(item)


def bug_comments(db: Session, bug_id: int):
    return db.execute(text(COMMENT_SELECT + "WHERE l.bug_id = :b ORDER BY l.bug_when, l.comment_id"),
                      {"b": bug_id}).mappings().all()


def comment_count(all_rows, row) -> int:
    return sum(1 for r in all_rows if r["bug_when"] <= row["bug_when"]) - 1


def comments(ctx: Ctx, ids=None, comment_ids=None) -> dict:
    if ids is None and comment_ids is None:
        raise params_required("Bug.comments", ["ids", "comment_ids"])
    insider = ctx.user.is_insider
    new_since = ctx.params.get("new_since")
    bugs: dict = {}
    for bug_id in ids or []:
        row = check_bug(ctx, bug_id)
        all_rows = bug_comments(ctx.db, row["bug_id"])
        out = []
        for count, c in enumerate(all_rows):
            if c["isprivate"] and not insider:
                continue
            if new_since and iso_z(c["bug_when"]) <= _norm_since(new_since):
                continue
            out.append(comment_hash(ctx, c, count))
        bugs[str(row["bug_id"])] = {"comments": out}
    result: dict = {}
    if comment_ids:
        found = {}
        for cid in comment_ids:
            cid = str(cid).strip()
            crow = ctx.db.execute(text(COMMENT_SELECT + "WHERE l.comment_id = :c"),
                                  {"c": to_int(cid, -1)}).mappings().first()
            if not crow:
                raise BugzillaError("comment_id_invalid", f"{cid} is not a valid comment id.")
            found[cid] = crow
        for crow in found.values():
            check_bug(ctx, crow["bug_id"])
        for crow in found.values():
            if crow["isprivate"] and not insider:
                raise BugzillaError("comment_is_private", f"Comment id {crow['comment_id']} is private.")
            all_rows = bug_comments(ctx.db, crow["bug_id"])
            result[str(crow["comment_id"])] = comment_hash(ctx, crow, comment_count(all_rows, crow))
    return {"bugs": bugs, "comments": result}


def _norm_since(value) -> str:
    s = str(value).strip().replace(" ", "T")
    if len(s) == 10:
        s += "T00:00:00"
    if not s.endswith("Z"):
        s += "Z"
    return s


# ---------------------------------------------------------------- history
def history(ctx: Ctx, ids=None) -> dict:
    ids = ids if ids is not None else ctx.param_list("ids")
    if not ids and "ids" not in ctx.params:
        raise param_required("ids")
    new_since = ctx.params.get("new_since")
    out = []
    for bug_id in ids:
        row = check_bug(ctx, bug_id)
        out.append({"id": row["bug_id"], "alias": aliases_of(ctx.db, row["bug_id"]),
                    "history": activity(ctx, row["bug_id"], new_since)})
    return {"bugs": out}


def activity(ctx: Ctx, bug_id: int, new_since=None, attach_id: int | None = None) -> list[dict]:
    """Bugzilla::Bug::get_activity grouped into changesets."""
    user = ctx.user
    binds: dict = {"b": bug_id}
    sql = ("SELECT f.name, a.attach_id, a.bug_when, a.removed, a.added, p.login_name, a.comment_id FROM bugs_activity a ")
    if not user.is_insider:
        sql += "LEFT JOIN attachments att ON att.attach_id = a.attach_id "
    sql += "JOIN fielddefs f ON f.id = a.fieldid JOIN profiles p ON p.userid = a.who WHERE a.bug_id = :b "
    if new_since:
        sql += "AND a.bug_when > :since "
        binds["since"] = _norm_since(new_since).replace("T", " ").rstrip("Z")
    if attach_id:
        sql += "AND a.attach_id = :att "
        binds["att"] = attach_id
    if not user.is_insider:
        sql += "AND COALESCE(att.isprivate, 0) = 0 "
    sql += "ORDER BY a.bug_when, a.comment_id"
    operations: list[dict] = []
    current: dict | None = None
    for name, aid, when, removed, added, who, comment_id in ctx.db.execute(text(sql), binds):
        if name in TIMETRACKING_FIELDS and not user.is_timetracker:
            continue
        if name == "longdescs.isprivate" and not user.is_insider and added:
            continue
        removed, added = removed or "", added or ""
        if current and (who != current["who"] or when != current["_when"]):
            operations.append(current)
            current = None
        if current is None:
            current = {"who": who, "when": iso_z(when), "_when": when, "changes": []}
        changes = current["changes"]
        if changes and changes[-1]["_name"] == name and (changes[-1].get("_cid") or 0) == (comment_id or 0) \
                and (changes[-1].get("attachment_id") or 0) == (aid or 0):
            prev = changes.pop()
            removed = _join_activity(prev["removed"], removed)
            added = _join_activity(prev["added"], added)
        change = {"field_name": API_NAME.get(name, name), "removed": removed, "added": added,
                  "_name": name, "_cid": comment_id}
        if aid:
            change["attachment_id"] = aid
        changes.append(change)
    if current:
        operations.append(current)
    for op in operations:
        op.pop("_when")
        for ch in op["changes"]:
            ch.pop("_name")
            ch.pop("_cid")
    return operations


def _join_activity(old: str, new: str) -> str:
    if not old:
        return new
    if not new:
        return old
    return f"{old}, {new}"


# ---------------------------------------------------------------- attachments
ATTACH_SELECT = ("SELECT a.attach_id, a.bug_id, a.creation_ts, a.modification_time, a.description, a.mimetype, "
                 "a.ispatch, a.filename, a.submitter_id, a.isobsolete, a.isprivate, p.login_name, "
                 "LENGTH(d.thedata) AS size FROM attachments a JOIN profiles p ON p.userid = a.submitter_id "
                 "LEFT JOIN attach_data d ON d.id = a.attach_id ")


def attachment_hash(ctx: Ctx, row) -> dict:
    f = ctx.filter
    item = f.apply({
        "creation_time": iso_z(row["creation_ts"]), "last_change_time": iso_z(row["modification_time"]),
        "id": row["attach_id"], "bug_id": row["bug_id"], "file_name": row["filename"],
        "summary": row["description"], "content_type": row["mimetype"], "is_private": int(row["isprivate"]),
        "is_obsolete": int(row["isobsolete"]), "is_patch": int(row["ispatch"]),
    })
    if f.wants("creator"):
        item["creator"] = row["login_name"]
    if f.wants("data"):
        data = ctx.db.execute(text("SELECT thedata FROM attach_data WHERE id = :a"), {"a": row["attach_id"]}).scalar()
        item["data"] = base64.b64encode(data or b"").decode()
    if f.wants("size"):
        item["size"] = int(row["size"] or 0)
    if f.wants("flags"):
        item["flags"] = []
    return item


def attachments(ctx: Ctx, ids=None, attachment_ids=None) -> dict:
    if ids is None and attachment_ids is None:
        raise params_required("Bug.attachments", ["ids", "attachment_ids"])
    insider = ctx.user.is_insider
    bugs: dict = {}
    for bug_id in ids or []:
        row = check_bug(ctx, bug_id)
        rows = ctx.db.execute(text(ATTACH_SELECT + "WHERE a.bug_id = :b ORDER BY a.attach_id"),
                              {"b": row["bug_id"]}).mappings().all()
        bugs[str(row["bug_id"])] = [attachment_hash(ctx, a) for a in rows if insider or not a["isprivate"]]
    result: dict = {}
    for aid in attachment_ids or []:
        arow = ctx.db.execute(text(ATTACH_SELECT + "WHERE a.attach_id = :a"),
                              {"a": to_int(aid, -1)}).mappings().first()
        if not arow:
            continue
        check_bug(ctx, arow["bug_id"])
        if arow["isprivate"] and not insider:
            raise BugzillaError("auth_failure", f"Sorry, you are not authorized to access attachment #{arow['attach_id']}.")
        result[str(arow["attach_id"])] = attachment_hash(ctx, arow)
    return {"bugs": bugs, "attachments": result}


# ---------------------------------------------------------------- fields / legal_values
FIELD_SELECT = ("SELECT id, name, type, custom, description, enter_bug, is_mandatory, visibility_field_id, "
                "value_field_id, obsolete FROM fielddefs ")


def _field_name(db: Session, field_id: int | None) -> str | None:
    if not field_id:
        return None
    return db.execute(text("SELECT name FROM fielddefs WHERE id = :i"), {"i": field_id}).scalar()


def _select_table(name: str) -> str:
    if not IDENT_RE.match(name):
        raise BugzillaError("invalid_field_name", f"Can't use \"{name}\" as a field name.")
    return name


def _choice_values(db: Session, field) -> list[dict]:
    if field["name"] == "classification":
        return [{"name": r.name, "sort_key": r.sortkey, "sortkey": r.sortkey, "visibility_values": []}
                for r in db.execute(text("SELECT name, sortkey FROM classifications ORDER BY sortkey, name"))]
    table = _select_table(field["name"])
    vis_field = _field_name(db, field["value_field_id"])
    out = []
    for value, sortkey, vis_id in db.execute(text(
            f"SELECT value, sortkey, visibility_value_id FROM {table} ORDER BY sortkey, value")):
        vis_values = []
        if vis_id is not None and vis_field:
            vis_name = db.execute(text(f"SELECT {'name' if vis_field in ('product', 'component', 'classification') else 'value'} "
                                       f"FROM {_select_table(vis_field) if vis_field not in ('product', 'component', 'classification') else vis_field + 's'} "
                                       "WHERE id = :i"), {"i": vis_id}).scalar()
            vis_values = [vis_name] if vis_name is not None else []
        out.append({"name": value, "sort_key": sortkey, "sortkey": sortkey, "visibility_values": vis_values})
    return out


def _visible_products(ctx: Ctx) -> list:
    rows = ctx.db.execute(text("SELECT id, name FROM products ORDER BY name")).all()
    restricted: dict[int, set[int]] = {}
    for pid, gid in ctx.db.execute(text("SELECT product_id, group_id FROM group_control_map WHERE entry = 1 OR membercontrol = 3")):
        restricted.setdefault(pid, set()).add(gid)
    return [r for r in rows if restricted.get(r.id, set()) <= ctx.user.group_ids]


def _product_specific_values(ctx: Ctx, name: str) -> list[dict]:
    db = ctx.db
    visible = {r.id: r.name for r in _visible_products(ctx)}
    if name == "version":
        rows = db.execute(text("SELECT value AS name, product_id, isactive, 0 AS sortkey FROM versions ORDER BY value")).all()
    elif name == "component":
        rows = db.execute(text("SELECT name, product_id, isactive, 0 AS sortkey FROM components ORDER BY name")).all()
    else:
        rows = db.execute(text("SELECT value AS name, product_id, isactive, sortkey FROM milestones ORDER BY sortkey, value")).all()
    return [{"name": r.name, "sort_key": r.sortkey, "sortkey": r.sortkey, "visibility_values": [visible[r.product_id]],
             "is_active": bool(r.isactive)} for r in rows if r.product_id in visible]


def _status_values(db: Session) -> list[dict]:
    statuses = db.execute(text("SELECT id, value, sortkey, is_open FROM bug_status WHERE isactive = 1 ORDER BY sortkey")).all()
    by_id = {s.id: s for s in statuses}
    workflow = db.execute(text("SELECT old_status, new_status, require_comment FROM status_workflow")).all()

    def transitions(old_id):
        rows = [w for w in workflow if w.old_status == old_id and w.new_status in by_id]
        rows.sort(key=lambda w: by_id[w.new_status].sortkey)
        return [{"name": by_id[w.new_status].value, "comment_required": bool(w.require_comment)} for w in rows
                if w.new_status != old_id]

    out = [{"name": None, "is_open": True, "sort_key": 0, "sortkey": 0, "can_change_to": transitions(None),
            "visibility_values": []}]
    for s in statuses:
        out.append({"name": s.value, "is_open": bool(s.is_open), "sort_key": s.sortkey, "sortkey": s.sortkey,
                    "can_change_to": transitions(s.id), "visibility_values": []})
    return out


def field_hash(ctx: Ctx, field) -> dict:
    db = ctx.db
    name = field["name"]
    vis_field = _field_name(db, field["visibility_field_id"])
    vis_values: list[str] = []
    if field["visibility_field_id"]:
        vtable = _select_table(vis_field) if vis_field not in ("product", "component", "classification") else vis_field + "s"
        col = "name" if vis_field in ("product", "component", "classification") else "value"
        vis_values = [r[0] for r in db.execute(text(
            f"SELECT v.{col} FROM field_visibility fv JOIN {vtable} v ON v.id = fv.value_id WHERE fv.field_id = :f"),
            {"f": field["id"]})]
    value_field = _field_name(db, field["value_field_id"])
    is_select = field["type"] in (2, 3)
    values, has_values = None, False
    if name in PRODUCT_SPECIFIC_FIELDS:
        has_values, values, value_field = True, _product_specific_values(ctx, name), "product"
    elif name == "bug_status":
        has_values, values = True, _status_values(db)
    elif name == "keywords":
        has_values = True
        values = [{"name": r.name, "description": r.description} for r in db.execute(text(
            "SELECT name, description FROM keyworddefs ORDER BY name"))]
    elif is_select and name != "product":
        has_values, values = True, _choice_values(db, field)
    data = {"id": field["id"], "type": field["type"], "is_custom": bool(field["custom"]), "name": name,
            "display_name": field["description"], "is_mandatory": bool(field["is_mandatory"]),
            "is_on_bug_entry": bool(field["enter_bug"]), "visibility_field": vis_field,
            "visibility_values": vis_values}
    if has_values:
        data["value_field"] = value_field
        data["values"] = values
    return ctx.filter.apply(data)


def fields(ctx: Ctx, ids=None, names=None) -> dict:
    db = ctx.db
    found = []
    for fid in ids or []:
        row = db.execute(text(FIELD_SELECT + "WHERE id = :i"), {"i": to_int(fid, -1)}).mappings().first()
        if not row:
            raise object_does_not_exist("field", fid, by_id=True)
        found.append(row)
    for fname in names or []:
        row = db.execute(text(FIELD_SELECT + "WHERE name = :n"), {"n": str(fname).strip()}).mappings().first()
        if not row:
            raise object_does_not_exist("field", fname)
        if not any(f["id"] == row["id"] for f in found):
            found.append(row)
    if ids is None and names is None:
        found = db.execute(text(FIELD_SELECT + "WHERE obsolete = 0 ORDER BY sortkey, name")).mappings().all()
    return {"fields": [field_hash(ctx, f) for f in found]}


def legal_values(ctx: Ctx, field: str | None, product_id=None) -> dict:
    db = ctx.db
    if field is None:
        raise param_required("field")
    name = FIELD_MAP.get(field, field)
    global_selects = {r[0] for r in db.execute(text("SELECT name FROM fielddefs WHERE type IN (2, 3)"))} - set(ABNORMAL_SELECTS)
    if name in global_selects:
        table = _select_table(name)
        values = [r[0] for r in db.execute(text(f"SELECT value FROM {table} WHERE isactive = 1 ORDER BY sortkey, value"))]
    elif name in PRODUCT_SPECIFIC_FIELDS:
        if product_id is None:
            raise param_required("product_id", "Bug.legal_values")
        pid = to_int(product_id, -1)
        if pid not in {r.id for r in _visible_products(ctx)}:
            raise BugzillaError("product_access_denied",
                                f"Either the product with the id {product_id} does not exist or you don't have access to it.")
        if name == "version":
            sql = "SELECT value FROM versions WHERE product_id = :p ORDER BY value"
        elif name == "component":
            sql = "SELECT name FROM components WHERE product_id = :p ORDER BY name"
        else:
            sql = "SELECT value FROM milestones WHERE product_id = :p ORDER BY sortkey, value"
        values = [r[0] for r in db.execute(text(sql), {"p": pid})]
    else:
        raise BugzillaError("invalid_field_name", f"Can't use \"{field}\" as a field name.")
    return {"values": values}
