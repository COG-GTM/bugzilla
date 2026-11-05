"""Port of the read side of Bugzilla::WebService::User: login/logout/valid_login and get.

create/update live in app.modules.user.writes. offer_account_by_email has no REST resource
(Bugzilla/WebService/Server/REST/Resources/User.pm only maps /user and /user/{id_or_name}) and
is intentionally left on Perl.
"""
import re

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.auth import User, authenticate, issue_login_token, logout_all, user_group_ids
from app.core.errors import BugzillaError, param_required
from app.core.params import param
from app.core.rest import Ctx

GRANT_DIRECT = 0
GRANT_REGEXP = 2
GROUP_MEMBERSHIP = 0
GROUP_BLESS = 1
GROUP_VISIBLE = 2
USER_COLUMNS = "userid, login_name, realname, cryptpassword, disabledtext, disable_mail, is_enabled, extern_id"


# ---------------------------------------------------------------- errors
def object_does_not_exist(kind: str, name, by_id: bool = False) -> BugzillaError:
    what = f"with the id '{name}'" if by_id else f"named '{name}'"
    msg = f"There is no {kind} {what}."
    if kind == "user":
        msg += " Either you mis-typed the name or that user has not yet registered for a Bugzilla account."
    return BugzillaError("object_does_not_exist", msg)


def param_must_be_numeric(function: str, name: str | None = None) -> BugzillaError:
    which = f"parameter {name} " if name else "parameter "
    return BugzillaError("param_must_be_numeric", f"Invalid {which}passed to {function}: It must be numeric.")


def auth_failure(group: str, action: str, obj: str, reason: str | None = None) -> BugzillaError:
    """template/en/default/global/user-error.html.tmpl#auth_failure (the variants User.pm raises)."""
    if reason == "not_visible":
        return BugzillaError("auth_failure", "Sorry, you do not have permission to access that user.")
    actions = {"add": "add new", "edit": "add, modify or delete", "access": "access"}
    msg = f"Sorry, you aren't a member of the '{group}' group, and "
    if reason == "cant_bless":
        msg += "you don't have permissions to add or remove people from a group, and "
    return BugzillaError("auth_failure", msg + f"so you are not authorized to {actions.get(action, action)} {obj}.")


def invalid_group_name(name) -> BugzillaError:
    return BugzillaError("invalid_group_name", f"The group you specified, {name}, is not valid here.")


# ---------------------------------------------------------------- params
def perl_true(value) -> bool:
    """Perl truthiness of a JSON/query value (false, 0, "", "0" and null are false)."""
    if value is None or value is False:
        return False
    if isinstance(value, (int, float)):
        return value != 0
    return str(value) not in ("", "0")


def array_param(ctx: Ctx, name: str):
    """Bugzilla::WebService::Util::validate: scalars become one-element arrays; csv is NOT split."""
    value = ctx.params.get(name)
    if value is None:
        return None
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        raise BugzillaError("param_scalar_array_required",
                            f"A scalar or an array reference was expected, but you passed a hash reference "
                            f"for the '{name}' parameter.")
    return [value]


def path_param(id_or_name: str) -> dict:
    """REST /user/{id_or_name}: digits select ``ids``, anything else ``names``."""
    return {"ids": [id_or_name]} if re.fullmatch(r"\d+", id_or_name) else {"names": [id_or_name]}


# ---------------------------------------------------------------- loading users / groups
def _row_to_dict(row) -> dict:
    return {"id": row.userid, "login": row.login_name, "realname": row.realname or "",
            "cryptpassword": row.cryptpassword, "disabledtext": row.disabledtext or "",
            "disable_mail": int(row.disable_mail or 0), "is_enabled": int(row.is_enabled or 0),
            "extern_id": row.extern_id}


def user_by_id(db: Session, user_id) -> dict | None:
    row = db.execute(text(f"SELECT {USER_COLUMNS} FROM profiles WHERE userid = :id"), {"id": int(user_id)}).first()
    return _row_to_dict(row) if row else None


def user_by_name(db: Session, login) -> dict | None:
    row = db.execute(text(f"SELECT {USER_COLUMNS} FROM profiles WHERE login_name = :n"), {"n": str(login)}).first()
    return _row_to_dict(row) if row else None


def check_user_name(db: Session, login) -> dict:
    """Bugzilla::User->check($name)."""
    login = "" if login is None else str(login).strip()
    if not login:
        raise BugzillaError("object_not_specified", "You must select/enter a user.")
    user = user_by_name(db, login)
    if not user:
        raise object_does_not_exist("user", login)
    return user


def check_user_id(db: Session, user_id) -> dict:
    """Bugzilla::User->check({id => $id}) (used by params_to_objects for update)."""
    if not re.fullmatch(r"\d+", str(user_id)):
        raise param_must_be_numeric("Bugzilla::User::new_from_list")
    user = user_by_id(db, user_id)
    if not user:
        raise object_does_not_exist("user", user_id, by_id=True)
    return user


def users_from_list(db: Session, ids) -> list[dict]:
    """Bugzilla::User->new_from_list: every id must be natural; missing ids are skipped; sorted by login."""
    for i in ids:
        if not re.fullmatch(r"\d+", str(i)):
            raise param_must_be_numeric("Bugzilla::User::new_from_list")
    if not ids:
        return []
    rows = db.execute(text(f"SELECT {USER_COLUMNS} FROM profiles WHERE userid IN "
                           f"({','.join(str(int(i)) for i in ids)}) ORDER BY login_name"))
    return [_row_to_dict(r) for r in rows]


def group_by_id(db: Session, group_id) -> dict | None:
    row = db.execute(text("SELECT id, name, description FROM groups WHERE id = :id"), {"id": int(group_id)}).first()
    return dict(row._mapping) if row else None


def group_by_name(db: Session, name) -> dict | None:
    row = db.execute(text("SELECT id, name, description FROM groups WHERE name = :n"), {"n": str(name)}).first()
    return dict(row._mapping) if row else None


def groups_of(db: Session, user_id: int) -> list[dict]:
    """$user->groups: direct memberships plus inherited ones, sorted by name (Group LIST_ORDER)."""
    ids = user_group_ids(db, user_id)
    if not ids:
        return []
    rows = db.execute(text(f"SELECT id, name, description FROM groups WHERE id IN ({','.join(map(str, ids))}) "
                           "ORDER BY isbuggroup, name"))
    return [dict(r._mapping) for r in rows]


def bless_group_ids(db: Session, user: User) -> set[int]:
    """Bugzilla::User::bless_groups (ids only)."""
    if not user.is_logged_in:
        return set()
    if user.in_group("editusers"):
        return {r[0] for r in db.execute(text("SELECT id FROM groups"))}
    if perl_true(param("usevisibilitygroups")) and not visible_group_ids(db, user):
        return set()
    direct = {r[0] for r in db.execute(text(
        "SELECT DISTINCT group_id FROM user_group_map WHERE user_id = :u AND isbless = 1"), {"u": user.id})}
    inherited = set()
    if user.group_ids:
        inherited = {r[0] for r in db.execute(text(
            "SELECT DISTINCT grantor_id FROM group_group_map WHERE grant_type = :t AND member_id IN "
            f"({','.join(map(str, user.group_ids))})"), {"t": GROUP_BLESS})}
    ids = direct | inherited
    if perl_true(param("usevisibilitygroups")):
        ids &= visible_group_ids(db, user)
    return ids


def visible_group_ids(db: Session, user: User) -> set[int]:
    """Bugzilla::User::visible_groups_inherited: groups granted GROUP_VISIBLE to any of the caller's
    groups (visible_groups_direct), plus every group that is a member of those
    (Bugzilla::Group::flatten_group_membership). Membership alone grants no visibility."""
    if not user.is_logged_in:
        return set()
    if not perl_true(param("usevisibilitygroups")):
        return {r[0] for r in db.execute(text("SELECT id FROM groups"))}
    member_ids = ",".join(map(str, user.group_ids)) or "-1"
    visible = {r[0] for r in db.execute(text(
        f"SELECT DISTINCT grantor_id FROM group_group_map WHERE member_id IN ({member_ids}) AND grant_type = :t"),
        {"t": GROUP_VISIBLE})}
    todo = list(visible)
    while todo:
        node = todo.pop(0)
        for (member,) in db.execute(text("SELECT member_id FROM group_group_map WHERE grantor_id = :g "
                                         "AND grant_type = :t"), {"g": node, "t": GROUP_MEMBERSHIP}):
            if member not in visible:
                visible.add(member)
                todo.append(member)
    return visible


def can_see_user(db: Session, viewer: User, other_id: int) -> bool:
    if not perl_true(param("usevisibilitygroups")):
        return db.execute(text("SELECT 1 FROM profiles WHERE userid = :id"), {"id": other_id}).first() is not None
    visible = visible_group_ids(db, viewer)
    if not visible:
        return False
    return db.execute(text("SELECT 1 FROM user_group_map WHERE user_id = :id AND isbless = 0 AND group_id IN "
                           f"({','.join(map(str, visible))})"), {"id": other_id}).first() is not None


# ---------------------------------------------------------------- match
def match(db: Session, viewer: User, s: str, limit: int | None, exclude_disabled: bool) -> list[dict]:
    """Bugzilla::User::match: wildcard search, else exact login, else substring (len >= 3)."""
    s = (s or "").strip()
    if not s:
        return []
    use_vis = perl_true(param("usevisibilitygroups"))
    join, vis_clause = "", ""
    if use_vis:
        join = "INNER JOIN user_group_map ON user_group_map.user_id = profiles.userid "
        vis_clause = ("AND isbless = 0 AND group_id IN (-1" +
                      "".join(f", {g}" for g in visible_group_ids(db, viewer)) + ") ")
    tail = ("AND is_enabled = 1 " if exclude_disabled else "") + (f"LIMIT {int(limit)}" if limit else "")
    users: list[dict] = []
    if "*" in s and viewer.is_logged_in:
        wild = s.replace("*", "%")
        rows = db.execute(text(f"SELECT DISTINCT userid FROM profiles {join}WHERE (login_name LIKE :w OR realname LIKE :w) "
                               f"{vis_clause}{tail}"), {"w": wild})
        users = users_from_list(db, [r[0] for r in rows])
    else:
        row = db.execute(text("SELECT userid FROM profiles WHERE login_name = :s"), {"s": s}).first()
        if row:
            users = [user_by_id(db, row[0])]
    if not users and len(s) >= 3 and viewer.is_logged_in:
        rows = db.execute(text(f"SELECT DISTINCT userid FROM profiles {join}WHERE (LOCATE(:s, login_name) > 0 OR "
                               f"LOCATE(:s, realname) > 0) {vis_clause}{tail}"), {"s": s})
        users = users_from_list(db, [r[0] for r in rows])
    return users


# ---------------------------------------------------------------- User.get
def _filter_users_by_group(ctx: Ctx, users: list[dict], group_ids, group_names) -> list[dict]:
    if not group_ids and not group_names:
        return users
    groups: dict[int, dict] = {}
    for gid in group_ids or []:
        if not re.fullmatch(r"\d+", str(gid)):
            raise param_must_be_numeric("Bugzilla::Group::new")
        group = group_by_id(ctx.db, gid)
        if not group:
            raise object_does_not_exist("group", gid, by_id=True)
        groups[group["id"]] = group
    for name in group_names or []:
        group = group_by_name(ctx.db, name)
        if not group or group["id"] not in ctx.user.group_ids:
            raise invalid_group_name(name)
        groups[group["id"]] = group
    out = []
    for u in users:
        if user_group_ids(ctx.db, u["id"]) & set(groups):
            out.append(u)
    return out


def _group_to_hash(g: dict) -> dict:
    return {"id": int(g["id"]), "name": g["name"], "description": g["description"] or ""}


def _saved(db: Session, table: str, user_col: str, user_id: int) -> list[dict]:
    rows = db.execute(text(f"SELECT id, name, query FROM {table} WHERE {user_col} = :u ORDER BY name"), {"u": user_id})
    return [{"id": r.id, "name": r.name, "query": r.query} for r in rows]


def get(ctx: Ctx, ids=None, names=None) -> dict:
    db, me = ctx.db, ctx.user
    ids = ids if ids is not None else array_param(ctx, "ids")
    names = names if names is not None else array_param(ctx, "names")
    matches = array_param(ctx, "match")
    group_ids, group_names = array_param(ctx, "group_ids"), array_param(ctx, "groups")
    if ids is None and names is None and matches is None:
        raise BugzillaError("params_required", "The function User.get requires that you set one of the following "
                            "parameters: ids, names, match")

    users: list[dict] = []
    seen: set[int] = set()

    def add(u):
        if u["id"] not in seen:
            seen.add(u["id"])
            users.append(u)

    for name in names or []:
        add(check_user_name(db, name))

    if not me.is_logged_in:
        if ids:
            raise BugzillaError("user_access_by_id_denied", 'Logged-out users cannot use the "ids" argument to this '
                                "function to access any user information.")
        if matches:
            raise BugzillaError("user_access_by_match_denied", 'Logged-out users cannot use the "match" argument to '
                                "this function to access any user information.")
        in_group = _filter_users_by_group(ctx, users, group_ids, group_names)
        return {"users": [ctx.filter.apply({"id": u["id"], "real_name": u["realname"], "name": u["login"]})
                          for u in in_group]}

    for u in users_from_list(db, ids or []):
        if not can_see_user(db, me, u["id"]):
            raise auth_failure("", "access", "user", reason="not_visible")
        add(u)

    limit = int(param("maxusermatches") or 0)
    raw_limit = ctx.params.get("limit")
    if raw_limit is not None and perl_true(raw_limit):
        if not re.fullmatch(r"\d+", str(raw_limit)) or int(raw_limit) == 0:
            raise param_must_be_numeric("Bugzilla::WebService::User::match", "limit")
        limit = min(int(raw_limit), limit) if limit else int(raw_limit)

    exclude_disabled = not perl_true(ctx.params.get("include_disabled"))
    for s in matches or []:
        for u in match(db, me, str(s), limit, exclude_disabled):
            add(u)

    editusers = me.in_group("editusers")
    suffix = param("emailsuffix") or ""
    out = []
    for u in _filter_users_by_group(ctx, users, group_ids, group_names):
        info = ctx.filter.apply({"id": u["id"], "real_name": u["realname"], "name": u["login"],
                                 "email": u["login"] + suffix, "can_login": bool(u["is_enabled"])})
        if editusers:
            info["email_enabled"] = not u["disable_mail"]
            info["login_denied_text"] = u["disabledtext"]
        if me.id == u["id"]:
            if ctx.filter.wants("saved_searches"):
                info["saved_searches"] = _saved(db, "namedqueries", "userid", u["id"])
            if ctx.filter.wants("saved_reports"):
                info["saved_reports"] = _saved(db, "reports", "user_id", u["id"])
        if ctx.filter.wants("groups"):
            groups = groups_of(db, u["id"])
            if me.id == u["id"] or editusers:
                info["groups"] = [_group_to_hash(g) for g in groups]
            else:
                blessable = bless_group_ids(db, me)
                info["groups"] = [_group_to_hash(g) for g in groups if g["id"] in blessable]
        out.append(info)
    return {"users": out}


def login(ctx: Ctx) -> dict:
    user = ctx.user
    if user.is_logged_in:
        token = f"{user.id}-{user.login_token}" if user.login_token else issue_login_token(ctx.db, user)
        return {"id": user.id, "token": token}
    for name in ("login", "password"):
        if ctx.params.get(name) is None and ctx.params.get("Bugzilla_" + name) is None:
            raise param_required(name)
    creds = {"Bugzilla_login": ctx.params.get("Bugzilla_login", ctx.params.get("login")),
             "Bugzilla_password": ctx.params.get("Bugzilla_password", ctx.params.get("password"))}
    user = authenticate(ctx.db, creds)
    restrict = str(ctx.params.get("restrict_login", "0")) in ("1", "true", "True")
    return {"id": user.id, "token": issue_login_token(ctx.db, user, ctx.params.get("_remote_ip"), restrict)}


def logout(ctx: Ctx) -> dict:
    logout_all(ctx.db, ctx.user)
    return {"result": None}


def valid_login(ctx: Ctx) -> dict:
    login_name = ctx.params.get("login")
    if login_name is None:
        raise param_required("login")
    user = ctx.user
    return {"result": bool(user.is_logged_in and user.login == str(login_name))}
