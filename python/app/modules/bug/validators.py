"""Write-side rules of Bugzilla::Bug (create / set_* / update) with Perl's validation order and errors."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text

from app.core.errors import BugzillaError, user_error
from app.modules.bug import activity
from app.modules.bug.model import (
    CMT_ATTACHMENT_CREATED, CMT_DUPE_OF, CMT_HAS_DUPE, FIELD_MAP, BugRow, Ctx, alias_to_id, can_see_bug,
    check_bug, in_group_for_product, load_bug, person_by_login, resolve_bug_id, status_by_id, status_row,
)

# Display names used by object_not_specified / object_does_not_exist (template object_name block)
CHOICE_NAMES = {
    "op_sys": "OS", "rep_platform": "Hardware", "priority": "Priority", "bug_severity": "Severity",
    "resolution": "Resolution", "bug_status": "Status", "product": "product", "component": "component",
    "version": "version", "target_milestone": "milestone", "classification": "classification",
}
PRODUCT_SPECIFIC = {"component": "components", "version": "versions", "target_milestone": "milestones"}
VALIDATOR_DEPENDENCIES = {
    "assigned_to": ["component"], "blocked": ["product"], "bug_status": ["product", "comment", "target_milestone"],
    "cc": ["component"], "comment": ["creation_ts"], "component": ["product"], "dependson": ["product"],
    "dup_id": ["bug_status", "resolution"], "groups": ["product"], "keywords": ["product"],
    "resolution": ["bug_status", "dependson"], "qa_contact": ["component"], "target_milestone": ["product"],
    "version": ["product"],
}
MAX_FREETEXT_LENGTH = 255
MAX_COMMENT_LENGTH = 65535


def sort_by_dep(fields: list[str]) -> list[str]:
    """Bugzilla::Object::_sort_by_dep: no-dependency fields alphabetically, then the rest with deps first."""
    has = sorted(f for f in fields if f in VALIDATOR_DEPENDENCIES)
    result = sorted(f for f in fields if f not in VALIDATOR_DEPENDENCIES)

    def insert(f: str) -> None:
        if f in result:
            return
        for dep in VALIDATOR_DEPENDENCIES.get(f, []):
            if dep in has:
                insert(dep)
        result.append(f)

    for f in has:
        insert(f)
    return result


def map_fields(params: dict, keep: set[str] | None = None) -> dict:
    out = {}
    for k, v in params.items():
        if keep and k in keep:
            out[k] = v
        else:
            out[FIELD_MAP.get(k, k)] = v
    return out


def db_now(ctx: Ctx):
    return ctx.session.execute(text("SELECT LOCALTIMESTAMP(0)")).scalar_one()


def clean_text(value: str) -> str:
    import re

    return re.sub(r"[\x00-\x1f]+", " ", value).strip()


# --------------------------------------------------------------------------- field checks

def check_select(ctx: Ctx, table: str, value: Any) -> str:
    name = CHOICE_NAMES[table]
    value = "" if value is None else str(value).strip()
    if value == "":
        raise user_error("object_not_specified", **{"class": name})
    row = ctx.session.execute(text(f"SELECT value FROM {table} WHERE value = :v"), {"v": value}).first()
    if row is None:
        raise user_error("object_does_not_exist_value", **{"class": name, "name": value})
    return row.value


def check_product_for_entry(ctx: Ctx, name: Any):
    name = "" if name is None else str(name).strip()
    if name == "":
        raise user_error("object_not_specified", **{"class": "product"})
    product = ctx.session.execute(
        text("SELECT id, name, isactive, defaultmilestone, allows_unconfirmed, classification_id FROM products WHERE name = :n"),
        {"n": name},
    ).first()
    if product is None or not product_user_has_access(ctx, product.id):
        raise user_error("entry_access_denied", product=name)
    if not product.isactive:
        raise BugzillaError("product_disabled", f"Sorry, entering a bug into the product {product.name} has been disabled.")
    return product


def product_user_has_access(ctx: Ctx, product_id: int) -> bool:
    rows = ctx.session.execute(
        text("SELECT group_id, membercontrol, othercontrol FROM group_control_map WHERE product_id = :p AND entry != 0"),
        {"p": product_id},
    ).all()
    if not rows:
        return True
    mine = set(ctx.session.execute(text("SELECT group_id FROM user_group_map WHERE user_id = :u"), {"u": ctx.user.id}).scalars())
    return all(r.group_id in mine for r in rows)


def check_in_product(ctx: Ctx, kind: str, product, value: Any):
    table = PRODUCT_SPECIFIC[kind]
    display = CHOICE_NAMES[kind]
    value = "" if value is None else str(value).strip()
    if value == "":
        if kind == "component":
            raise BugzillaError("require_component", "You must choose a component to file this bug in.")
        raise user_error("object_not_specified", **{"class": display})
    col = "name" if kind == "component" else "value"
    row = ctx.session.execute(
        text(f"SELECT * FROM {table} WHERE product_id = :p AND {col} = :v"), {"p": product.id, "v": value}
    ).first()
    if row is None:
        raise user_error("object_does_not_exist_in_product", **{"class": display, "name": value, "product": product.name})
    return row


def check_user(ctx: Ctx, login: Any):
    login = "" if login is None else str(login).strip()
    if login == "":
        raise user_error("object_not_specified", **{"class": "user"})
    p = person_by_login(ctx.session, login)
    if p is None:
        raise user_error("object_does_not_exist_user", name=login)
    return p


def login_to_id(ctx: Ctx, login: str) -> int:
    p = person_by_login(ctx.session, login)
    if p is None:
        raise user_error("invalid_username", name=login)
    return p.id


def check_alias(ctx: Ctx, aliases: Any, bug_id: int | None = None) -> list[str]:
    import re

    if aliases is None:
        return []
    items = aliases if isinstance(aliases, list) else re.split(r"[\s,]+", str(aliases))
    out = []
    for alias in items:
        alias = (alias or "").strip()
        if not alias:
            continue
        if len(alias) > 40:
            raise user_error("alias_too_long")
        if alias.isdigit():
            raise user_error("alias_is_numeric", alias=alias)
        if "," in alias or " " in alias:
            raise user_error("alias_has_comma_or_space", alias=alias)
        other = alias_to_id(ctx.session, alias)
        if other and other != bug_id:
            raise user_error("alias_in_use", alias=alias, bug_id=other)
        out.append(alias)
    return out


def check_short_desc(value: Any) -> str:
    value = clean_text(str(value)) if value else value
    if value is None or value == "":
        raise user_error("require_summary")
    if len(value) > MAX_FREETEXT_LENGTH:
        raise user_error("freetext_too_long", field="short_desc")
    return value


def check_comment_text(value: Any) -> str:
    import re

    if value is None:
        value = ""
    value = re.sub(r"\s*$", "", str(value), count=1)
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    if len(value) > MAX_COMMENT_LENGTH:
        raise user_error("comment_too_long")
    return value


def check_is_private(ctx: Ctx, is_private: Any, current: bool | None = None) -> int:
    value = 1 if is_private in (True, 1, "1", "true", "True") else 0
    if ((current is None and value) or (current is not None and int(current) != value)) and not ctx.is_insider:
        raise user_error("user_not_insider")
    return value


def statuses_available(ctx: Ctx, product, old_status_id: int | None) -> list:
    cond = "old_status IS NULL" if old_status_id is None else "old_status = :old"
    rows = ctx.session.execute(
        text(
            "SELECT bs.id, bs.value, bs.is_open, bs.sortkey FROM status_workflow sw JOIN bug_status bs ON bs.id = sw.new_status "
            f"WHERE bs.isactive = 1 AND {cond} ORDER BY bs.sortkey"
        ),
        {} if old_status_id is None else {"old": old_status_id},
    ).all()
    rows = list(rows)
    if old_status_id is not None:
        rows.append(status_by_id(ctx.session, old_status_id))
    if not product.allows_unconfirmed:
        rows = [r for r in rows if r.value != "UNCONFIRMED"]
    return rows


def comment_required_on_change(ctx: Ctx, new_id: int, old_id: int | None) -> bool:
    cond = "old_status IS NULL" if old_id is None else "old_status = :old"
    r = ctx.session.execute(
        text(f"SELECT require_comment FROM status_workflow WHERE new_status = :new AND {cond}"),
        {"new": new_id} | ({} if old_id is None else {"old": old_id}),
    ).scalar()
    return bool(r)


def check_bug_status(ctx: Ctx, new_status: Any, product, old_status: Any, comment_text: str | None) -> Any:
    """Bugzilla::Bug::_check_bug_status for both create (old_status None) and update."""
    valid = statuses_available(ctx, product, old_status.id if old_status is not None else None)
    if old_status is None:
        if in_group_for_product(ctx, "editbugs", product.id) or in_group_for_product(ctx, "canconfirm", product.id):
            if not new_status:
                if len(valid) == 1:
                    new_status = valid[0].value
                else:
                    new_status = valid[0].value if valid[0].value != "UNCONFIRMED" else valid[1].value
        else:
            new_status = "UNCONFIRMED" if any(v.value == "UNCONFIRMED" for v in valid) else valid[0].value
    new = status_row(ctx.session, str(new_status).strip()) if new_status else None
    if new is None:
        raise user_error("object_does_not_exist_value", **{"class": "Status", "name": str(new_status).strip()})
    if (old_status is None or old_status.id != new.id) and not any(v.value == new.value for v in valid):
        if old_status is None:
            raise user_error("illegal_bug_status_new", new=new.value)
        raise user_error("illegal_bug_status_transition", old=old_status.value, new=new.value)
    if comment_required_on_change(ctx, new.id, old_status.id if old_status is not None else None) and not comment_text:
        raise user_error("comment_required", field="bug_status", old=old_status.value if old_status is not None else "", new=new.value)
    return new


def check_resolution(ctx: Ctx, resolution: Any, status, dependson: list[int]) -> str:
    resolution = "" if resolution is None else str(resolution).strip()
    is_open = bool(status.is_open)
    if not resolution and not is_open:
        raise user_error("missing_resolution", status=status.value)
    resolution = check_select(ctx, "resolution", resolution) if resolution else ""
    if is_open and resolution:
        raise user_error("resolution_not_allowed")
    if (ctx.param("noresolveonopenblockers") == "1" and resolution == "FIXED" and dependson):
        open_count = ctx.session.execute(
            text("SELECT COUNT(*) FROM bugs b JOIN bug_status s ON s.value = b.bug_status WHERE s.is_open = 1 AND b.bug_id IN ("
                 + ",".join(str(int(d)) for d in dependson) + ")")
        ).scalar()
        if open_count:
            raise BugzillaError("still_unresolved_bugs", f"This bug has {open_count} open dependencies and cannot be resolved FIXED.", code=32000)
    return resolution


def _as_id_list(value: Any) -> list[int]:
    import re

    if value is None or value == "":
        return []
    items = value if isinstance(value, list) else re.split(r"[\s,]+", str(value))
    return [int(v) if str(v).isdigit() else v for v in items if str(v).strip()]


def validate_dependencies(ctx: Ctx, dependson: list[int], blocked: list[int], bug_id: int) -> None:
    """Bugzilla::Bug::ValidateDependencies: no bug in both lists, and no cycles through this bug."""
    both = set(dependson) & set(blocked)
    if bug_id and (bug_id in dependson or bug_id in blocked):
        raise user_error("dependency_loop_single")
    if both:
        raise user_error("dependency_loop_multi", bug_ids=", ".join(str(b) for b in sorted(both)))

    def closure(start: list[int], column: str, other: str) -> set[int]:
        seen: set[int] = set()
        frontier = list(start)
        while frontier:
            ids = [i for i in frontier if i not in seen]
            if not ids:
                break
            seen.update(ids)
            frontier = list(ctx.session.execute(
                text(f"SELECT {column} FROM dependencies WHERE {other} IN (" + ",".join(str(int(i)) for i in ids) + ")")
            ).scalars())
        return seen

    if dependson and blocked:
        # bugs that "dependson" transitively depend on must not include anything we block
        upstream = closure(dependson, "dependson", "blocked")
        loops = upstream & set(blocked)
        if loops:
            raise user_error("dependency_loop_multi", bug_ids=", ".join(str(b) for b in sorted(loops)))
    if bug_id:
        if dependson and bug_id in closure(dependson, "dependson", "blocked"):
            raise user_error("dependency_loop_single")
        if blocked and bug_id in closure(blocked, "blocked", "dependson"):
            raise user_error("dependency_loop_single")


def check_dependencies(ctx: Ctx, dependson: Any, blocked: Any, bug: BugRow | None, product_id: int) -> tuple[list[int], list[int]]:
    if bug is None and not in_group_for_product(ctx, "editbugs", product_id):
        return [], []
    out = {}
    for name, raw in (("dependson", dependson), ("blocked", blocked)):
        ids = _as_id_list(raw)
        resolved = []
        for value in ids:
            other = check_bug(ctx, value, name, visibility=False)
            if ctx.param("strict_isolation") == "1":
                pass
            resolved.append(other.id)
        out[name] = resolved
    validate_dependencies(ctx, out["dependson"], out["blocked"], bug.id if bug else 0)
    return out["dependson"], out["blocked"]


def default_groups(ctx: Ctx, product_id: int) -> list[int]:
    rows = ctx.session.execute(
        text("SELECT group_id, membercontrol, othercontrol FROM group_control_map WHERE product_id = :p"), {"p": product_id}
    ).all()
    mine = set(ctx.session.execute(text("SELECT group_id FROM user_group_map WHERE user_id = :u"), {"u": ctx.user.id}).scalars())
    out = []
    for r in rows:
        control = r.membercontrol if r.group_id in mine else r.othercontrol
        if control in (1, 3):  # CONTROLMAPDEFAULT, CONTROLMAPMANDATORY
            out.append(r.group_id)
    return out


# --------------------------------------------------------------------------- create

def create_bug(ctx: Ctx, api_params: dict) -> int:
    p = map_fields(api_params)
    bz = ctx.param
    for name, default in (("bug_severity", bz("defaultseverity")), ("priority", bz("defaultpriority")),
                          ("op_sys", bz("defaultopsys")), ("rep_platform", bz("defaultplatform"))):
        if p.get(name) is None:
            p[name] = default
    if p.get("comment") is None:
        p["comment"] = ""
    # fields Perl validates for a new bug (VALIDATORS keys that exist in params + required ones)
    required = ["alias", "assigned_to", "bug_file_loc", "bug_severity", "bug_status", "cc", "comment", "component",
                "deadline", "dependson", "blocked", "dup_id", "groups", "keywords", "op_sys", "priority", "product",
                "qa_contact", "rep_platform", "resolution", "short_desc", "status_whiteboard", "target_milestone",
                "version", "cclist_accessible", "reporter_accessible", "estimated_time", "remaining_time", "see_also"]
    names = [n for n in required if n in p or n in (
        "assigned_to", "bug_file_loc", "bug_severity", "bug_status", "cc", "comment", "component", "groups", "keywords",
        "op_sys", "priority", "product", "qa_contact", "rep_platform", "short_desc", "status_whiteboard",
        "target_milestone", "version", "dependson", "blocked")]
    order = sort_by_dep(names)
    v: dict[str, Any] = {}
    product = component = None
    deps_done = False
    for name in order:
        val = p.get(name)
        if name == "alias":
            v["alias"] = check_alias(ctx, val)
        elif name == "bug_file_loc":
            url = ("" if val is None else str(val)).strip()
            v["bug_file_loc"] = "" if url == "http://" else url
        elif name in ("bug_severity", "priority", "op_sys", "rep_platform"):
            v[name] = check_select(ctx, name, val)
        elif name == "product":
            product = check_product_for_entry(ctx, val)
            v["product_id"] = product.id
        elif name == "component":
            component = check_in_product(ctx, "component", product, val)
            v["component_id"] = component.id
        elif name == "version":
            v["version"] = check_in_product(ctx, "version", product, val).value
        elif name == "target_milestone":
            target = product.defaultmilestone if val is None else val
            v["target_milestone"] = check_in_product(ctx, "target_milestone", product, target).value
        elif name == "short_desc":
            v["short_desc"] = check_short_desc(val)
        elif name == "status_whiteboard":
            v["status_whiteboard"] = "" if val is None else str(val)
        elif name == "comment":
            v["comment"] = check_comment_text(val)
            v["comment_is_private"] = check_is_private(ctx, p.get("comment_is_private")) if p.get("comment_is_private") is not None else 0
        elif name == "assigned_to":
            if not in_group_for_product(ctx, "editbugs", product.id) or not val:
                v["assigned_to"] = component.initialowner
            else:
                v["assigned_to"] = check_user(ctx, val).id
        elif name == "qa_contact":
            if bz("useqacontact") != "1":
                v["qa_contact"] = None
            elif not in_group_for_product(ctx, "editbugs", product.id) or not val:
                v["qa_contact"] = component.initialqacontact
            else:
                v["qa_contact"] = check_user(ctx, val).id
        elif name == "cc":
            ids: dict[int, bool] = {}
            if val:
                items = val if isinstance(val, list) else [x for x in str(val).replace(";", ",").split(",")]
                for login in items:
                    login = (login or "").strip()
                    if login:
                        ids[login_to_id(ctx, login)] = True
            for uid in ctx.session.execute(text("SELECT user_id FROM component_cc WHERE component_id = :c"), {"c": component.id}).scalars():
                ids[uid] = True
            v["cc"] = list(ids)
        elif name in ("dependson", "blocked"):
            if not deps_done:
                v["dependson"], v["blocked"] = check_dependencies(ctx, p.get("dependson"), p.get("blocked"), None, product.id)
                deps_done = True
        elif name == "bug_status":
            status = check_bug_status(ctx, val, product, None, v.get("comment"))
            v["bug_status"] = status
            v["everconfirmed"] = 0 if status.value == "UNCONFIRMED" else 1
        elif name == "resolution":
            v["resolution"] = check_resolution(ctx, val, v["bug_status"], v.get("dependson", []))
        elif name == "dup_id":
            v["dup_id"] = val
        elif name == "groups":
            if val is None:
                v["groups"] = default_groups(ctx, product.id)
            else:
                names_ = val if isinstance(val, list) else [g.strip() for g in str(val).split(",")]
                gids = []
                for gname in names_:
                    gid = ctx.session.execute(text("SELECT id FROM groups WHERE name = :n"), {"n": gname}).scalar()
                    if gid is None:
                        raise BugzillaError("group_restriction_not_allowed", f"You tried to restrict the bug to the {gname} group, but either this group does not exist or you are not allowed to use it.")
                    gids.append(gid)
                v["groups"] = list(set(gids) | set(g for g in default_groups(ctx, product.id)))
        elif name == "keywords":
            v["keywords"] = check_keywords(ctx, val)
        elif name == "deadline":
            v["deadline"] = val or None
        elif name in ("cclist_accessible", "reporter_accessible"):
            v[name] = 1 if val in (None, True, 1, "1") else 0
        elif name in ("estimated_time", "remaining_time"):
            v[name] = float(val or 0)
        elif name == "see_also":
            v["see_also"] = val
    if "resolution" not in v:
        v["resolution"] = check_resolution(ctx, "", v["bug_status"], v.get("dependson", []))

    now = db_now(ctx)
    s = ctx.session
    s.execute(
        text(
            "INSERT INTO bugs (assigned_to, bug_file_loc, bug_severity, bug_status, creation_ts, delta_ts, short_desc, "
            "op_sys, priority, product_id, rep_platform, reporter, version, component_id, resolution, target_milestone, "
            "qa_contact, status_whiteboard, lastdiffed, everconfirmed, reporter_accessible, cclist_accessible, "
            "estimated_time, remaining_time, deadline) VALUES (:assigned_to, :bug_file_loc, :bug_severity, :bug_status, "
            ":now, :now, :short_desc, :op_sys, :priority, :product_id, :rep_platform, :reporter, :version, :component_id, "
            ":resolution, :target_milestone, :qa_contact, :status_whiteboard, NULL, :everconfirmed, :reporter_accessible, "
            ":cclist_accessible, :estimated_time, :remaining_time, :deadline)"
        ),
        {
            "assigned_to": v["assigned_to"], "bug_file_loc": v["bug_file_loc"], "bug_severity": v["bug_severity"],
            "bug_status": v["bug_status"].value, "now": now, "short_desc": v["short_desc"], "op_sys": v["op_sys"],
            "priority": v["priority"], "product_id": v["product_id"], "rep_platform": v["rep_platform"],
            "reporter": ctx.user.id, "version": v["version"], "component_id": v["component_id"],
            "resolution": v["resolution"], "target_milestone": v["target_milestone"], "qa_contact": v.get("qa_contact"),
            "status_whiteboard": v["status_whiteboard"], "everconfirmed": v["everconfirmed"],
            "reporter_accessible": v.get("reporter_accessible", 1), "cclist_accessible": v.get("cclist_accessible", 1),
            "estimated_time": v.get("estimated_time", 0), "remaining_time": v.get("remaining_time", 0),
            "deadline": v.get("deadline"),
        },
    )
    bug_id = s.execute(text("SELECT LAST_INSERT_ID()")).scalar_one()
    for gid in v.get("groups", []):
        s.execute(text("INSERT INTO bug_group_map (bug_id, group_id) VALUES (:b, :g)"), {"b": bug_id, "g": gid})
    for uid in v.get("cc", []):
        s.execute(text("INSERT INTO cc (bug_id, who) VALUES (:b, :w)"), {"b": bug_id, "w": uid})
    for kid in v.get("keywords", []):
        s.execute(text("INSERT INTO keywords (bug_id, keywordid) VALUES (:b, :k)"), {"b": bug_id, "k": kid})
    for dep in v.get("dependson", []):
        s.execute(text("INSERT INTO dependencies (blocked, dependson) VALUES (:b, :d)"), {"b": bug_id, "d": dep})
        activity.log_activity(ctx, dep, "blocked", "", bug_id, now)
        s.execute(text("UPDATE bugs SET delta_ts = :t WHERE bug_id = :b"), {"t": now, "b": dep})
    for blk in v.get("blocked", []):
        s.execute(text("INSERT INTO dependencies (blocked, dependson) VALUES (:b, :d)"), {"b": blk, "d": bug_id})
        activity.log_activity(ctx, blk, "dependson", "", bug_id, now)
        s.execute(text("UPDATE bugs SET delta_ts = :t WHERE bug_id = :b"), {"t": now, "b": blk})
    insert_comment(ctx, bug_id, v["comment"], now, isprivate=v.get("comment_is_private", 0))
    for alias in v.get("alias", []):
        s.execute(text("INSERT INTO bugs_aliases (alias, bug_id) VALUES (:a, :b)"), {"a": alias, "b": bug_id})
    sync_fulltext(ctx, bug_id, new_bug=True)
    return bug_id


def check_keywords(ctx: Ctx, value: Any) -> list[int]:
    import re

    if value is None:
        return []
    items = value if isinstance(value, list) else re.split(r"[\s,]+", str(value).strip())
    out = {}
    for kw in items:
        if not kw:
            continue
        kid = ctx.session.execute(text("SELECT id FROM keyworddefs WHERE name = :n"), {"n": kw}).scalar()
        if kid is None:
            raise user_error("object_does_not_exist_value", **{"class": "keyword", "name": kw})
        out[kid] = True
    return list(out)


def insert_comment(ctx: Ctx, bug_id: int, thetext: str, when, *, isprivate: int = 0, type_: int = 0,
                   extra_data: Any = None, work_time: float = 0, who: int | None = None) -> int:
    ctx.session.execute(
        text(
            "INSERT INTO longdescs (bug_id, who, bug_when, work_time, thetext, isprivate, already_wrapped, type, extra_data) "
            "VALUES (:b, :w, :t, :wt, :tx, :p, 0, :ty, :ex)"
        ),
        {"b": bug_id, "w": who or ctx.user.id, "t": when, "wt": work_time, "tx": thetext, "p": isprivate,
         "ty": type_, "ex": None if extra_data is None else str(extra_data)},
    )
    return ctx.session.execute(text("SELECT LAST_INSERT_ID()")).scalar_one()


def sync_fulltext(ctx: Ctx, bug_id: int, *, new_bug: bool = False, update_short_desc: bool = False,
                  update_comments: bool = False) -> None:
    s = ctx.session
    all_c = public_c = None
    if new_bug or update_comments:
        rows = s.execute(text("SELECT thetext, isprivate FROM longdescs WHERE bug_id = :b"), {"b": bug_id}).all()
        all_c = "\n".join(r.thetext for r in rows)
        public_c = "\n".join(r.thetext for r in rows if not r.isprivate)
    short = s.execute(text("SELECT short_desc FROM bugs WHERE bug_id = :b"), {"b": bug_id}).scalar()
    if new_bug:
        s.execute(
            text("INSERT INTO bugs_fulltext (bug_id, short_desc, comments, comments_noprivate) VALUES (:b, :s, :c, :p)"),
            {"b": bug_id, "s": short, "c": all_c, "p": public_c},
        )
        return
    sets, binds = [], {"b": bug_id}
    if update_short_desc:
        sets.append("short_desc = :s")
        binds["s"] = short
    if update_comments:
        sets += ["comments = :c", "comments_noprivate = :p"]
        binds.update(c=all_c, p=public_c)
    if sets:
        s.execute(text("UPDATE bugs_fulltext SET " + ", ".join(sets) + " WHERE bug_id = :b"), binds)


# --------------------------------------------------------------------------- update

@dataclass
class Pending:
    """Everything an update wants to change, applied in Bug::update order."""
    columns: dict[str, Any] = field(default_factory=dict)      # bugs.* column -> new value
    comments: list[dict] = field(default_factory=list)
    cc: list[int] | None = None
    aliases: list[str] | None = None
    keywords: list[int] | None = None
    dependson: list[int] | None = None
    blocked: list[int] | None = None
    groups: list[int] | None = None
    dup_id: int | None = None
    dup_changed: bool = False
    dup_add_cc: bool = False


PRIVILEGES = {1: "the reporter", 2: "the assignee or an empowered user", 3: "an empowered user"}


def check_can_change_field(ctx: Ctx, bug: BugRow, fname: str, old: Any, new: Any) -> None:
    """Bugzilla::Bug::check_can_change_field (non-hook parts)."""
    old_s = "" if old is None else str(old)
    new_s = "" if new is None else str(new)
    if old_s.strip() == new_s.strip():
        return
    if fname.startswith("longdesc") or fname == "flagtypes.name":
        return
    privs = None
    if fname in ("estimated_time", "remaining_time", "work_time", "deadline") and not ctx.is_timetracker:
        privs = 3
    elif in_group_for_product(ctx, "editbugs", bug.row.product_id):
        return
    elif fname == "bug_status" and (old_s == "UNCONFIRMED") != (new_s == "UNCONFIRMED"):
        if in_group_for_product(ctx, "canconfirm", bug.row.product_id):
            return
        privs = 3
    elif bug.row.assigned_to == ctx.user.id or (ctx.param("useqacontact") == "1" and bug.row.qa_contact == ctx.user.id):
        return
    elif fname in ("assigned_to", "qa_contact", "target_milestone", "everconfirmed"):
        privs = 2
    elif fname == "priority" and ctx.param("letsubmitterchoosepriority") != "1":
        privs = 2
    elif fname == "bug_status" and _is_open(ctx, old_s) and _is_open(ctx, new_s):
        privs = 2
    elif bug.row.reporter == ctx.user.id:
        return
    else:
        privs = 1
    raise user_error("illegal_change", field=fname, oldvalue=old_s, newvalue=new_s, privs=PRIVILEGES[privs])


def _is_open(ctx: Ctx, status: str) -> bool:
    r = status_row(ctx.session, status)
    return bool(r and r.is_open)


def apply_update(ctx: Ctx, bug: BugRow, api_params: dict) -> dict[str, tuple[str, str]]:
    """Bugzilla::WebService::Bug::update for one bug: set_all + update. Returns changes keyed by DB field."""
    p = map_fields(api_params, keep={"summary", "platform", "severity", "url"})
    p.pop("ids", None)
    p.pop("dependencies", None)
    row = bug.row
    pend = Pending()
    s = ctx.session
    product = s.execute(text("SELECT id, name, allows_unconfirmed, defaultmilestone FROM products WHERE id = :p"), {"p": row.product_id}).first()

    if "product" in p and str(p["product"]).strip().lower() != product.name.lower():
        raise BugzillaError("illegal_change", "Changing the product of a bug is not supported by the Python port yet.", code=115)

    # dependencies
    if "dependson" in p or "blocked" in p:
        new_deps = {}
        for name, current in (("dependson", bug.depends_on), ("blocked", bug.blocks)):
            ids = list(current)
            spec = p.get(name)
            if spec is None:
                pass
            elif isinstance(spec, dict):
                if "set" in spec:
                    ids = _as_id_list(spec["set"])
                else:
                    for add in _as_id_list(spec.get("add")):
                        if add not in ids:
                            ids.append(add)
                    for rem in _as_id_list(spec.get("remove")):
                        ids = [i for i in ids if i != rem]
            else:
                ids = _as_id_list(spec)
            new_deps[name] = ids
        for name in ("dependson", "blocked"):
            old = set(bug.depends_on if name == "dependson" else bug.blocks)
            touched = set(new_deps[name]) ^ old
            if touched:
                check_can_change_field(ctx, bug, name, "", "x")
            for value in touched:
                check_bug(ctx, value, name, visibility=False)
        resolved = {n: [resolve_bug_id(s, v, n) for v in new_deps[n]] for n in new_deps}
        validate_dependencies(ctx, resolved["dependson"], resolved["blocked"], bug.id)
        pend.dependson, pend.blocked = resolved["dependson"], resolved["blocked"]

    # keywords
    if "keywords" in p:
        spec = p["keywords"]
        current = set(s.execute(text("SELECT keywordid FROM keywords WHERE bug_id = :b"), {"b": bug.id}).scalars())
        if isinstance(spec, dict):
            for action in sorted(spec):
                ids = set(check_keywords(ctx, spec[action]))
                if action == "add":
                    current |= ids
                elif action == "remove":
                    current -= ids
                else:
                    current = ids
        else:
            current = set(check_keywords(ctx, spec))
        pend.keywords = sorted(current)

    # comment
    if "comment" in p or "work_time" in p:
        c = p.get("comment") or {}
        if isinstance(c, str):
            c = {"body": c}
        body = c.get("body", c.get("comment"))
        thetext = check_comment_text(body)
        is_private = c.get("is_private")
        isprivate = check_is_private(ctx, is_private) if is_private is not None else 0
        work_time = float(p.get("work_time") or 0)
        if thetext != "" or work_time:
            pend.comments.append({"thetext": thetext, "isprivate": isprivate, "work_time": work_time, "type": 0, "extra_data": None})
            if work_time:
                pend.columns["remaining_time"] = max(float(row.remaining_time) - work_time, 0)

    # alias
    if "alias" in p:
        spec = p["alias"]
        current = list(bug.aliases)
        if isinstance(spec, dict):
            if "set" in spec:
                current = _as_str_list(spec["set"])
            else:
                for a in _as_str_list(spec.get("add")):
                    if a not in current:
                        current.append(a)
                for a in _as_str_list(spec.get("remove")):
                    current = [x for x in current if x != a]
        else:
            current = _as_str_list(spec)
        pend.aliases = check_alias(ctx, current, bug.id)

    # cc
    if "cc" in p:
        spec = p["cc"]
        current = [u.id for u in bug.cc_users]
        if isinstance(spec, dict):
            if "set" in spec:
                current = [check_user(ctx, x).id for x in _as_str_list(spec["set"])]
            else:
                for login in _as_str_list(spec.get("add")):
                    uid = check_user(ctx, login).id
                    if uid not in current:
                        current.append(uid)
                for login in _as_str_list(spec.get("remove")):
                    uid = check_user(ctx, login).id
                    current = [x for x in current if x != uid]
        else:
            current = [check_user(ctx, x).id for x in _as_str_list(spec)]
        pend.cc = current

    # groups
    if "groups" in p:
        spec = p["groups"] or {}
        current = set(s.execute(text("SELECT group_id FROM bug_group_map WHERE bug_id = :b"), {"b": bug.id}).scalars())
        for action in ("add", "remove"):
            for gname in _as_str_list(spec.get(action)) if isinstance(spec, dict) else []:
                gid = s.execute(text("SELECT id FROM groups WHERE name = :n"), {"n": gname}).scalar()
                if gid is None:
                    raise BugzillaError("group_restriction_not_allowed", f"You tried to restrict the bug to the {gname} group, but either this group does not exist or you are not allowed to use it.")
                current.add(gid) if action == "add" else current.discard(gid)
        pend.groups = sorted(current)

    # plain setters, in Perl's dependency order (status before resolution, resolution before dup_id)
    setters = {
        "summary": "short_desc", "short_desc": "short_desc", "platform": "rep_platform", "rep_platform": "rep_platform",
        "severity": "bug_severity", "bug_severity": "bug_severity", "url": "bug_file_loc", "bug_file_loc": "bug_file_loc",
        "priority": "priority", "op_sys": "op_sys", "status_whiteboard": "status_whiteboard", "version": "version",
        "target_milestone": "target_milestone", "component": "component", "assigned_to": "assigned_to",
        "qa_contact": "qa_contact", "bug_status": "bug_status", "resolution": "resolution", "dup_id": "dup_id",
        "deadline": "deadline", "estimated_time": "estimated_time", "remaining_time": "remaining_time",
        "cclist_accessible": "cclist_accessible", "reporter_accessible": "reporter_accessible",
        "comment_is_private": None, "see_also": None, "flags": None, "reset_assigned_to": None, "reset_qa_contact": None,
    }
    wanted = [k for k in p if k in setters and setters[k]]
    ordered = sort_by_dep(list({setters[k]: None for k in wanted}))
    by_col = {setters[k]: p[k] for k in wanted}
    status_obj = status_row(s, row.bug_status)
    new_status = status_obj
    for col in ordered:
        val = by_col[col]
        if col == "short_desc":
            new = check_short_desc(val)
        elif col in ("rep_platform", "bug_severity", "priority", "op_sys"):
            new = check_select(ctx, col, val)
        elif col == "bug_file_loc":
            new = ("" if val is None else str(val)).strip()
        elif col == "status_whiteboard":
            new = "" if val is None else str(val)
        elif col == "version":
            new = check_in_product(ctx, "version", product, val).value
        elif col == "target_milestone":
            new = check_in_product(ctx, "target_milestone", product, val).value
        elif col == "component":
            comp = check_in_product(ctx, "component", product, val)
            new = comp.id
            col = "component_id"
        elif col == "assigned_to":
            if val is None or str(val).strip() == "":
                raise BugzillaError("reassign_to_empty", "You cannot reassign to a bug to nobody. Unless you intentionally cleared out the \"Reassign bug to\" field, the user you tried to reassign to may not exist.")
            new = check_user(ctx, val).id
        elif col == "qa_contact":
            new = check_user(ctx, val).id if val else None
        elif col == "bug_status":
            new_status = check_bug_status(ctx, val, product, status_obj, pend.comments[-1]["thetext"] if pend.comments else None)
            new = new_status.value
        elif col == "resolution":
            # set_bug_status already set the resolution when the status changed to a closed one
            if "bug_status" in by_col and pend.columns.get("bug_status") is not None:
                continue
            new = check_resolution(ctx, val, new_status, pend.dependson if pend.dependson is not None else bug.depends_on)
        elif col == "dup_id":
            continue
        elif col == "deadline":
            new = val or None
        elif col in ("estimated_time", "remaining_time"):
            new = float(val or 0)
        elif col in ("cclist_accessible", "reporter_accessible"):
            new = 1 if val in (True, 1, "1", "true") else 0
        else:
            continue
        old = getattr(row, col)
        if col == "bug_status":
            check_can_change_field(ctx, bug, col, old, new)
            if old != new:
                pend.columns["bug_status"] = new
                if new_status.is_open:
                    pend.columns["everconfirmed"] = 0 if new == "UNCONFIRMED" else 1
                    if row.resolution:
                        pend.columns["resolution"] = ""
                    if bug.dup_id:
                        pend.dup_id, pend.dup_changed = None, True
                else:
                    resolution = by_col.get("resolution", row.resolution)
                    res = check_resolution(ctx, resolution, new_status, pend.dependson if pend.dependson is not None else bug.depends_on)
                    _set_resolution(ctx, bug, pend, res, by_col.get("dup_id"))
                    if row.remaining_time and status_obj.id != new_status.id:
                        pend.columns["remaining_time"] = 0
            elif "resolution" in by_col and not new_status.is_open:
                res = check_resolution(ctx, by_col["resolution"], new_status, pend.dependson if pend.dependson is not None else bug.depends_on)
                _set_resolution(ctx, bug, pend, res, by_col.get("dup_id"))
            continue
        if col == "resolution":
            check_can_change_field(ctx, bug, col, old, new)
            _set_resolution(ctx, bug, pend, new, by_col.get("dup_id"))
            continue
        if (old or "") != (new or "") and not (isinstance(old, float) and float(old) == float(new or 0)):
            check_can_change_field(ctx, bug, col, old, new)
            pend.columns[col] = new
    if "dup_id" in by_col and not pend.dup_changed and by_col["dup_id"] and by_col["dup_id"] != bug.dup_id:
        _set_dup_id(ctx, bug, pend, by_col["dup_id"])

    return commit_update(ctx, bug, pend)


def _as_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [v.strip() for v in str(value).split(",") if v.strip()]


def _set_resolution(ctx: Ctx, bug: BugRow, pend: Pending, new_res: str, dup_param: Any) -> None:
    old_res = bug.row.resolution
    if new_res != old_res:
        pend.columns["resolution"] = new_res
        if old_res == "DUPLICATE" and bug.dup_id:
            pend.dup_id, pend.dup_changed = None, True
        elif new_res == "DUPLICATE" and bug.row.remaining_time:
            pend.columns["remaining_time"] = 0
    if new_res == "DUPLICATE":
        if dup_param:
            _set_dup_id(ctx, bug, pend, dup_param)
        elif not bug.dup_id:
            raise user_error("dupe_id_required")
    elif bug.dup_id:
        pend.dup_id, pend.dup_changed = None, True


def _set_dup_id(ctx: Ctx, bug: BugRow, pend: Pending, dupe_of: Any) -> None:
    raw = str(dupe_of).strip()
    if not raw:
        raise BugzillaError("undefined_field", "A value for the dup_id field is required.")
    other = check_bug(ctx, raw, "dup_id", visibility=False)
    if bug.dup_id == other.id:
        return
    if not can_see_bug(ctx, other.id):
        raise user_error("bug_access_denied", bug_id=raw)
    # follow the duplicate chain and refuse loops
    seen = {bug.id}
    cur = other.id
    while cur:
        if cur in seen:
            raise BugzillaError("dupe_loop_detected", f"You can't mark bug {bug.id} as a duplicate of bug {other.id} because that would create a loop.", code=118)
        seen.add(cur)
        cur = ctx.session.execute(text("SELECT dupe_of FROM duplicates WHERE dupe = :d"), {"d": cur}).scalar()
    if ctx.param("commentonduplicate") == "1" and not pend.comments:
        raise user_error("comment_required", field="dup_id", old="", new="")
    pend.dup_id, pend.dup_changed = other.id, True
    pend.dup_add_cc = other.row.reporter != bug.row.reporter
    if pend.columns.get("resolution", bug.row.resolution) != "DUPLICATE":
        pend.columns["resolution"] = "DUPLICATE"


def commit_update(ctx: Ctx, bug: BugRow, pend: Pending) -> dict[str, tuple[str, str]]:
    s = ctx.session
    row = bug.row
    now = db_now(ctx)
    changes: dict[str, tuple[str, str]] = {}

    # bugs.* columns (Bugzilla::Object::update)
    cols = {c: v for c, v in pend.columns.items() if (getattr(row, c) if c != "component_id" else row.component_id) != v}
    if cols:
        s.execute(text("UPDATE bugs SET " + ", ".join(f"{c} = :{c}" for c in cols) + " WHERE bug_id = :id"), {**cols, "id": bug.id})
    for c, v in cols.items():
        old = getattr(row, c)
        if c == "component_id":
            changes["component"] = (bug.component, s.execute(text("SELECT name FROM components WHERE id = :i"), {"i": v}).scalar())
        elif c in ("assigned_to", "qa_contact"):
            from app.modules.bug.model import person
            changes[c] = (person(s, old).login if old else "", person(s, v).login if v else "")
        else:
            changes[c] = ("" if old is None else str(old), "" if v is None else str(v))

    # cc
    if pend.cc is not None:
        old_cc = [u.id for u in bug.cc_users]
        removed = [u for u in old_cc if u not in pend.cc]
        added = [u for u in pend.cc if u not in old_cc]
        if removed:
            s.execute(text("DELETE FROM cc WHERE bug_id = :b AND who IN (" + ",".join(map(str, removed)) + ")"), {"b": bug.id})
        for uid in added:
            s.execute(text("INSERT INTO cc (bug_id, who) VALUES (:b, :w)"), {"b": bug.id, "w": uid})
        if removed or added:
            from app.modules.bug.model import person
            changes["cc"] = (", ".join(person(s, u).login for u in removed), ", ".join(person(s, u).login for u in added))

    # aliases
    if pend.aliases is not None:
        removed = [a for a in bug.aliases if a not in pend.aliases]
        added = [a for a in pend.aliases if a not in bug.aliases]
        for a in removed:
            s.execute(text("DELETE FROM bugs_aliases WHERE bug_id = :b AND alias = :a"), {"b": bug.id, "a": a})
        for a in added:
            s.execute(text("INSERT INTO bugs_aliases (bug_id, alias) VALUES (:b, :a)"), {"b": bug.id, "a": a})
        if removed or added:
            changes["alias"] = (", ".join(removed), ", ".join(added))

    # keywords
    if pend.keywords is not None:
        old_kw = list(s.execute(text("SELECT keywordid FROM keywords WHERE bug_id = :b"), {"b": bug.id}).scalars())
        removed = [k for k in old_kw if k not in pend.keywords]
        added = [k for k in pend.keywords if k not in old_kw]
        if removed:
            s.execute(text("DELETE FROM keywords WHERE bug_id = :b AND keywordid IN (" + ",".join(map(str, removed)) + ")"), {"b": bug.id})
        for k in added:
            s.execute(text("INSERT INTO keywords (bug_id, keywordid) VALUES (:b, :k)"), {"b": bug.id, "k": k})
        if removed or added:
            kname = lambda k: s.execute(text("SELECT name FROM keyworddefs WHERE id = :i"), {"i": k}).scalar()  # noqa: E731
            changes["keywords"] = (", ".join(kname(k) for k in removed), ", ".join(kname(k) for k in added))

    # dependencies
    for type_, other, old, new in (("dependson", "blocked", bug.depends_on, pend.dependson), ("blocked", "dependson", bug.blocks, pend.blocked)):
        if new is None:
            continue
        removed = [i for i in old if i not in new]
        added = [i for i in new if i not in old]
        for rid in removed:
            s.execute(text(f"DELETE FROM dependencies WHERE {type_} = :r AND {other} = :b"), {"r": rid, "b": bug.id})
            activity.log_activity(ctx, rid, other, bug.id, "", now)
            s.execute(text("UPDATE bugs SET delta_ts = :t WHERE bug_id = :b"), {"t": now, "b": rid})
        for aid in added:
            s.execute(text(f"INSERT INTO dependencies ({type_}, {other}) VALUES (:a, :b)"), {"a": aid, "b": bug.id})
            activity.log_activity(ctx, aid, other, "", bug.id, now)
            s.execute(text("UPDATE bugs SET delta_ts = :t WHERE bug_id = :b"), {"t": now, "b": aid})
        if removed or added:
            changes[type_] = (", ".join(map(str, removed)), ", ".join(map(str, added)))

    # groups
    if pend.groups is not None:
        old_g = list(s.execute(text("SELECT group_id FROM bug_group_map WHERE bug_id = :b"), {"b": bug.id}).scalars())
        removed = [g for g in old_g if g not in pend.groups]
        added = [g for g in pend.groups if g not in old_g]
        if removed:
            s.execute(text("DELETE FROM bug_group_map WHERE bug_id = :b AND group_id IN (" + ",".join(map(str, removed)) + ")"), {"b": bug.id})
        for g in added:
            s.execute(text("INSERT INTO bug_group_map (bug_id, group_id) VALUES (:b, :g)"), {"b": bug.id, "g": g})
        if removed or added:
            gname = lambda g: s.execute(text("SELECT name FROM groups WHERE id = :i"), {"i": g}).scalar()  # noqa: E731
            changes["bug_group"] = (", ".join(gname(g) for g in removed), ", ".join(gname(g) for g in added))

    # duplicate bookkeeping comments (set_dup_id)
    if pend.dup_changed and pend.dup_id:
        normal = [c for c in pend.comments if c["type"] == 0]
        if normal:
            normal[-1]["type"], normal[-1]["extra_data"] = CMT_DUPE_OF, pend.dup_id
        else:
            pend.comments.append({"thetext": "", "isprivate": 0, "work_time": 0, "type": CMT_DUPE_OF, "extra_data": pend.dup_id})

    # comments
    for c in pend.comments:
        cid = insert_comment(ctx, bug.id, c["thetext"], now, isprivate=c["isprivate"], type_=c["type"],
                             extra_data=c["extra_data"], work_time=c["work_time"])
        if c["work_time"]:
            activity.log_activity(ctx, bug.id, "work_time", "", c["work_time"], now)

    for fname, (frm, to) in changes.items():
        activity.log_activity(ctx, bug.id, fname, frm, to, now)

    # duplicates table + the other bug
    old_dup, cur_dup = bug.dup_id or 0, (pend.dup_id or 0) if pend.dup_changed else (bug.dup_id or 0)
    if old_dup != cur_dup:
        s.execute(text("DELETE FROM duplicates WHERE dupe = :d"), {"d": bug.id})
        if cur_dup:
            s.execute(text("INSERT INTO duplicates (dupe, dupe_of) VALUES (:d, :o)"), {"d": bug.id, "o": cur_dup})
            if pend.dup_add_cc:
                exists = s.execute(text("SELECT 1 FROM cc WHERE bug_id = :b AND who = :w"), {"b": cur_dup, "w": row.reporter}).first()
                if not exists:
                    s.execute(text("INSERT INTO cc (bug_id, who) VALUES (:b, :w)"), {"b": cur_dup, "w": row.reporter})
                    from app.modules.bug.model import person
                    activity.log_activity(ctx, cur_dup, "cc", "", person(s, row.reporter).login, now)
            insert_comment(ctx, cur_dup, "", now, type_=CMT_HAS_DUPE, extra_data=bug.id)
            s.execute(text("UPDATE bugs SET delta_ts = :t WHERE bug_id = :b"), {"t": now, "b": cur_dup})
            sync_fulltext(ctx, cur_dup, update_comments=True)
        # reported in "changes" but never written to bugs_activity (dup_id has no fielddefs row)
        changes["dup_id"] = (str(old_dup) if old_dup else "", str(cur_dup) if cur_dup else "")

    if changes or pend.comments:
        s.execute(text("UPDATE bugs SET delta_ts = :t WHERE bug_id = :b"), {"t": now, "b": bug.id})
        sync_fulltext(ctx, bug.id, update_short_desc="short_desc" in changes, update_comments=bool(pend.comments))
    return changes


# --------------------------------------------------------------------------- attachments

LEGAL_CONTENT_TYPES = ("application", "audio", "image", "message", "model", "multipart", "text", "video")


def create_attachment(ctx: Ctx, bug: BugRow, params: dict, now) -> int:
    import base64
    import re

    s = ctx.session
    data = params.get("data")
    if data is None:
        raise BugzillaError("param_required", "The function requires a data argument, and that argument was not set.")
    try:
        raw = base64.b64decode(data, validate=False) if isinstance(data, str) else bytes(data)
    except Exception:
        raw = (data or "").encode()
    size = len(raw)
    # validators run alphabetically: bug, data, description, filename, ispatch, isprivate, then mimetype
    if not size:
        raise user_error("zero_length_file")
    max_size = max(int(ctx.param("maxlocalattachment") or 0) * 1048576, int(ctx.param("maxattachmentsize") or 0) * 1024)
    if size > max_size:
        raise user_error("file_too_large", filesize=f"{size / 1024:.0f}", maxsize=max_size // 1024)
    description = (params.get("summary") or "").strip()
    if not description:
        raise user_error("missing_attachment_description")
    filename = clean_text(str(params.get("file_name") or ""))
    if not filename:
        raise user_error("file_not_specified")
    filename = re.sub(r"^.*[/\\]", "", filename)[-255:]
    ispatch = 1 if params.get("is_patch") in (True, 1, "1", "true") else 0
    isprivate = check_is_private(ctx, params.get("is_private"))
    content_type = "text/plain" if ispatch else clean_text(str(params.get("content_type") or ""))
    legal = "|".join(LEGAL_CONTENT_TYPES)
    if not content_type or not re.match(rf"^({legal})/[a-z0-9_\-\+\.]+(;\s*charset=[a-z0-9_\-\+]+)?$", content_type, re.I):
        raise user_error("invalid_content_type", contenttype=content_type)
    s.execute(
        text(
            "INSERT INTO attachments (bug_id, creation_ts, modification_time, description, mimetype, ispatch, filename, "
            "submitter_id, isobsolete, isprivate) VALUES (:b, :t, :t, :d, :m, :p, :f, :u, 0, :pr)"
        ),
        {"b": bug.id, "t": now, "d": description, "m": content_type, "p": ispatch, "f": filename, "u": ctx.user.id, "pr": isprivate},
    )
    attach_id = s.execute(text("SELECT LAST_INSERT_ID()")).scalar_one()
    s.execute(text("INSERT INTO attach_data (id, thedata) VALUES (:i, :d)"), {"i": attach_id, "d": raw})
    comment = params.get("comment") or ""
    insert_comment(ctx, bug.id, check_comment_text(comment), now, isprivate=isprivate, type_=CMT_ATTACHMENT_CREATED, extra_data=attach_id)
    s.execute(text("UPDATE bugs SET delta_ts = :t WHERE bug_id = :b"), {"t": now, "b": bug.id})
    sync_fulltext(ctx, bug.id, update_comments=True)
    return attach_id


ATTACHMENT_RETURNS = {"description": "summary", "ispatch": "is_patch", "isprivate": "is_private",
                      "isobsolete": "is_obsolete", "filename": "file_name", "mimetype": "content_type"}


def update_attachment(ctx: Ctx, attach: Any, params: dict, now) -> dict[str, tuple[str, str]]:
    import re

    s = ctx.session
    new: dict[str, Any] = {}
    if "summary" in params or "description" in params:
        d = (params.get("summary", params.get("description")) or "").strip()
        if not d:
            raise user_error("missing_attachment_description")
        new["description"] = d
    if "file_name" in params or "filename" in params:
        f = clean_text(str(params.get("file_name", params.get("filename")) or ""))
        if not f:
            raise user_error("filename_not_specified")
        new["filename"] = re.sub(r"^.*[/\\]", "", f)[-255:]
    if "is_patch" in params:
        new["ispatch"] = 1 if params["is_patch"] in (True, 1, "1", "true") else 0
    if "is_private" in params:
        new["isprivate"] = check_is_private(ctx, params["is_private"], current=bool(attach.isprivate))
    if "is_obsolete" in params:
        new["isobsolete"] = 1 if params["is_obsolete"] in (True, 1, "1", "true") else 0
    if "content_type" in params:
        ispatch = new.get("ispatch", attach.ispatch)
        ct = "text/plain" if ispatch else clean_text(str(params["content_type"] or ""))
        legal = "|".join(LEGAL_CONTENT_TYPES)
        if not ct or not re.match(rf"^({legal})/[a-z0-9_\-\+\.]+(;\s*charset=[a-z0-9_\-\+]+)?$", ct, re.I):
            raise user_error("invalid_content_type", contenttype=ct)
        new["mimetype"] = ct
    changes = {}
    for col in ("description", "filename", "isobsolete", "ispatch", "isprivate", "mimetype"):
        if col in new and str(getattr(attach, col)) != str(new[col]):
            changes[col] = (str(getattr(attach, col)), str(new[col]))
    if changes:
        s.execute(
            text("UPDATE attachments SET " + ", ".join(f"{c} = :{c}" for c in changes) + ", modification_time = :t WHERE attach_id = :id"),
            {**{c: new[c] for c in changes}, "t": now, "id": attach.attach_id},
        )
        for col, (frm, to) in changes.items():
            activity.log_activity(ctx, attach.bug_id, f"attachments.{col}", frm, to, now, attach_id=attach.attach_id)
        s.execute(text("UPDATE bugs SET delta_ts = :t WHERE bug_id = :b"), {"t": now, "b": attach.bug_id})
    return changes
