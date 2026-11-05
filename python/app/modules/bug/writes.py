"""Write side of Bugzilla::WebService::Bug: create, update, add_comment, add_attachment.

Ports the validators in Bugzilla/Bug.pm (_check_*), Bugzilla::Bug::update's activity logging
and Bugzilla::Attachment->create. Everything runs in the request's transaction (app.core.db.get_db).
"""
import base64
import re
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.auth import User
from app.core.errors import BugzillaError, param_required
from app.core.params import param
from app.core.rest import Ctx
from app.core.util import as_list, iso_z, to_int
from app.modules.bug.service import (API_NAME, CMT_ATTACHMENT_CREATED, CMT_DUPE_OF, CMT_HAS_DUPE,
                                     CMT_NORMAL, FIELD_MAP, aliases_of, blocked_of, can_see_bug,
                                     check_bug, dependson_of, dup_id_of, find_bug, keywords_of, login_of,
                                     object_does_not_exist, object_not_specified)

LEGAL_CONTENT_TYPES = ("application", "audio", "image", "message", "model", "multipart", "text", "video")
CONTENT_TYPE_RE = re.compile(r"^(" + "|".join(LEGAL_CONTENT_TYPES) + r")/[a-z0-9_\-\+\.]+(;\s*charset=[a-z0-9_\-\+]+)?$",
                             re.I)
MAX_FREETEXT_LENGTH = 255


# ---------------------------------------------------------------- helpers
def db_now(db: Session):
    return db.execute(text("SELECT LOCALTIMESTAMP(0)")).scalar()


_fieldids: dict[str, int] = {}


def fieldid(db: Session, name: str) -> int:
    if name not in _fieldids:
        fid = db.execute(text("SELECT id FROM fielddefs WHERE name = :n"), {"n": name}).scalar()
        if fid is None:
            raise BugzillaError("invalid_field_name", f"Can't use \"{name}\" as a field name.")
        _fieldids[name] = fid
    return _fieldids[name]


def log_activity(db: Session, bug_id: int, field: str, removed, added, who: int, when, attach_id=None,
                 comment_id=None):
    """Bugzilla::Bug::LogActivityEntry (values longer than 255 chars are split like Perl does)."""
    removed, added = "" if removed is None else str(removed), "" if added is None else str(added)
    while len(removed) > 254 or len(added) > 254:
        r_cut, a_cut = _cut(removed), _cut(added)
        db.execute(text("INSERT INTO bugs_activity (bug_id, who, bug_when, fieldid, removed, added, attach_id, comment_id) "
                        "VALUES (:b, :w, :t, :f, :r, :a, :att, :c)"),
                   {"b": bug_id, "w": who, "t": when, "f": fieldid(db, field), "r": r_cut, "a": a_cut,
                    "att": attach_id, "c": comment_id})
        removed, added = removed[len(r_cut):], added[len(a_cut):]
    db.execute(text("INSERT INTO bugs_activity (bug_id, who, bug_when, fieldid, removed, added, attach_id, comment_id) "
                    "VALUES (:b, :w, :t, :f, :r, :a, :att, :c)"),
               {"b": bug_id, "w": who, "t": when, "f": fieldid(db, field), "r": removed, "a": added,
                "att": attach_id, "c": comment_id})


def _cut(value: str) -> str:
    if len(value) <= 254:
        return value
    head = value[:254]
    idx = head.rfind(", ")
    return head[: idx + 2] if idx > 0 else head


def trim(value) -> str:
    return "" if value is None else str(value).strip()


def truthy(value) -> bool:
    if isinstance(value, str):
        return value.strip().lower() not in ("", "0", "false", "no")
    return bool(value)


def check_choice(db: Session, field: str, value) -> str:
    """Bugzilla::Field::Choice->check for the global select fields (priority, bug_severity, ...)."""
    value = trim(value)
    if not value:
        raise object_not_specified(field)
    row = db.execute(text(f"SELECT value FROM {field} WHERE value = :v"), {"v": value}).first()
    if not row:
        raise object_does_not_exist(field, value)
    return row[0]


def check_user(db: Session, login) -> int:
    login = trim(login)
    if not login:
        raise object_not_specified("user")
    uid = db.execute(text("SELECT userid FROM profiles WHERE login_name = :l"), {"l": login}).scalar()
    if uid is None:
        raise object_does_not_exist("user", login)
    return uid


def check_product_for_entry(ctx: Ctx, name) -> dict:
    name = trim(name)
    row = ctx.db.execute(text("SELECT id, name, allows_unconfirmed, defaultmilestone, classification_id FROM products "
                              "WHERE name = :n"), {"n": name}).mappings().first()
    if not row or not can_enter_product(ctx, row["id"]):
        raise BugzillaError("entry_access_denied", f"Sorry, either the product {name} does not exist or you aren't "
                            "authorized to enter a bug into it.")
    return row


def can_enter_product(ctx: Ctx, product_id: int) -> bool:
    gids = {r[0] for r in ctx.db.execute(text(
        "SELECT group_id FROM group_control_map WHERE product_id = :p AND entry = 1"), {"p": product_id})}
    return gids <= ctx.user.group_ids


def can_edit_product(user: User, db: Session, product_id: int) -> bool:
    gids = {r[0] for r in db.execute(text(
        "SELECT group_id FROM group_control_map WHERE product_id = :p AND canedit != 0"), {"p": product_id})}
    return gids <= user.group_ids


def check_for_edit(ctx: Ctx, id_or_alias):
    row = check_bug(ctx, id_or_alias)
    if not can_edit_product(ctx.user, ctx.db, row["product_id"]):
        raise BugzillaError("product_edit_denied", f"Sorry, you are not allowed to edit bugs in the "
                            f"'{row['product_name']}' product.")
    return row


def check_component(db: Session, product, name) -> dict:
    name = trim(name)
    if not name:
        raise BugzillaError("require_component", "You must choose a component to file this bug in.")
    row = db.execute(text("SELECT id, name, initialowner, initialqacontact, isactive FROM components "
                          "WHERE product_id = :p AND name = :n"), {"p": product["id"], "n": name}).mappings().first()
    if not row:
        raise object_does_not_exist("component", name, product["name"])
    return row


def check_version(db: Session, product, value) -> str:
    value = trim(value)
    if not value:
        raise object_not_specified("version")
    row = db.execute(text("SELECT value FROM versions WHERE product_id = :p AND value = :v"),
                     {"p": product["id"], "v": value}).first()
    if not row:
        raise object_does_not_exist("version", value, product["name"])
    return row[0]


def check_milestone(db: Session, product, value) -> str:
    value = trim(value)
    if not value:
        raise object_not_specified("target_milestone")
    row = db.execute(text("SELECT value FROM milestones WHERE product_id = :p AND value = :v"),
                     {"p": product["id"], "v": value}).first()
    if not row:
        raise object_does_not_exist("target_milestone", value, product["name"])
    return row[0]


def check_short_desc(value) -> str:
    value = re.sub(r"\s+", " ", trim(value)) if value else ""
    if not value:
        raise BugzillaError("require_summary", "You must enter a summary for this bug.")
    if len(value) > MAX_FREETEXT_LENGTH:
        raise BugzillaError("freetext_too_long", f"The text you entered in the summary field is too long "
                            f"({len(value)} characters, above the maximum length allowed of {MAX_FREETEXT_LENGTH} characters).")
    return value


def check_aliases(db: Session, aliases, self_id: int | None = None) -> list[str]:
    if not isinstance(aliases, list):
        aliases = re.split(r"[\s,]+", str(aliases or ""))
    out = []
    for alias in aliases:
        alias = trim(alias)
        if not alias:
            continue
        if len(alias) > 40:
            raise BugzillaError("alias_too_long", "Bug aliases cannot be longer than 40 characters. "
                                "Please choose a shorter alias.")
        if alias.isdigit():
            raise BugzillaError("alias_is_numeric", f"You tried to give this bug the alias {alias}, but aliases "
                                "cannot be merely numbers, since they could then be confused with bug IDs. "
                                "Please choose an alias containing at least one letter.")
        if "," in alias or " " in alias:
            raise BugzillaError("alias_has_comma_or_space", f"The alias you entered, {alias}, contains one or more "
                                "commas or spaces. Aliases cannot contain commas or spaces because those characters "
                                "are used to separate aliases from each other in lists. Please choose an alias that "
                                "does not contain commas and spaces.")
        other = db.execute(text("SELECT bug_id FROM bugs_aliases WHERE alias = :a"), {"a": alias}).scalar()
        if other is not None and other != self_id:
            raise BugzillaError("alias_in_use", f"Bug {other} has already taken the alias {alias}. "
                                "Please choose another alias.")
        out.append(alias)
    return out


def statuses_available(db: Session, old_status_id: int | None, allows_unconfirmed: bool) -> list:
    cond = "old_status IS NULL" if old_status_id is None else "old_status = :o"
    rows = db.execute(text(f"SELECT bs.id, bs.value, bs.is_open FROM status_workflow sw JOIN bug_status bs "
                           f"ON bs.id = sw.new_status WHERE bs.isactive = 1 AND {cond} ORDER BY bs.sortkey"),
                      {"o": old_status_id}).all()
    if old_status_id is not None:
        rows = list(rows) + list(db.execute(text("SELECT id, value, is_open FROM bug_status WHERE id = :i"),
                                            {"i": old_status_id}).all())
    if not allows_unconfirmed:
        rows = [r for r in rows if r.value != "UNCONFIRMED"]
    return rows


def status_row(db: Session, value):
    value = trim(value)
    if not value:
        raise object_not_specified("bug_status")
    row = db.execute(text("SELECT id, value, is_open FROM bug_status WHERE value = :v"), {"v": value}).first()
    if not row:
        raise object_does_not_exist("bug_status", value)
    return row


def comment_required_between(db: Session, old_id: int | None, new_id: int) -> bool:
    cond = "old_status IS NULL" if old_id is None else "old_status = :o"
    return bool(db.execute(text(f"SELECT require_comment FROM status_workflow WHERE {cond} AND new_status = :n"),
                           {"o": old_id, "n": new_id}).scalar())


def check_private(user: User, value) -> int:
    if truthy(value):
        if not user.is_insider:
            raise BugzillaError("user_not_insider", "Sorry, but you are not allowed to (un)mark comments or "
                                "attachments as private.")
        return 1
    return 0


def insert_comment(db: Session, bug_id: int, who: int, when, thetext: str, isprivate: int = 0, ctype: int = CMT_NORMAL,
                   extra_data=None, work_time=0) -> int:
    db.execute(text("INSERT INTO longdescs (bug_id, who, bug_when, work_time, thetext, isprivate, already_wrapped, type, "
                    "extra_data) VALUES (:b, :w, :t, :wt, :x, :p, 0, :ty, :e)"),
               {"b": bug_id, "w": who, "t": when, "wt": work_time or 0, "x": thetext or "", "p": isprivate,
                "ty": ctype, "e": None if extra_data is None else str(extra_data)})
    return db.execute(text("SELECT LAST_INSERT_ID()")).scalar()


def sync_fulltext(db: Session, bug_id: int, new_bug: bool = False, short_desc: str | None = None,
                  comments: bool = False):
    """Bugzilla::Bug::_sync_fulltext."""
    all_text = public_text = None
    if new_bug or comments:
        rows = db.execute(text("SELECT thetext, isprivate FROM longdescs WHERE bug_id = :b ORDER BY bug_when, comment_id"),
                          {"b": bug_id}).all()
        all_text = "\n".join(r[0] or "" for r in rows)
        public_text = "\n".join(r[0] or "" for r in rows if not r[1])
    if new_bug:
        db.execute(text("INSERT INTO bugs_fulltext (bug_id, short_desc, comments, comments_noprivate) "
                        "VALUES (:b, :s, :c, :p)"), {"b": bug_id, "s": short_desc, "c": all_text, "p": public_text})
        return
    sets, binds = [], {"b": bug_id}
    if short_desc is not None:
        sets.append("short_desc = :s")
        binds["s"] = short_desc
    if comments:
        sets += ["comments = :c", "comments_noprivate = :p"]
        binds.update(c=all_text, p=public_text)
    if sets:
        db.execute(text("UPDATE bugs_fulltext SET " + ", ".join(sets) + " WHERE bug_id = :b"), binds)


def touch(db: Session, bug_id: int, when):
    db.execute(text("UPDATE bugs SET delta_ts = :t, lastdiffed = :t WHERE bug_id = :b"), {"t": when, "b": bug_id})


def map_fields(params: dict) -> dict:
    return {FIELD_MAP.get(k, k): v for k, v in params.items()}


def list_change(value) -> dict:
    """``{"add": [...], "remove": [...], "set": [...]}`` as Bug.update expects; scalars/lists mean ``set``."""
    if isinstance(value, dict):
        return {k: as_list(v) for k, v in value.items() if k in ("add", "remove", "set")}
    return {"set": as_list(value)}


def apply_list_change(current: list, change: dict) -> list:
    if "set" in change:
        return list(dict.fromkeys(change["set"]))
    out = list(current)
    for v in change.get("add", []):
        if v not in out:
            out.append(v)
    for v in change.get("remove", []):
        if v in out:
            out.remove(v)
    return out


def dep_ids(ctx: Ctx, values, field: str) -> list[int]:
    out = []
    for v in values:
        for part in re.split(r"[\s,]+", str(v)) if isinstance(v, str) else [v]:
            if part == "" or part is None:
                continue
            out.append(find_bug(ctx.db, part, field)["bug_id"])
    return out


# ---------------------------------------------------------------- Bug.create
def create(ctx: Ctx) -> dict:
    ctx.user.require_login()
    db, user = ctx.db, ctx.user
    p = map_fields({k: v for k, v in ctx.params.items() if not k.startswith("Bugzilla_") and k != "_remote_ip"})

    # Bugzilla::Object::run_create_validators order (_sort_by_dep): fields without dependencies
    # alphabetically, then dependent fields each preceded by what they depend on.
    aliases = check_aliases(db, p.get("alias")) if p.get("alias") else []
    severity = check_choice(db, "bug_severity", p.get("bug_severity", param("defaultseverity")))
    description = p.get("comment")
    description = "" if description is None else str(description)
    is_private = check_private(user, p.get("comment_is_private"))
    op_sys = check_choice(db, "op_sys", p.get("op_sys", param("defaultopsys")))
    priority = check_choice(db, "priority", p.get("priority", param("defaultpriority")))
    product = check_product_for_entry(ctx, p.get("product"))
    platform = check_choice(db, "rep_platform", p.get("rep_platform", param("defaultplatform")))
    short_desc = check_short_desc(p.get("short_desc"))
    whiteboard = trim(p.get("status_whiteboard")) if truthy(param("usestatuswhiteboard")) else ""
    url = trim(p.get("bug_file_loc"))
    reporter_accessible = 1 if p.get("reporter_accessible") is None else int(truthy(p["reporter_accessible"]))
    cclist_accessible = 1 if p.get("cclist_accessible") is None else int(truthy(p["cclist_accessible"]))

    # dependent fields: component(product), assigned_to(component), bug_status(product), cc(component), ...
    component = check_component(db, product, p.get("component"))
    editbugs = user.in_group("editbugs")
    if editbugs and trim(p.get("assigned_to")):
        assigned_to = check_user(db, p["assigned_to"])
    else:
        assigned_to = component["initialowner"]

    valid = statuses_available(db, None, bool(product["allows_unconfirmed"]))
    if editbugs or user.in_group("canconfirm"):
        if trim(p.get("bug_status")):
            status = status_row(db, p["bug_status"])
            if status.value not in {v.value for v in valid}:
                raise BugzillaError("illegal_bug_status_transition",
                                    f"You are not allowed to file new bugs with the {status.value} status.")
        else:
            status = valid[0] if len(valid) == 1 or valid[0].value != "UNCONFIRMED" else valid[1]
    else:
        status = next((v for v in valid if v.value == "UNCONFIRMED"), valid[0])
    if comment_required_between(db, None, status.id) and not description.strip():
        raise BugzillaError("comment_required", f"You have to specify a comment when changing the Status of a bug "
                            f"from (empty) to {status.value}.")
    everconfirmed = 0 if status.value == "UNCONFIRMED" else 1

    cc_ids: list[int] = []
    raw_cc = p.get("cc")
    for person in (raw_cc if isinstance(raw_cc, list) else re.split(r"[,;]+", str(raw_cc or ""))):
        if trim(person):
            uid = check_user(db, person)
            if uid not in cc_ids:
                cc_ids.append(uid)
    for (uid,) in db.execute(text("SELECT user_id FROM component_cc WHERE component_id = :c"), {"c": component["id"]}):
        if uid not in cc_ids:
            cc_ids.append(uid)

    dependson = dep_ids(ctx, as_list(p.get("dependson")), "dependson") if editbugs else []
    blocked = dep_ids(ctx, as_list(p.get("blocked")), "blocked") if editbugs else []
    keyword_ids = []
    for kw in as_list(p.get("keywords")):
        for name in (re.split(r"[\s,]+", kw) if isinstance(kw, str) else [kw]):
            if not trim(name):
                continue
            kid = db.execute(text("SELECT id FROM keyworddefs WHERE name = :n"), {"n": trim(name)}).scalar()
            if kid is None:
                raise object_does_not_exist("keyword", name)
            keyword_ids.append(kid)
    qa_contact = None
    if truthy(param("useqacontact")):
        qa_contact = check_user(db, p["qa_contact"]) if trim(p.get("qa_contact")) else component["initialqacontact"]

    resolution = trim(p.get("resolution"))
    if status.is_open:
        if resolution:
            raise BugzillaError("resolution_not_allowed", "You cannot set a resolution for open bugs.")
    else:
        if not resolution:
            raise BugzillaError("missing_resolution", f"A valid resolution is required to mark bugs as {status.value}.")
        resolution = check_choice(db, "resolution", resolution)
    milestone = check_milestone(db, product, p["target_milestone"]) if trim(p.get("target_milestone")) \
        else product["defaultmilestone"]
    version = check_version(db, product, p.get("version"))

    ts = db_now(db)
    db.execute(text(
        "INSERT INTO bugs (assigned_to, bug_file_loc, bug_severity, bug_status, creation_ts, delta_ts, short_desc, op_sys, "
        "priority, product_id, rep_platform, reporter, version, component_id, resolution, target_milestone, qa_contact, "
        "status_whiteboard, lastdiffed, everconfirmed, reporter_accessible, cclist_accessible, estimated_time, "
        "remaining_time, deadline) VALUES (:assigned_to, :url, :severity, :status, :ts, :ts, :summary, :op_sys, "
        ":priority, :product_id, :platform, :reporter, :version, :component_id, :resolution, :milestone, :qa_contact, "
        ":whiteboard, :ts, :everconfirmed, :ra, :ca, 0, 0, NULL)"),
        {"assigned_to": assigned_to, "url": url, "severity": severity, "status": status.value, "ts": ts,
         "summary": short_desc, "op_sys": op_sys, "priority": priority, "product_id": product["id"],
         "platform": platform, "reporter": user.id, "version": version, "component_id": component["id"],
         "resolution": resolution, "milestone": milestone, "qa_contact": qa_contact, "whiteboard": whiteboard,
         "everconfirmed": everconfirmed, "ra": reporter_accessible, "ca": cclist_accessible})
    bug_id = db.execute(text("SELECT LAST_INSERT_ID()")).scalar()
    for uid in cc_ids:
        db.execute(text("INSERT INTO cc (bug_id, who) VALUES (:b, :w)"), {"b": bug_id, "w": uid})
    for kid in keyword_ids:
        db.execute(text("INSERT INTO keywords (bug_id, keywordid) VALUES (:b, :k)"), {"b": bug_id, "k": kid})
    for dep in dependson:
        db.execute(text("INSERT INTO dependencies (blocked, dependson) VALUES (:b, :d)"), {"b": bug_id, "d": dep})
        log_activity(db, dep, "blocked", "", bug_id, user.id, ts)
        touch(db, dep, ts)
    for blk in blocked:
        db.execute(text("INSERT INTO dependencies (blocked, dependson) VALUES (:b, :d)"), {"b": blk, "d": bug_id})
        log_activity(db, blk, "dependson", "", bug_id, user.id, ts)
        touch(db, blk, ts)
    insert_comment(db, bug_id, user.id, ts, description, is_private)
    for alias in aliases:
        db.execute(text("INSERT INTO bugs_aliases (alias, bug_id) VALUES (:a, :b)"), {"a": alias, "b": bug_id})
    sync_fulltext(db, bug_id, new_bug=True, short_desc=short_desc)
    return {"id": bug_id}


# ---------------------------------------------------------------- Bug.update
SIMPLE_COLUMNS = {
    "short_desc": lambda ctx, row, v: check_short_desc(v),
    "status_whiteboard": lambda ctx, row, v: trim(v),
    "bug_file_loc": lambda ctx, row, v: trim(v),
    "priority": lambda ctx, row, v: check_choice(ctx.db, "priority", v),
    "bug_severity": lambda ctx, row, v: check_choice(ctx.db, "bug_severity", v),
    "op_sys": lambda ctx, row, v: check_choice(ctx.db, "op_sys", v),
    "rep_platform": lambda ctx, row, v: check_choice(ctx.db, "rep_platform", v),
    "version": lambda ctx, row, v: check_version(ctx.db, {"id": row["product_id"], "name": row["product_name"]}, v),
    "target_milestone": lambda ctx, row, v: check_milestone(ctx.db, {"id": row["product_id"], "name": row["product_name"]}, v),
}


def update(ctx: Ctx, ids=None) -> dict:
    ctx.user.require_login()
    ids = ids if ids is not None else ctx.param_list("ids")
    if not ids and "ids" not in ctx.params:
        raise param_required("ids")
    p = map_fields({k: v for k, v in ctx.params.items() if k not in ("ids", "_remote_ip") and not k.startswith("Bugzilla_")})
    p.pop("include_fields", None)
    p.pop("exclude_fields", None)
    p.pop("dependencies", None)
    rows = [check_for_edit(ctx, i) for i in ids]
    return {"bugs": [_update_one(ctx, row, dict(p)) for row in rows]}


def _update_one(ctx: Ctx, row, p: dict) -> dict:
    db, user = ctx.db, ctx.user
    bug_id = row["bug_id"]
    old = dict(row)
    new: dict = {}
    changes: dict[str, tuple[str, str]] = {}
    comments_to_add: list[dict] = []
    ts = db_now(db)

    comment = p.pop("comment", None)
    if comment is not None:
        if isinstance(comment, dict):
            body = comment.get("body", comment.get("comment"))
            is_private = check_private(user, comment.get("is_private"))
        else:
            body, is_private = comment, 0
        if trim(body):
            comments_to_add.append({"text": str(body), "private": is_private, "type": CMT_NORMAL, "extra": None})

    for col, checker in SIMPLE_COLUMNS.items():
        if col in p:
            new[col] = checker(ctx, row, p[col])
    if "component" in p:
        comp = check_component(db, {"id": row["product_id"], "name": row["product_name"]}, p["component"])
        new["component_id"] = comp["id"]
        changes_component = (row["component_name"], comp["name"])
    else:
        changes_component = None
    if "assigned_to" in p:
        if not trim(p["assigned_to"]):
            raise BugzillaError("reassign_to_empty", "To reassign a bug, you must provide an address for the new assignee.")
        new["assigned_to"] = check_user(db, p["assigned_to"])
    if truthy(p.get("reset_assigned_to")):
        new["assigned_to"] = db.execute(text("SELECT initialowner FROM components WHERE id = :c"),
                                        {"c": new.get("component_id", row["component_id"])}).scalar()
    if "qa_contact" in p and truthy(param("useqacontact")):
        new["qa_contact"] = check_user(db, p["qa_contact"]) if trim(p["qa_contact"]) else None
    if "reporter_accessible" in p:
        new["reporter_accessible"] = int(truthy(p["reporter_accessible"]))
    if "cclist_accessible" in p:
        new["cclist_accessible"] = int(truthy(p["cclist_accessible"]))
    if "deadline" in p:
        new["deadline"] = trim(p["deadline"]) or None
    if user.is_timetracker:
        for col in ("estimated_time", "remaining_time"):
            if col in p:
                try:
                    new[col] = Decimal(str(p[col]))
                except Exception:
                    raise BugzillaError("number_not_numeric", f"The value '{p[col]}' in the {col} field is not a numeric value.")

    # duplicates ------------------------------------------------------------
    dup_target = None
    if "dup_id" in p:
        dup_value = trim(p["dup_id"])
        if not dup_value:
            raise BugzillaError("undefined_field", "The dup_id field is required.", code=50)
        dup_target = find_bug(db, dup_value, "dup_id")
        if dup_target["bug_id"] == bug_id:
            raise BugzillaError("dupe_of_self", "You can't mark a bug as a duplicate of itself.")
        if dup_id_of(db, bug_id) == dup_target["bug_id"]:
            dup_target = None
        else:
            if not can_see_bug(db, user, dup_target):
                raise BugzillaError("bug_access_denied", f"You are not authorized to access bug {dup_value}.")
            if row["resolution"] != "DUPLICATE":
                p.setdefault("bug_status", param("duplicate_or_move_bug_status") or "RESOLVED")
                p["resolution"] = "DUPLICATE"

    # status / resolution -----------------------------------------------------
    old_status = db.execute(text("SELECT id, value, is_open FROM bug_status WHERE value = :v"),
                            {"v": row["bug_status"]}).first()
    new_status = old_status
    if "bug_status" in p:
        new_status = status_row(db, p["bug_status"])
        if new_status.id != old_status.id:
            valid = statuses_available(db, old_status.id, bool(row["allows_unconfirmed"]))
            if new_status.value not in {v.value for v in valid}:
                raise BugzillaError("illegal_bug_status_transition", "You are not allowed to change the bug status "
                                    f"from {old_status.value} to {new_status.value}.")
            if comment_required_between(db, old_status.id, new_status.id) and not comments_to_add:
                raise BugzillaError("comment_required", "You have to specify a comment when changing the Status of a "
                                    f"bug from {old_status.value} to {new_status.value}.")
            new["bug_status"] = new_status.value
            if new_status.value != "UNCONFIRMED":
                new["everconfirmed"] = 1
            if new_status.is_open:
                new["resolution"] = ""
    if "resolution" in p or ("bug_status" in new and not new_status.is_open):
        resolution = trim(p.get("resolution")) or (row["resolution"] if "resolution" not in p else "")
        if new_status.is_open:
            if resolution:
                raise BugzillaError("resolution_not_allowed", "You cannot set a resolution for open bugs.")
        else:
            if not resolution:
                raise BugzillaError("missing_resolution",
                                    f"A valid resolution is required to mark bugs as {new_status.value}.")
            resolution = check_choice(db, "resolution", resolution)
            new["resolution"] = resolution

    # lists ----------------------------------------------------------------
    cc_change = dep_changes = kw_change = alias_change = None
    if "cc" in p:
        current = [c.userid for c in db.execute(text("SELECT who AS userid FROM cc WHERE bug_id = :b"), {"b": bug_id})]
        change = {k: [check_user(db, x) for x in v] for k, v in list_change(p["cc"]).items()}
        cc_change = (current, apply_list_change(current, change))
    dep_changes = {}
    for field in ("dependson", "blocked"):
        if field in p:
            current = dependson_of(db, bug_id) if field == "dependson" else blocked_of(db, bug_id)
            change = {k: dep_ids(ctx, v, field) for k, v in list_change(p[field]).items()}
            wanted = apply_list_change(current, change)
            if bug_id in wanted:
                raise BugzillaError("dependency_loop_single", "You can't make a bug block or depend on itself.")
            dep_changes[field] = (current, wanted)
    if "keywords" in p:
        current = keywords_of(db, bug_id)
        names = {k: [n for x in v for n in (re.split(r"[\s,]+", x) if isinstance(x, str) else [x]) if trim(n)]
                 for k, v in list_change(p["keywords"]).items()}
        for lst in names.values():
            for n in lst:
                if db.execute(text("SELECT id FROM keyworddefs WHERE name = :n"), {"n": n}).scalar() is None:
                    raise object_does_not_exist("keyword", n)
        kw_change = (current, apply_list_change(current, names))
    if "alias" in p:
        current = aliases_of(db, bug_id)
        change = {k: check_aliases(db, v, bug_id) for k, v in list_change(p["alias"]).items()}
        alias_change = (current, apply_list_change(current, change))

    # apply: scalar columns ----------------------------------------------------
    sets, binds = [], {"b": bug_id}
    for col, value in new.items():
        if _same(old[col], value):
            continue
        sets.append(f"{col} = :{col}")
        binds[col] = value
        if col in ("assigned_to", "qa_contact"):
            changes[col] = (login_of(db, old[col]), login_of(db, value))
        elif col == "component_id":
            changes["component"] = changes_component
        elif col == "deadline":
            changes[col] = (iso_z(old[col])[:10] if old[col] else "", value or "")
        else:
            changes[col] = (_s(old[col]), _s(value))
    for field, (removed, added) in changes.items():
        log_activity(db, bug_id, field, removed, added, user.id, ts)
    # lists
    if cc_change:
        removed = sorted(set(cc_change[0]) - set(cc_change[1]))
        added = [u for u in cc_change[1] if u not in cc_change[0]]
        if removed:
            db.execute(text(f"DELETE FROM cc WHERE bug_id = :b AND who IN ({','.join(map(str, removed))})"), {"b": bug_id})
        for uid in added:
            db.execute(text("INSERT INTO cc (bug_id, who) VALUES (:b, :w)"), {"b": bug_id, "w": uid})
        if removed or added:
            changes["cc"] = (", ".join(sorted(login_of(db, u) for u in removed)),
                             ", ".join(sorted(login_of(db, u) for u in added)))
            log_activity(db, bug_id, "cc", changes["cc"][0], changes["cc"][1], user.id, ts)
    for field, (current, wanted) in dep_changes.items():
        other = "blocked" if field == "dependson" else "dependson"
        removed = [d for d in current if d not in wanted]
        added = [d for d in wanted if d not in current]
        for dep in removed:
            db.execute(text(f"DELETE FROM dependencies WHERE {field} = :d AND {other} = :b"), {"d": dep, "b": bug_id})
            log_activity(db, dep, other, bug_id, "", user.id, ts)
            touch(db, dep, ts)
        for dep in added:
            db.execute(text(f"INSERT INTO dependencies ({field}, {other}) VALUES (:d, :b)"), {"d": dep, "b": bug_id})
            log_activity(db, dep, other, "", bug_id, user.id, ts)
            touch(db, dep, ts)
        if removed or added:
            changes[field] = (", ".join(map(str, removed)), ", ".join(map(str, added)))
            log_activity(db, bug_id, field, changes[field][0], changes[field][1], user.id, ts)
    if kw_change:
        removed = [k for k in kw_change[0] if k not in kw_change[1]]
        added = [k for k in kw_change[1] if k not in kw_change[0]]
        for name in removed:
            db.execute(text("DELETE FROM keywords WHERE bug_id = :b AND keywordid = (SELECT id FROM keyworddefs WHERE name = :n)"),
                       {"b": bug_id, "n": name})
        for name in added:
            db.execute(text("INSERT INTO keywords (bug_id, keywordid) SELECT :b, id FROM keyworddefs WHERE name = :n"),
                       {"b": bug_id, "n": name})
        if removed or added:
            changes["keywords"] = (", ".join(removed), ", ".join(added))
            log_activity(db, bug_id, "keywords", changes["keywords"][0], changes["keywords"][1], user.id, ts)
    if alias_change:
        removed = [a for a in alias_change[0] if a not in alias_change[1]]
        added = [a for a in alias_change[1] if a not in alias_change[0]]
        for a in removed:
            db.execute(text("DELETE FROM bugs_aliases WHERE bug_id = :b AND alias = :a"), {"b": bug_id, "a": a})
        for a in added:
            db.execute(text("INSERT INTO bugs_aliases (alias, bug_id) VALUES (:a, :b)"), {"b": bug_id, "a": a})
        if removed or added:
            changes["alias"] = (", ".join(removed), ", ".join(added))
            log_activity(db, bug_id, "alias", changes["alias"][0], changes["alias"][1], user.id, ts)
    # duplicates
    if dup_target is not None:
        target_id = dup_target["bug_id"]
        old_dup = dup_id_of(db, bug_id) or 0
        db.execute(text("DELETE FROM duplicates WHERE dupe = :b"), {"b": bug_id})
        db.execute(text("INSERT INTO duplicates (dupe, dupe_of) VALUES (:b, :d)"), {"b": bug_id, "d": target_id})
        changes["dup_id"] = (str(old_dup) if old_dup else "", str(target_id))
        normal = [c for c in comments_to_add if c["type"] == CMT_NORMAL]
        if normal:
            normal[-1]["type"], normal[-1]["extra"] = CMT_DUPE_OF, target_id
        else:
            comments_to_add.append({"text": "", "private": 0, "type": CMT_DUPE_OF, "extra": target_id})
        # the original bug: CC the duplicate's reporter and note the duplicate
        reporter_user = User(id=row["reporter"], group_ids=set())
        if dup_target["reporter"] != row["reporter"] and (can_see_bug(db, reporter_user, dup_target) or True):
            if not db.execute(text("SELECT 1 FROM cc WHERE bug_id = :b AND who = :w"),
                              {"b": target_id, "w": row["reporter"]}).first():
                db.execute(text("INSERT INTO cc (bug_id, who) VALUES (:b, :w)"), {"b": target_id, "w": row["reporter"]})
                log_activity(db, target_id, "cc", "", login_of(db, row["reporter"]), user.id, ts)
        insert_comment(db, target_id, user.id, ts, "", 0, CMT_HAS_DUPE, bug_id)
        touch(db, target_id, ts)
        sync_fulltext(db, target_id, comments=True)

    for c in comments_to_add:
        insert_comment(db, bug_id, user.id, ts, c["text"], c["private"], c["type"], c["extra"])
    if sets:
        db.execute(text("UPDATE bugs SET " + ", ".join(sets) + " WHERE bug_id = :b"), binds)
    if changes or comments_to_add:
        touch(db, bug_id, ts)
        sync_fulltext(db, bug_id, short_desc=new.get("short_desc") if "short_desc" in changes else None,
                      comments=bool(comments_to_add))
        delta_ts = ts
    else:
        delta_ts = row["delta_ts"]
    return {"id": bug_id, "alias": aliases_of(db, bug_id), "last_change_time": iso_z(delta_ts),
            "changes": {API_NAME.get(k, k): {"removed": v[0], "added": v[1]} for k, v in changes.items()}}


def _s(value) -> str:
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return str(value.normalize()) if value != 0 else "0"
    return str(value)


def _same(a, b) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return str(a) == str(b)


# ---------------------------------------------------------------- Bug.add_comment
def add_comment(ctx: Ctx, bug_id) -> dict:
    ctx.user.require_login()
    db, user = ctx.db, ctx.user
    if bug_id is None:
        raise param_required("id")
    body = ctx.params.get("comment")
    if body is None or not str(body).strip():
        raise param_required("comment")
    row = check_for_edit(ctx, bug_id)
    private = ctx.params.get("is_private", ctx.params.get("private"))
    is_private = check_private(user, private)
    work_time = 0
    if user.is_timetracker and ctx.params.get("work_time") not in (None, "", 0, "0"):
        try:
            work_time = Decimal(str(ctx.params["work_time"]))
        except Exception:
            raise BugzillaError("number_not_numeric", f"The value '{ctx.params['work_time']}' in the work_time field "
                                "is not a numeric value.")
    ts = db_now(db)
    cid = insert_comment(db, row["bug_id"], user.id, ts, str(body), is_private, work_time=work_time)
    if work_time:
        remaining = max(Decimal(row["remaining_time"] or 0) - work_time, Decimal(0))
        db.execute(text("UPDATE bugs SET remaining_time = :r WHERE bug_id = :b"), {"r": remaining, "b": row["bug_id"]})
        log_activity(db, row["bug_id"], "work_time", "", _s(work_time), user.id, ts)
        if remaining != Decimal(row["remaining_time"] or 0):
            log_activity(db, row["bug_id"], "remaining_time", _s(row["remaining_time"]), _s(remaining), user.id, ts)
    touch(db, row["bug_id"], ts)
    sync_fulltext(db, row["bug_id"], comments=True)
    return {"id": cid}


# ---------------------------------------------------------------- Bug.add_attachment
def add_attachment(ctx: Ctx, ids=None) -> dict:
    ctx.user.require_login()
    db, user, p = ctx.db, ctx.user, ctx.params
    ids = ids if ids is not None else ctx.param_list("ids")
    if not ids and "ids" not in p:
        raise param_required("ids")
    if p.get("data") is None:
        raise param_required("data")
    rows = [check_for_edit(ctx, i) for i in ids]
    try:
        data = base64.b64decode(str(p["data"]), validate=False)
    except Exception:
        data = b""
    if not data:
        raise BugzillaError("zero_length_file", "The file you are trying to attach is empty, does not exist, or you "
                            "don't have permission to read it.")
    max_size = max(to_int(param("maxlocalattachment"), 0) * 1048576, to_int(param("maxattachmentsize"), 1000) * 1024)
    if len(data) > max_size:
        raise BugzillaError("file_too_large", f"The file you are trying to attach is {len(data) // 1024} kilobytes (KB) "
                            f"in size. Attachments cannot be more than {max_size // 1024} KB.")
    description = trim(p.get("summary"))
    if not description:
        raise BugzillaError("missing_attachment_description", "You must enter a description for the attachment.")
    filename = re.sub(r"\s+", " ", trim(p.get("file_name")))
    if not filename:
        raise BugzillaError("file_not_specified", "You did not specify a file to attach.")
    filename = re.sub(r"^.*[/\\]", "", filename)[-255:]
    is_patch = int(truthy(p.get("is_patch")))
    content_type = "text/plain" if is_patch else re.sub(r"\s+", " ", trim(p.get("content_type")))
    if not content_type or not CONTENT_TYPE_RE.match(content_type):
        raise BugzillaError("invalid_content_type", f"The content type {content_type} is invalid. Valid types must be "
                            f"of the form foo/bar where foo is one of {', '.join(LEGAL_CONTENT_TYPES)} and bar must "
                            "not contain any special characters (such as \"=\", \"?\", ...).")
    is_private = check_private(user, p.get("is_private"))
    comment = p.get("comment") or ""
    ts = db_now(db)
    created = []
    for row in rows:
        db.execute(text("INSERT INTO attachments (bug_id, creation_ts, modification_time, description, mimetype, ispatch, "
                        "filename, submitter_id, isobsolete, isprivate) VALUES (:b, :t, :t, :d, :m, :p, :f, :s, 0, :pr)"),
                   {"b": row["bug_id"], "t": ts, "d": description, "m": content_type, "p": is_patch, "f": filename,
                    "s": user.id, "pr": is_private})
        attach_id = db.execute(text("SELECT LAST_INSERT_ID()")).scalar()
        db.execute(text("INSERT INTO attach_data (id, thedata) VALUES (:i, :d)"), {"i": attach_id, "d": data})
        insert_comment(db, row["bug_id"], user.id, ts, str(comment), is_private, CMT_ATTACHMENT_CREATED, attach_id)
        touch(db, row["bug_id"], ts)
        sync_fulltext(db, row["bug_id"], comments=True)
        created.append(attach_id)
    return {"ids": created}
