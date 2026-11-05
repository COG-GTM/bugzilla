"""The subset of Bugzilla::Search that Bug.search exposes over REST.

Supported: id, alias, summary, product, component, classification, status, resolution, priority,
severity, op_sys, platform, version, target_milestone, whiteboard, url, creator, assigned_to,
qa_contact, cc, keywords, creation_time, last_change_time, limit/offset, order, and quicksearch
(ALIAS:, bare ids, words in summary/alias). Boolean charts (f1/o1/v1) are not supported.
Unknown select values are dropped, as Perl does, so they never raise.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import text

from app.core.errors import BugzillaError, user_error
from app.modules.bug.model import Ctx

SELECT_TABLES = {"bug_status": "bug_status", "resolution": "resolution", "priority": "priority",
                 "bug_severity": "bug_severity", "op_sys": "op_sys", "rep_platform": "rep_platform"}
SUBSTR_FIELDS = {"short_desc": "b.short_desc", "status_whiteboard": "b.status_whiteboard", "bug_file_loc": "b.bug_file_loc"}
ORDER_COLUMNS = {"bug_id": "b.bug_id", "id": "b.bug_id", "priority": "b.priority", "bug_status": "b.bug_status",
                 "status": "b.bug_status", "assigned_to": "b.assigned_to", "creation_ts": "b.creation_ts",
                 "creation_time": "b.creation_ts", "delta_ts": "b.delta_ts", "last_change_time": "b.delta_ts",
                 "short_desc": "b.short_desc", "summary": "b.short_desc", "severity": "b.bug_severity",
                 "bug_severity": "b.bug_severity", "component": "c.name", "product": "p.name"}


def _list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        out: list[str] = []
        for v in value:
            out.extend(_list(v))
        return out
    return [str(value)]


def _legal(ctx: Ctx, table: str, values: list[str]) -> list[str]:
    if not values:
        return []
    rows = ctx.session.execute(text(f"SELECT value FROM {table}")).scalars().all()
    legal = {r.lower(): r for r in rows}
    return [legal[v.lower()] for v in values if v.lower() in legal]


def quicksearch(ctx: Ctx, query: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    words = []
    for tok in query.split():
        m = re.match(r"^(ALIAS|alias):(.+)$", tok)
        if m:
            out.setdefault("alias", []).append(m.group(2))
            continue
        if tok.isdigit() or (tok.startswith("#") and tok[1:].isdigit()):
            out.setdefault("bug_id", []).append(tok.lstrip("#"))
            continue
        words.append(tok)
    if words:
        out["_words"] = words
    return out


def search(ctx: Ctx, params: dict) -> list[int]:
    p = {k: v for k, v in params.items() if k not in ("include_fields", "exclude_fields")}
    if "quicksearch" in p:
        p = quicksearch(ctx, str(p["quicksearch"]))
    if "offset" in p and "limit" not in p:
        raise BugzillaError("param_required", "The function Bug.search() requires a limit argument, and that argument was not set.")
    max_results = int(ctx.param("max_search_results") or 10000)
    limit = p.pop("limit", None)
    offset = p.pop("offset", None)
    if limit is not None and str(limit).lstrip("-").isdigit():
        limit = int(limit)
    else:
        limit = None if limit is None else "ignored"
    if limit == 0:
        limit, offset = None, None
    elif limit == "ignored" or limit is None or limit > max_results:
        limit = max_results
    offset = int(offset) if offset is not None and str(offset).isdigit() else None

    where: list[str] = []
    binds: dict[str, Any] = {}
    joins: list[str] = []
    n = 0

    def bind(value: Any) -> str:
        nonlocal n
        n += 1
        key = f"v{n}"
        binds[key] = value
        return f":{key}"

    def in_clause(col: str, values: list[Any]) -> None:
        if values:
            where.append(f"{col} IN (" + ", ".join(bind(v) for v in values) + ")")
        else:
            where.append("1 = 0")

    api_to_db = {"id": "bug_id", "status": "bug_status", "severity": "bug_severity", "platform": "rep_platform",
                 "summary": "short_desc", "whiteboard": "status_whiteboard", "url": "bug_file_loc",
                 "creation_time": "creation_ts", "last_change_time": "delta_ts", "creator": "reporter"}
    for key, raw in list(p.items()):
        f = api_to_db.get(key, key)
        values = _list(raw)
        if f == "bug_id":
            in_clause("b.bug_id", [int(v) for v in values if str(v).isdigit()])
        elif f == "alias":
            alias_ids = list(ctx.session.execute(
                text("SELECT bug_id FROM bugs_aliases WHERE alias IN (" + ", ".join(f":a{i}" for i in range(len(values))) + ")"),
                {f"a{i}": v for i, v in enumerate(values)},
            ).scalars()) if values else []
            in_clause("b.bug_id", alias_ids)
        elif f in SELECT_TABLES:
            legal = _legal(ctx, SELECT_TABLES[f], values)
            if legal:
                in_clause(f"b.{f}", legal)
        elif f == "product":
            in_clause("p.name", values)
        elif f == "component":
            in_clause("c.name", values)
        elif f == "classification":
            in_clause("cl.name", values)
        elif f in ("version", "target_milestone"):
            in_clause(f"b.{f}", values)
        elif f in SUBSTR_FIELDS:
            for v in values:
                for word in str(v).split():
                    where.append(f"{SUBSTR_FIELDS[f]} LIKE {bind('%' + word + '%')}")
        elif f in ("reporter", "assigned_to", "qa_contact"):
            alias_ = {"reporter": "rp", "assigned_to": "ap", "qa_contact": "qp"}[f]
            joins.append(f"LEFT JOIN profiles {alias_} ON {alias_}.userid = b.{f}")
            ors = [f"{alias_}.login_name LIKE {bind('%' + w + '%')}" for v in values for w in str(v).split()]
            where.append("(" + " OR ".join(ors) + ")" if ors else "1 = 0")
        elif f == "cc":
            ors = [f"ccp.login_name LIKE {bind('%' + w + '%')}" for v in values for w in str(v).split()]
            where.append("EXISTS (SELECT 1 FROM cc JOIN profiles ccp ON ccp.userid = cc.who WHERE cc.bug_id = b.bug_id AND (" + " OR ".join(ors) + "))" if ors else "1 = 0")
        elif f == "keywords":
            for v in values:
                for word in str(v).split():
                    where.append(f"EXISTS (SELECT 1 FROM keywords k JOIN keyworddefs kd ON kd.id = k.keywordid WHERE k.bug_id = b.bug_id AND kd.name = {bind(word)})")
        elif f in ("creation_ts", "delta_ts"):
            v = str(values[0]).replace("T", " ").rstrip("Z") if values else None
            if v:
                where.append(f"b.{f} >= {bind(v)}")
        elif f == "_words":
            for word in values:
                where.append(
                    f"(b.short_desc LIKE {bind('%' + word + '%')} OR EXISTS (SELECT 1 FROM bugs_aliases ba WHERE ba.bug_id = b.bug_id AND ba.alias LIKE {bind('%' + word + '%')}))"
                )
        elif f == "longdesc":
            for v in values:
                for word in str(v).split():
                    col = "ft.comments" if ctx.is_insider else "ft.comments_noprivate"
                    where.append(f"{col} LIKE {bind('%' + word + '%')}")
            joins.append("LEFT JOIN bugs_fulltext ft ON ft.bug_id = b.bug_id")
        elif f == "order":
            continue
        else:
            # unknown parameters are ignored, as Bugzilla::Search does with unsupported fields
            continue

    if not where and ctx.param("search_allow_no_criteria") != "1":
        raise user_error("buglist_parameters_required")

    # visibility (Bugzilla::Search::_sql_where visibility clause)
    uid = ctx.user.id
    where.append(
        "(NOT EXISTS (SELECT 1 FROM bug_group_map bgm WHERE bgm.bug_id = b.bug_id AND bgm.group_id NOT IN "
        f"(SELECT group_id FROM user_group_map WHERE user_id = {int(uid)} AND isbless = 0))"
        + (f" OR (b.reporter = {int(uid)} AND b.reporter_accessible = 1) OR b.assigned_to = {int(uid)} OR b.qa_contact = {int(uid)}"
           f" OR (b.cclist_accessible = 1 AND EXISTS (SELECT 1 FROM cc WHERE cc.bug_id = b.bug_id AND cc.who = {int(uid)}))" if uid else "")
        + ")"
    )

    order_cols = []
    for o in _list(p.get("order")) if p.get("order") else []:
        for part in str(o).split(","):
            part = part.strip()
            direction = "DESC" if part.lower().endswith(" desc") else "ASC"
            name = re.sub(r"\s+(asc|desc)$", "", part, flags=re.I)
            if name in ORDER_COLUMNS:
                order_cols.append(f"{ORDER_COLUMNS[name]} {direction}")
    if not order_cols:
        order_cols = ["b.bug_id ASC"]
    elif "b.bug_id ASC" not in order_cols and "b.bug_id DESC" not in order_cols:
        order_cols.append("b.bug_id ASC")

    sql = (
        "SELECT DISTINCT b.bug_id FROM bugs b JOIN products p ON p.id = b.product_id JOIN components c ON c.id = b.component_id "
        "JOIN classifications cl ON cl.id = p.classification_id " + " ".join(dict.fromkeys(joins)) +
        " WHERE " + " AND ".join(where) + " ORDER BY " + ", ".join(order_cols)
    )
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
        if offset:
            sql += f" OFFSET {int(offset)}"
    return list(ctx.session.execute(text(sql), binds).scalars())
