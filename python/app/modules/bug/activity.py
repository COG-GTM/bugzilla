"""bugs_activity writes (Bugzilla::Bug::LogActivityEntry) and Bug.history reads (get_activity)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text

from app.modules.bug.model import API_NAME, TIMETRACKING_FIELDS, Ctx, field_id
from app.modules.bugzilla.service import iso

MAX_LINE_LENGTH = 254


def _wrap_point(value: str, limit: int) -> int:
    pos = value.rfind(",", 0, limit)
    if pos <= 0:
        pos = value.rfind(" ", 0, limit)
    return pos + 1 if pos > 0 else limit


def log_activity(ctx: Ctx, bug_id: int, field: str, removed: Any, added: Any, when, *,
                 comment_id: int | None = None, attach_id: int | None = None, who: int | None = None) -> None:
    removed = "" if removed is None else str(removed)
    added = "" if added is None else str(added)
    fid = field_id(ctx.session, field)
    while removed or added:
        rem_s, add_s = removed, added
        if len(rem_s) > MAX_LINE_LENGTH:
            cut = _wrap_point(removed, MAX_LINE_LENGTH)
            rem_s, removed = removed[:cut], removed[cut:]
        else:
            removed = ""
        if len(add_s) > MAX_LINE_LENGTH:
            cut = _wrap_point(added, MAX_LINE_LENGTH)
            add_s, added = added[:cut], added[cut:]
        else:
            added = ""
        ctx.session.execute(
            text(
                "INSERT INTO bugs_activity (bug_id, who, bug_when, fieldid, removed, added, comment_id, attach_id) "
                "VALUES (:b, :w, :t, :f, :r, :a, :c, :at)"
            ),
            {"b": bug_id, "w": who or ctx.user.id, "t": when, "f": fid, "r": rem_s, "a": add_s,
             "c": comment_id, "at": attach_id},
        )


def _join(field: str, old: str, new: str) -> str:
    if not old:
        return new
    if not new:
        return old
    if field in ("longdesc", "short_desc", "status_whiteboard", "bug_file_loc"):
        return old + new
    return old + ", " + new if not old.endswith(",") else old + " " + new


def history(ctx: Ctx, bug_id: int, new_since: str | None = None) -> list[dict]:
    sql = (
        "SELECT fd.name, ba.attach_id, ba.bug_when, ba.removed, ba.added, p.login_name, ba.comment_id "
        "FROM bugs_activity ba "
        + ("" if ctx.is_insider else "LEFT JOIN attachments att ON att.attach_id = ba.attach_id ")
        + "INNER JOIN fielddefs fd ON ba.fieldid = fd.id INNER JOIN profiles p ON p.userid = ba.who "
        "WHERE ba.bug_id = :id "
    )
    binds: dict[str, Any] = {"id": bug_id}
    if new_since:
        sql += "AND ba.bug_when > :since "
        binds["since"] = new_since.replace("T", " ").rstrip("Z")
    if not ctx.is_insider:
        sql += "AND COALESCE(att.isprivate, 0) = 0 "
    sql += "ORDER BY ba.bug_when, ba.comment_id"
    rows = ctx.session.execute(text(sql), binds).all()

    operations: list[dict] = []
    op: dict[str, Any] = {}
    changes: list[dict] = []
    for name, attach_id, when, removed, added, who, comment_id in rows:
        removed = removed or ""
        added = added or ""
        if name in TIMETRACKING_FIELDS and not ctx.is_timetracker:
            continue
        if name == "longdescs.isprivate" and not ctx.is_insider and added:
            continue
        if op.get("who") and (who != op["who"] or when != op["when"]):
            op["changes"] = changes
            operations.append(op)
            op, changes = {}, []
        if (op.get("who") and who == op["who"] and when == op["when"] and name == op["fieldname"]
                and (comment_id or 0) == (op.get("comment_id") or 0) and (attach_id or 0) == (op.get("attachid") or 0)):
            old = changes.pop()
            removed = _join(name, old["removed"], removed)
            added = _join(name, old["added"], added)
        op.update(who=who, when=when, fieldname=name, attachid=attach_id, comment_id=comment_id)
        change = {"field_name": API_NAME.get(name, name), "removed": removed, "added": added}
        if attach_id:
            change["attachment_id"] = attach_id
        changes.append(change)
    if op.get("who"):
        op["changes"] = changes
        operations.append(op)
    return [{"when": iso(o["when"]), "who": o["who"], "changes": o["changes"]} for o in operations]
