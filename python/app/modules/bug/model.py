"""Bugzilla::Bug / Bugzilla::Comment / Bugzilla::Attachment read side: loading rows,
visibility rules and the WebService hash shapes (``_bug_to_hash`` & friends)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.auth import User
from app.core.errors import BugzillaError, user_error
from app.core.fields import filter_wants
from app.modules.bugzilla.service import iso

# API name -> DB column (Bugzilla::Bug::FIELD_MAP)
FIELD_MAP = {
    "blocks": "blocked", "commentprivacy": "comment_is_private", "creation_time": "creation_ts",
    "creator": "reporter", "description": "comment", "depends_on": "dependson", "dupe_of": "dup_id",
    "id": "bug_id", "is_confirmed": "everconfirmed", "is_cc_accessible": "cclist_accessible",
    "is_creator_accessible": "reporter_accessible", "last_change_time": "delta_ts", "platform": "rep_platform",
    "severity": "bug_severity", "status": "bug_status", "summary": "short_desc", "url": "bug_file_loc",
    "whiteboard": "status_whiteboard",
}
API_NAME = {v: k for k, v in FIELD_MAP.items()}
API_NAME["bug_group"] = "groups"

CMT_NORMAL, CMT_DUPE_OF, CMT_HAS_DUPE, CMT_ATTACHMENT_CREATED, CMT_ATTACHMENT_UPDATED = 0, 1, 2, 5, 6
TIMETRACKING_FIELDS = ("estimated_time", "remaining_time", "work_time", "percentage_complete")
_SECRET = os.environ.get("BZ_SITE_SECRET", base64.b64encode(os.urandom(24)).decode())


def _as_list(value: Any) -> list:
    if value is None:
        return []
    return list(value) if isinstance(value, (list, tuple)) else [value]


def _ts(value: datetime | None) -> str | None:
    return iso(value)


@dataclass
class Ctx:
    session: Session
    user: User
    params: dict[str, Any]
    bz_params: dict[str, Any]

    def param(self, name: str, default: Any = None) -> Any:
        return self.bz_params.get(name, default)

    @property
    def is_insider(self) -> bool:
        g = self.param("insidergroup")
        return bool(g) and self.user.in_group(g)

    @property
    def is_timetracker(self) -> bool:
        g = self.param("timetrackinggroup")
        return bool(g) and self.user.in_group(g)

    @property
    def email_suffix(self) -> str:
        return self.param("emailsuffix") or ""


@dataclass
class Person:
    id: int
    login: str
    realname: str

    def to_hash(self, ctx: Ctx, params: dict, prefix: str) -> dict:
        data = {
            "id": self.id,
            "real_name": self.realname or "",
            "name": self.login,
            "email": self.login + ctx.email_suffix,
        }
        return {k: v for k, v in data.items() if filter_wants(params, k, prefix + ".")}


@dataclass
class BugRow:
    row: Any
    aliases: list[str] = field(default_factory=list)
    cc_users: list[Person] = field(default_factory=list)
    depends_on: list[int] = field(default_factory=list)
    blocks: list[int] = field(default_factory=list)
    groups: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    see_also: list[str] = field(default_factory=list)
    dup_id: int | None = None
    product: str = ""
    component: str = ""
    classification: str = ""
    reporter: Person | None = None
    assignee: Person | None = None
    qa: Person | None = None
    is_open: bool = True

    @property
    def id(self) -> int:
        return self.row.bug_id


# --------------------------------------------------------------------------- lookups

def person(session: Session, user_id: int | None) -> Person | None:
    if not user_id:
        return None
    r = session.execute(
        text("SELECT userid, login_name, realname FROM profiles WHERE userid = :id"), {"id": user_id}
    ).first()
    return Person(r.userid, r.login_name, r.realname or "") if r else None


def person_by_login(session: Session, login: str) -> Person | None:
    r = session.execute(
        text("SELECT userid, login_name, realname FROM profiles WHERE LOWER(login_name) = LOWER(:l)"),
        {"l": login.strip()},
    ).first()
    return Person(r.userid, r.login_name, r.realname or "") if r else None


def alias_to_id(session: Session, alias: str) -> int | None:
    return session.execute(text("SELECT bug_id FROM bugs_aliases WHERE alias = :a"), {"a": alias}).scalar()


def field_id(session: Session, name: str) -> int:
    fid = session.execute(text("SELECT id FROM fielddefs WHERE name = :n"), {"n": name}).scalar()
    if fid is None:
        raise BugzillaError("invalid_field_name", f"Can't use \"{name}\" as a field name.")
    return fid


def status_row(session: Session, name: str):
    return session.execute(
        text("SELECT id, value, is_open, sortkey FROM bug_status WHERE value = :v"), {"v": name}
    ).first()


def status_by_id(session: Session, status_id: int):
    return session.execute(
        text("SELECT id, value, is_open, sortkey FROM bug_status WHERE id = :v"), {"v": status_id}
    ).first()


# --------------------------------------------------------------------------- visibility

def visible_bug_ids(ctx: Ctx, bug_ids: list[int]) -> set[int]:
    """Bugzilla::User::_visible_bugs_check_and: a bug is visible when the user is in every group on
    it, or is the reporter/assignee/qa/cc with the matching *_accessible flag."""
    if not bug_ids:
        return set()
    uid = ctx.user.id
    rows = ctx.session.execute(
        text(
            "SELECT b.bug_id, b.reporter, b.assigned_to, b.qa_contact, b.reporter_accessible, "
            "b.cclist_accessible, "
            "(SELECT COUNT(*) FROM bug_group_map bgm WHERE bgm.bug_id = b.bug_id) AS n_groups, "
            "(SELECT COUNT(DISTINCT bgm.group_id) FROM bug_group_map bgm JOIN user_group_map ugm "
            "   ON ugm.group_id = bgm.group_id AND ugm.user_id = :uid AND ugm.isbless = 0 "
            "   WHERE bgm.bug_id = b.bug_id) AS n_mine, "
            "(SELECT COUNT(*) FROM cc WHERE cc.bug_id = b.bug_id AND cc.who = :uid) AS is_cc "
            "FROM bugs b WHERE b.bug_id IN (" + ",".join(str(int(i)) for i in bug_ids) + ")"
        ),
        {"uid": uid},
    ).all()
    out = set()
    for r in rows:
        if r.n_groups == 0 or r.n_groups == r.n_mine:
            out.add(r.bug_id)
        elif uid and (
            (r.reporter == uid and r.reporter_accessible)
            or r.assigned_to == uid
            or (r.qa_contact == uid)
            or (r.is_cc and r.cclist_accessible)
        ):
            out.add(r.bug_id)
    return out


def can_see_bug(ctx: Ctx, bug_id: int) -> bool:
    return bug_id in visible_bug_ids(ctx, [bug_id])


def can_edit_product(ctx: Ctx, product_id: int) -> bool:
    rows = ctx.session.execute(
        text("SELECT group_id FROM group_control_map WHERE product_id = :p AND canedit != 0"), {"p": product_id}
    ).scalars().all()
    if not rows:
        return True
    mine = set(
        ctx.session.execute(
            text("SELECT group_id FROM user_group_map WHERE user_id = :u AND isbless = 0"), {"u": ctx.user.id}
        ).scalars()
    )
    return all(g in mine for g in rows)


def in_group_for_product(ctx: Ctx, group: str, product_id: int) -> bool:
    """User->in_group(name, product_id): membership, or the product's group_control_map grant."""
    if ctx.user.in_group(group):
        return True
    col = {"editbugs": "editbugs", "canconfirm": "canconfirm", "editcomponents": "editcomponents"}.get(group)
    if not col:
        return False
    r = ctx.session.execute(
        text(
            f"SELECT 1 FROM group_control_map gcm JOIN user_group_map ugm ON ugm.group_id = gcm.group_id "
            f"WHERE gcm.product_id = :p AND gcm.{col} = 1 AND ugm.user_id = :u AND ugm.isbless = 0 LIMIT 1"
        ),
        {"p": product_id, "u": ctx.user.id},
    ).first()
    return r is not None


# --------------------------------------------------------------------------- Bug->check

def resolve_bug_id(session: Session, value: Any, field_name: str | None = None) -> int:
    """Bugzilla::Bug->new/check id resolution: ``#12``, ``12`` or an alias."""
    if value is None:
        raise user_error("improper_bug_id_field_value_undef", field=field_name or "")
    raw = str(value).strip()
    candidate = raw[1:] if raw.startswith("#") and raw[1:].isdigit() else raw
    if candidate.isdigit():
        bug_id = int(candidate)
        exists = session.execute(text("SELECT bug_id FROM bugs WHERE bug_id = :id"), {"id": bug_id}).scalar()
        if exists is None:
            raise user_error("invalid_bug_id_non_existent", bug_id=bug_id)
        return bug_id
    if raw:
        bug_id = alias_to_id(session, raw)
        if bug_id:
            return bug_id
    raise user_error("invalid_bug_id_or_alias", bug_id=raw)


def check_bug(ctx: Ctx, value: Any, field_name: str | None = None, *, visibility: bool = True) -> BugRow:
    bug_id = resolve_bug_id(ctx.session, value, field_name)
    if visibility and not can_see_bug(ctx, bug_id):
        shown = value if field_name else bug_id
        if ctx.user.is_logged_in:
            raise user_error("bug_access_denied", bug_id=shown)
        raise user_error("bug_access_query", bug_id=shown)
    return load_bug(ctx, bug_id)


def check_bug_for_edit(ctx: Ctx, value: Any) -> BugRow:
    bug = check_bug(ctx, value)
    if not can_edit_product(ctx, bug.row.product_id):
        raise user_error("product_edit_denied", product=bug.product)
    return bug


def load_bug(ctx: Ctx, bug_id: int) -> BugRow:
    s = ctx.session
    row = s.execute(
        text(
            "SELECT b.*, p.name AS product_name, c.name AS component_name, cl.name AS classification_name, "
            "st.is_open AS status_is_open "
            "FROM bugs b JOIN products p ON p.id = b.product_id JOIN components c ON c.id = b.component_id "
            "JOIN classifications cl ON cl.id = p.classification_id "
            "LEFT JOIN bug_status st ON st.value = b.bug_status WHERE b.bug_id = :id"
        ),
        {"id": bug_id},
    ).first()
    if row is None:
        raise user_error("invalid_bug_id_non_existent", bug_id=bug_id)
    bug = BugRow(row=row, product=row.product_name, component=row.component_name,
                 classification=row.classification_name, is_open=bool(row.status_is_open))
    bug.aliases = list(s.execute(text("SELECT alias FROM bugs_aliases WHERE bug_id = :id ORDER BY alias"), {"id": bug_id}).scalars())
    bug.cc_users = [
        Person(r.userid, r.login_name, r.realname or "")
        for r in s.execute(
            text("SELECT p.userid, p.login_name, p.realname FROM cc JOIN profiles p ON p.userid = cc.who "
                 "WHERE cc.bug_id = :id ORDER BY p.login_name"), {"id": bug_id}
        )
    ]
    bug.depends_on = list(s.execute(text("SELECT dependson FROM dependencies WHERE blocked = :id ORDER BY dependson"), {"id": bug_id}).scalars())
    bug.blocks = list(s.execute(text("SELECT blocked FROM dependencies WHERE dependson = :id ORDER BY blocked"), {"id": bug_id}).scalars())
    bug.groups = list(s.execute(text("SELECT g.name FROM bug_group_map m JOIN groups g ON g.id = m.group_id WHERE m.bug_id = :id ORDER BY g.name"), {"id": bug_id}).scalars())
    bug.keywords = list(s.execute(text("SELECT kd.name FROM keywords k JOIN keyworddefs kd ON kd.id = k.keywordid WHERE k.bug_id = :id ORDER BY kd.name"), {"id": bug_id}).scalars())
    bug.see_also = list(s.execute(text("SELECT value FROM bug_see_also WHERE bug_id = :id ORDER BY id"), {"id": bug_id}).scalars())
    bug.dup_id = s.execute(text("SELECT dupe_of FROM duplicates WHERE dupe = :id"), {"id": bug_id}).scalar()
    bug.reporter = person(s, row.reporter)
    bug.assignee = person(s, row.assigned_to)
    bug.qa = person(s, row.qa_contact)
    return bug


# --------------------------------------------------------------------------- hashes

def bug_to_hash(ctx: Ctx, bug: BugRow, params: dict) -> dict:
    r = bug.row
    want = lambda name, types=("default",): filter_wants(params, name, types=types)  # noqa: E731
    item: dict[str, Any] = {}
    plain = {
        "deadline": _ts(r.deadline)[:10] if r.deadline else None,
        "id": r.bug_id,
        "is_confirmed": bool(r.everconfirmed),
        "op_sys": r.op_sys,
        "platform": r.rep_platform,
        "priority": r.priority,
        "resolution": r.resolution,
        "severity": r.bug_severity,
        "status": r.bug_status,
        "summary": r.short_desc,
        "target_milestone": r.target_milestone,
        "url": r.bug_file_loc,
        "version": r.version,
        "whiteboard": r.status_whiteboard,
    }
    for k, v in plain.items():
        if want(k):
            item[k] = v
    if want("alias"):
        item["alias"] = list(bug.aliases)
    if want("assigned_to"):
        item["assigned_to"] = bug.assignee.login
        item["assigned_to_detail"] = bug.assignee.to_hash(ctx, params, "assigned_to")
    if want("blocks"):
        item["blocks"] = list(bug.blocks)
    if want("classification"):
        item["classification"] = bug.classification
    if want("component"):
        item["component"] = bug.component
    if want("cc"):
        item["cc"] = [p.login for p in bug.cc_users]
        item["cc_detail"] = [p.to_hash(ctx, params, "cc") for p in bug.cc_users]
    if want("creation_time"):
        item["creation_time"] = _ts(r.creation_ts)
    if want("creator"):
        item["creator"] = bug.reporter.login
        item["creator_detail"] = bug.reporter.to_hash(ctx, params, "creator")
    if want("depends_on"):
        item["depends_on"] = list(bug.depends_on)
    if want("dupe_of"):
        item["dupe_of"] = bug.dup_id
    if want("groups"):
        item["groups"] = list(bug.groups)
    if want("is_open"):
        item["is_open"] = bug.is_open
    if want("keywords"):
        item["keywords"] = list(bug.keywords)
    if want("last_change_time"):
        item["last_change_time"] = _ts(r.delta_ts)
    if want("product"):
        item["product"] = bug.product
    if want("qa_contact"):
        item["qa_contact"] = bug.qa.login if bug.qa else ""
        if bug.qa:
            item["qa_contact_detail"] = bug.qa.to_hash(ctx, params, "qa_contact")
    if want("see_also"):
        item["see_also"] = list(bug.see_also)
    if want("flags"):
        item["flags"] = flags_for(ctx, bug_id=r.bug_id)
    if want("tags", ("extra",)):
        item["tags"] = []
    if ctx.is_timetracker:
        if want("estimated_time"):
            item["estimated_time"] = _double(r.estimated_time)
        if want("remaining_time"):
            item["remaining_time"] = _double(r.remaining_time)
        if want("actual_time"):
            total = ctx.session.execute(
                text("SELECT SUM(work_time) FROM longdescs WHERE bug_id = :id"), {"id": r.bug_id}
            ).scalar()
            item["actual_time"] = _double(total or 0)
    if want("is_cc_accessible"):
        item["is_cc_accessible"] = bool(r.cclist_accessible)
    if want("is_creator_accessible"):
        item["is_creator_accessible"] = bool(r.reporter_accessible)
    return item


def _double(value: Any) -> int | float:
    """Perl's JSON encoder prints 0.00 as 0: integral doubles become ints."""
    f = float(value)
    return int(f) if f == int(f) else f


def update_token(bug: BugRow) -> str:
    data = f"{bug.id}-{bug.row.delta_ts}".encode()
    return base64.b64encode(hmac.new(_SECRET.encode(), data, hashlib.sha256).digest()).decode().rstrip("=")


def flags_for(ctx: Ctx, *, bug_id: int | None = None, attach_id: int | None = None) -> list[dict]:
    where = "f.bug_id = :id AND f.attach_id IS NULL" if attach_id is None else "f.attach_id = :id"
    rows = ctx.session.execute(
        text(
            "SELECT f.id, ft.name, f.type_id, f.creation_date, f.modification_date, f.status, "
            "s.login_name AS setter, r.login_name AS requestee FROM flags f JOIN flagtypes ft ON ft.id = f.type_id "
            "LEFT JOIN profiles s ON s.userid = f.setter_id LEFT JOIN profiles r ON r.userid = f.requestee_id "
            f"WHERE {where} ORDER BY f.id"
        ),
        {"id": bug_id if attach_id is None else attach_id},
    ).all()
    out = []
    for f in rows:
        item = {
            "id": f.id, "name": f.name, "type_id": f.type_id, "creation_date": _ts(f.creation_date),
            "modification_date": _ts(f.modification_date), "status": f.status,
        }
        if f.setter:
            item["setter"] = f.setter
        if f.requestee:
            item["requestee"] = f.requestee
        out.append(item)
    return out


def comment_body_full(session: Session, c: Any) -> str:
    body = c.thetext
    t = c.type or 0
    if t == CMT_DUPE_OF:
        text_ = f"{body}\n*** This bug has been marked as a duplicate of bug {c.extra_data} ***"
    elif t == CMT_HAS_DUPE:
        text_ = f"*** Bug {c.extra_data} has been marked as a duplicate of this bug. ***"
    elif t in (CMT_ATTACHMENT_CREATED, CMT_ATTACHMENT_UPDATED):
        desc = session.execute(
            text("SELECT description FROM attachments WHERE attach_id = :id"), {"id": c.extra_data}
        ).scalar() or ""
        head = "Created attachment" if t == CMT_ATTACHMENT_CREATED else "Comment on attachment"
        text_ = f"{head} {c.extra_data}\n{desc}\n{body}"
    else:
        return body
    return text_.strip()  # Template Toolkit TRIM


def comment_to_hash(ctx: Ctx, c: Any, params: dict, prefix: str = "", count: int | None = None) -> dict:
    attach_id = int(c.extra_data) if (c.type in (CMT_ATTACHMENT_CREATED, CMT_ATTACHMENT_UPDATED) and c.extra_data) else None
    if count is None:
        # Bugzilla::Comment->count (used for comment_ids lookups): comments up to this timestamp
        count = ctx.session.execute(
            text("SELECT COUNT(*) FROM longdescs WHERE bug_id = :b AND bug_when <= :w"),
            {"b": c.bug_id, "w": c.bug_when},
        ).scalar() - 1
    data = {
        "id": c.comment_id,
        "bug_id": c.bug_id,
        "creator": c.login_name,
        "time": _ts(c.bug_when),
        "creation_time": _ts(c.bug_when),
        "is_private": bool(c.isprivate),
        "text": comment_body_full(ctx.session, c),
        "attachment_id": attach_id,
        "count": count,
    }
    if ctx.param("comment_taggers_group"):
        data["tags"] = list(
            ctx.session.execute(
                text("SELECT tag FROM longdescs_tags WHERE comment_id = :id ORDER BY tag"), {"id": c.comment_id}
            ).scalars()
        )
    return {k: v for k, v in data.items() if filter_wants(params, k, prefix)}


COMMENT_SQL = (
    "SELECT l.comment_id, l.bug_id, l.who, l.bug_when, l.work_time, l.thetext, l.isprivate, l.type, "
    "l.extra_data, p.login_name FROM longdescs l JOIN profiles p ON p.userid = l.who "
)


def bug_comments(session: Session, bug_id: int, after: str | None = None) -> list:
    sql = COMMENT_SQL + "WHERE l.bug_id = :id"
    binds: dict[str, Any] = {"id": bug_id}
    if after:
        sql += " AND l.bug_when > :after"
        binds["after"] = after.replace("T", " ").rstrip("Z")
    sql += " ORDER BY l.bug_when, l.comment_id"
    return session.execute(text(sql), binds).all()


def attachment_to_hash(ctx: Ctx, a: Any, params: dict) -> dict:
    data = {
        "creation_time": _ts(a.creation_ts),
        "last_change_time": _ts(a.modification_time),
        "id": a.attach_id,
        "bug_id": a.bug_id,
        "file_name": a.filename,
        "summary": a.description,
        "content_type": a.mimetype,
        "is_private": int(a.isprivate),
        "is_obsolete": int(a.isobsolete),
        "is_patch": int(a.ispatch),
    }
    item = {k: v for k, v in data.items() if filter_wants(params, k)}
    if filter_wants(params, "creator"):
        item["creator"] = a.login_name
    if filter_wants(params, "data"):
        blob = ctx.session.execute(text("SELECT thedata FROM attach_data WHERE id = :id"), {"id": a.attach_id}).scalar()
        item["data"] = base64.b64encode(blob or b"").decode()
    if filter_wants(params, "size"):
        size = ctx.session.execute(text("SELECT LENGTH(thedata) FROM attach_data WHERE id = :id"), {"id": a.attach_id}).scalar()
        item["size"] = int(size or 0)
    if filter_wants(params, "flags"):
        item["flags"] = flags_for(ctx, attach_id=a.attach_id)
    return item


ATTACHMENT_SQL = (
    "SELECT a.attach_id, a.bug_id, a.creation_ts, a.modification_time, a.description, a.mimetype, a.ispatch, "
    "a.filename, a.submitter_id, a.isobsolete, a.isprivate, p.login_name FROM attachments a "
    "JOIN profiles p ON p.userid = a.submitter_id "
)
