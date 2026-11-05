"""Port of Bugzilla/WebService/Bug.pm (the public WebService methods)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text

from app.core.auth import require_login
from app.core.errors import BugzillaError, param_required, user_error
from app.core.fields import filter_wants
from app.core.request import Ctx as RequestCtx
from app.modules.bug import activity, search as search_mod, validators
from app.modules.bug.model import (
    ATTACHMENT_SQL, COMMENT_SQL, FIELD_MAP, API_NAME, Ctx, attachment_to_hash, bug_to_hash, check_bug,
    check_bug_for_edit, comment_to_hash, bug_comments, load_bug, update_token,
)

PRODUCT_SPECIFIC_FIELDS = ("version", "target_milestone", "component")
ABNORMAL_SELECTS = {"classification", "component", "product", "target_milestone", "version"}
FIELD_TYPE_SINGLE_SELECT, FIELD_TYPE_MULTI_SELECT = 2, 3


def _ctx(rc: RequestCtx) -> Ctx:
    return Ctx(session=rc.session, user=rc.user, params=rc.params, bz_params=rc.bz_params)


def _ids(params: dict, key: str) -> list | None:
    value = params.get(key)
    if value is None:
        return None
    if isinstance(value, list):
        if any(isinstance(v, (dict, list)) for v in value):
            raise BugzillaError("param_scalar_array_required", f"The {key} parameter must be an array of scalars.")
        return value
    return [value]


# --------------------------------------------------------------------------- Bug.get / search

def get(rc: RequestCtx) -> dict:
    ctx = _ctx(rc)
    ids = _ids(rc.params, "ids")
    if ids is None:
        raise param_required("ids")
    bugs, faults, hashes = [], [], []
    for bug_id in ids:
        if rc.params.get("permissive"):
            try:
                bug = check_bug(ctx, bug_id)
            except BugzillaError as e:
                faults.append({"id": bug_id, "faultString": e.message, "faultCode": e.code})
                continue
        else:
            bug = check_bug(ctx, bug_id)
        bugs.append(bug)
        hashes.append(bug_to_hash(ctx, bug, rc.params))
    if ctx.user.is_logged_in and filter_wants(rc.params, "update_token"):
        for bug, h in zip(bugs, hashes):
            h["update_token"] = update_token(bug)
    return {"bugs": hashes, "faults": faults}


def search(rc: RequestCtx) -> dict:
    ctx = _ctx(rc)
    ids = search_mod.search(ctx, rc.params)
    return {"bugs": [bug_to_hash(ctx, load_bug(ctx, i), rc.params) for i in ids]}


# --------------------------------------------------------------------------- Bug.create / update

def create(rc: RequestCtx) -> dict:
    require_login(rc.user)
    ctx = _ctx(rc)
    params = {k: v for k, v in rc.params.items() if k != "flags"}
    bug_id = validators.create_bug(ctx, params)
    return {"id": bug_id}


def update(rc: RequestCtx) -> dict:
    require_login(rc.user)
    ctx = _ctx(rc)
    ids = _ids(rc.params, "ids")
    if ids is None:
        raise param_required("ids")
    params = dict(rc.params)
    params.pop("flags", None)
    bugs = [check_bug_for_edit(ctx, i) for i in ids]
    if len(bugs) > 1:
        if "dupe_of" in params:
            raise user_error("dupe_not_allowed", bug_id="", dupe_of="")
        if params.get("alias") is not None:
            raise BugzillaError("multiple_alias_not_allowed", "You cannot set the alias of multiple bugs at once.")
    result = []
    for bug in bugs:
        changes = validators.apply_update(ctx, bug, params)
        fresh = load_bug(ctx, bug.id)
        item = {
            "id": bug.id,
            "last_change_time": activity.iso(fresh.row.delta_ts),
            "changes": {API_NAME.get(f, f): {"removed": frm, "added": to} for f, (frm, to) in changes.items()},
            "alias": list(fresh.aliases),
        }
        result.append(item)
    return {"bugs": result}


# --------------------------------------------------------------------------- comments

def comments(rc: RequestCtx) -> dict:
    ctx = _ctx(rc)
    bug_ids = _ids(rc.params, "ids")
    comment_ids = _ids(rc.params, "comment_ids")
    if bug_ids is None and comment_ids is None:
        raise BugzillaError("params_required", "The function Bug.comments requires that you set one of the following parameters: ids, comment_ids")
    bugs: dict[str, Any] = {}
    for bug_id in bug_ids or []:
        bug = check_bug(ctx, bug_id)
        rows = bug_comments(ctx.session, bug.id, rc.params.get("new_since"))
        # Bugzilla::Bug::comments numbers comments by position (0 = description)
        out = [comment_to_hash(ctx, c, rc.params, count=i) for i, c in enumerate(rows) if not (c.isprivate and not ctx.is_insider)]
        bugs[str(bug.id)] = {"comments": out}
    result: dict[str, Any] = {}
    if comment_ids:
        wanted = [str(c).strip() for c in comment_ids]
        numeric = [int(c) for c in wanted if c.isdigit()]
        rows = ctx.session.execute(
            text(COMMENT_SQL + "WHERE l.comment_id IN (" + ",".join(map(str, numeric)) + ")")
        ).all() if numeric else []
        got = {str(r.comment_id): r for r in rows}
        for c in wanted:
            if c not in got:
                raise user_error("comment_id_invalid", comment_id=c)
        for bug_id in sorted({r.bug_id for r in rows}):
            check_bug(ctx, bug_id)
        for r in rows:
            if r.isprivate and not ctx.is_insider:
                raise user_error("comment_is_private", comment_id=r.comment_id)
            result[str(r.comment_id)] = comment_to_hash(ctx, r, rc.params)
    return {"bugs": bugs, "comments": result}


def add_comment(rc: RequestCtx) -> dict:
    require_login(rc.user)
    ctx = _ctx(rc)
    if rc.params.get("id") is None:
        raise param_required("id")
    comment = rc.params.get("comment")
    if comment is None or str(comment).strip() == "":
        raise param_required("comment")
    bug = check_bug_for_edit(ctx, rc.params["id"])
    is_private = rc.params.get("is_private", rc.params.get("private"))
    body = {"comment": {"body": comment, "is_private": is_private}}
    if rc.params.get("work_time") is not None:
        body["work_time"] = rc.params["work_time"]
    validators.apply_update(ctx, bug, body)
    new_id = ctx.session.execute(
        text("SELECT MAX(comment_id) FROM longdescs WHERE bug_id = :b AND who = :w"), {"b": bug.id, "w": ctx.user.id}
    ).scalar()
    return {"id": new_id}


# --------------------------------------------------------------------------- history

def history(rc: RequestCtx) -> dict:
    ctx = _ctx(rc)
    ids = _ids(rc.params, "ids")
    if ids is None:
        raise param_required("ids")
    out = []
    for bug_id in ids:
        bug = check_bug(ctx, bug_id)
        out.append({"id": bug.id, "history": activity.history(ctx, bug.id, rc.params.get("new_since")), "alias": list(bug.aliases)})
    return {"bugs": out}


# --------------------------------------------------------------------------- attachments

def attachments(rc: RequestCtx) -> dict:
    ctx = _ctx(rc)
    ids = _ids(rc.params, "ids")
    attach_ids = _ids(rc.params, "attachment_ids")
    if ids is None and attach_ids is None:
        raise BugzillaError("param_required", "The function Bug.attachments requires that you set one of the following parameters: ids, attachment_ids")
    bugs: dict[str, Any] = {}
    for bug_id in ids or []:
        bug = check_bug(ctx, bug_id)
        rows = ctx.session.execute(text(ATTACHMENT_SQL + "WHERE a.bug_id = :b ORDER BY a.attach_id"), {"b": bug.id}).all()
        bugs[str(bug.id)] = [attachment_to_hash(ctx, a, rc.params) for a in rows if not (a.isprivate and not ctx.is_insider)]
    result: dict[str, Any] = {}
    numeric = [int(a) for a in (attach_ids or []) if str(a).strip().isdigit()]
    rows = ctx.session.execute(text(ATTACHMENT_SQL + "WHERE a.attach_id IN (" + ",".join(map(str, numeric)) + ") ORDER BY a.attach_id")).all() if numeric else []
    for a in rows:
        check_bug(ctx, a.bug_id)
        if a.isprivate and not ctx.is_insider:
            raise user_error("auth_failure", group="insiders", reason="access", object=f"attachment {a.attach_id}")
        result[str(a.attach_id)] = attachment_to_hash(ctx, a, rc.params)
    return {"bugs": bugs, "attachments": result}


def add_attachment(rc: RequestCtx) -> dict:
    require_login(rc.user)
    ctx = _ctx(rc)
    ids = _ids(rc.params, "ids")
    if ids is None:
        raise param_required("ids")
    if rc.params.get("data") is None:
        raise param_required("data")
    bugs = [check_bug_for_edit(ctx, i) for i in ids]
    now = validators.db_now(ctx)
    created = [validators.create_attachment(ctx, bug, rc.params, now) for bug in bugs]
    return {"ids": created}


def update_attachment(rc: RequestCtx) -> dict:
    require_login(rc.user)
    ctx = _ctx(rc)
    ids = _ids(rc.params, "ids")
    if ids is None:
        raise param_required("ids")
    params = {k: v for k, v in rc.params.items() if k not in ("ids", "login", "password", "token", "flags")}
    comment = params.pop("comment", None)
    rows = []
    for aid in ids:
        a = ctx.session.execute(text(ATTACHMENT_SQL + "WHERE a.attach_id = :id"), {"id": aid}).first() if str(aid).isdigit() else None
        if a is None:
            raise user_error("invalid_attach_id", attach_id=aid)
        check_bug(ctx, a.bug_id)
        rows.append(a)
    now = validators.db_now(ctx)
    result = []
    for a in rows:
        can_edit = ctx.user.id == a.submitter_id or validators.in_group_for_product(
            ctx, "editbugs", ctx.session.execute(text("SELECT product_id FROM bugs WHERE bug_id = :b"), {"b": a.bug_id}).scalar())
        if not can_edit:
            raise user_error("illegal_attachment_edit", attach_id=a.attach_id)
        changes = validators.update_attachment(ctx, a, params, now)
        if comment and str(comment).strip():
            validators.insert_comment(ctx, a.bug_id, validators.check_comment_text(comment), now,
                                      isprivate=int(a.isprivate), type_=6, extra_data=a.attach_id)
            ctx.session.execute(text("UPDATE bugs SET delta_ts = :t WHERE bug_id = :b"), {"t": now, "b": a.bug_id})
            validators.sync_fulltext(ctx, a.bug_id, update_comments=True)
        mod_time = ctx.session.execute(text("SELECT modification_time FROM attachments WHERE attach_id = :id"), {"id": a.attach_id}).scalar()
        result.append({
            "id": a.attach_id,
            "last_change_time": activity.iso(mod_time),
            "changes": {validators.ATTACHMENT_RETURNS.get(c, c): {"removed": frm, "added": to} for c, (frm, to) in changes.items()},
        })
    return {"attachments": result}


# --------------------------------------------------------------------------- fields / legal_values

def _choice_values(ctx: Ctx, name: str) -> list[dict]:
    if name in PRODUCT_SPECIFIC_FIELDS:
        table, col, has_sort = {"version": ("versions", "value", False), "component": ("components", "name", False),
                                "target_milestone": ("milestones", "value", True)}[name]
        rows = ctx.session.execute(text(
            f"SELECT t.{col} AS name, {'t.sortkey' if has_sort else '0'} AS sortkey, t.isactive, p.name AS product, p.id AS product_id "
            f"FROM {table} t JOIN products p ON p.id = t.product_id ORDER BY {'t.sortkey, ' if has_sort else ''}t.{col}, p.name"
        )).all()
        visible = {pid for (pid,) in ctx.session.execute(text("SELECT id FROM products"))}
        return [{"name": r.name, "sort_key": int(r.sortkey), "sortkey": int(r.sortkey), "visibility_values": [r.product],
                 "is_active": bool(r.isactive)} for r in rows if r.product_id in visible and _can_see_product(ctx, r.product_id)]
    if name == "bug_status":
        statuses = ctx.session.execute(text("SELECT id, value, is_open, sortkey FROM bug_status ORDER BY sortkey, value")).all()
        out = []
        for st in [None] + list(statuses):
            sid = st.id if st else None
            cond = "old_status IS NULL" if sid is None else "old_status = :o"
            targets = ctx.session.execute(text(
                f"SELECT bs.id, bs.value FROM status_workflow sw JOIN bug_status bs ON bs.id = sw.new_status WHERE bs.isactive = 1 AND {cond} ORDER BY bs.sortkey"
            ), {} if sid is None else {"o": sid}).all()
            can = [{"name": t.value, "comment_required": validators.comment_required_on_change(ctx, t.id, sid)} for t in targets if t.id != sid]
            out.append({"name": st.value if st else "", "is_open": bool(st.is_open) if st else True,
                        "sort_key": int(st.sortkey) if st else 0, "sortkey": int(st.sortkey) if st else 0,
                        "can_change_to": can, "visibility_values": []})
        return out
    if name == "keywords":
        return [{"name": r.name, "description": r.description} for r in ctx.session.execute(text("SELECT name, description FROM keyworddefs ORDER BY name"))]
    if name == "classification":
        return [{"name": r.name, "sort_key": int(r.sortkey), "sortkey": int(r.sortkey), "visibility_values": []}
                for r in ctx.session.execute(text("SELECT name, sortkey FROM classifications ORDER BY sortkey, name"))]
    rows = ctx.session.execute(text(f"SELECT value, sortkey, visibility_value_id FROM {name} ORDER BY sortkey, value")).all()
    return [{"name": r.value, "sort_key": int(r.sortkey), "sortkey": int(r.sortkey), "visibility_values": []} for r in rows]


def _can_see_product(ctx: Ctx, product_id: int) -> bool:
    rows = ctx.session.execute(text("SELECT group_id FROM group_control_map WHERE product_id = :p AND membercontrol = 3 AND othercontrol = 3"), {"p": product_id}).scalars().all()
    if not rows:
        return True
    mine = set(ctx.session.execute(text("SELECT group_id FROM user_group_map WHERE user_id = :u"), {"u": ctx.user.id}).scalars())
    return all(g in mine for g in rows)


def fields(rc: RequestCtx) -> dict:
    ctx = _ctx(rc)
    ids = _ids(rc.params, "ids")
    names = _ids(rc.params, "names")
    chosen = []
    if ids is not None:
        for i in ids:
            row = ctx.session.execute(text("SELECT * FROM fielddefs WHERE id = :i"), {"i": i}).first() if str(i).strip().isdigit() else None
            if row is None:
                raise user_error("object_does_not_exist_field_id", id=i)
            chosen.append(row)
    if names is not None:
        for n in names:
            row = ctx.session.execute(text("SELECT * FROM fielddefs WHERE name = :n"), {"n": str(n).strip()}).first()
            if row is None:
                raise user_error("object_does_not_exist_field", name=str(n).strip())
            if not any(c.id == row.id for c in chosen):
                chosen.append(row)
    if ids is None and names is None:
        chosen = ctx.session.execute(text("SELECT * FROM fielddefs WHERE obsolete = 0 ORDER BY sortkey, name")).all()
    out = []
    for f in chosen:
        is_select = f.type in (FIELD_TYPE_SINGLE_SELECT, FIELD_TYPE_MULTI_SELECT)
        has_values = (is_select and f.name != "product") or f.name in PRODUCT_SPECIFIC_FIELDS or f.name == "keywords"
        vis_field = ctx.session.execute(text("SELECT name FROM fielddefs WHERE id = :i"), {"i": f.visibility_field_id}).scalar() if f.visibility_field_id else None
        value_field = ctx.session.execute(text("SELECT name FROM fielddefs WHERE id = :i"), {"i": f.value_field_id}).scalar() if f.value_field_id else None
        if f.name in PRODUCT_SPECIFIC_FIELDS:
            value_field = "product"
        data: dict[str, Any] = {
            "id": f.id, "type": f.type, "is_custom": bool(f.custom), "name": f.name, "display_name": f.description,
            "is_mandatory": bool(f.is_mandatory), "is_on_bug_entry": bool(f.enter_bug), "visibility_field": vis_field,
            "visibility_values": [],
        }
        if has_values:
            data["value_field"] = value_field
            data["values"] = _choice_values(ctx, f.name)
        out.append({k: v for k, v in data.items() if filter_wants(rc.params, k)})
    return {"fields": out}


def legal_values(rc: RequestCtx) -> dict:
    ctx = _ctx(rc)
    if rc.params.get("field") is None:
        raise param_required("field")
    field = FIELD_MAP.get(rc.params["field"], rc.params["field"])
    global_selects = {r.name for r in ctx.session.execute(text("SELECT name FROM fielddefs WHERE type IN (2, 3)")) if r.name not in ABNORMAL_SELECTS}
    if field in global_selects:
        values = list(ctx.session.execute(text(f"SELECT value FROM {field} WHERE isactive = 1 ORDER BY sortkey, value")).scalars())
    elif field in PRODUCT_SPECIFIC_FIELDS:
        pid = rc.params.get("product_id")
        if pid is None:
            raise param_required("product_id", function="Bug.legal_values")
        product = ctx.session.execute(text("SELECT id, name FROM products WHERE id = :i"), {"i": pid}).first() if str(pid).isdigit() else None
        if product is None or not _can_see_product(ctx, product.id):
            raise BugzillaError("product_access_denied", f"Sorry, either the product {pid} does not exist or you aren't authorized to access it.")
        sql = {"version": "SELECT value FROM versions WHERE product_id = :p ORDER BY value",
               "target_milestone": "SELECT value FROM milestones WHERE product_id = :p ORDER BY sortkey, value",
               "component": "SELECT name FROM components WHERE product_id = :p ORDER BY name"}[field]
        values = list(ctx.session.execute(text(sql), {"p": product.id}).scalars())
    else:
        raise user_error("invalid_field_name", field=rc.params["field"])
    return {"values": [str(v) for v in values]}
