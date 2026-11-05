"""Group.get / Group.create / Group.update — port of Bugzilla/WebService/Group.pm plus the parts of
Bugzilla/Group.pm (validators, admin inheritance, regexp membership, audit log) and Bugzilla/User.pm
(bless / visibility helpers) that the WebService exercises."""

import re
import subprocess

from sqlalchemy import text

from app.core.errors import BugzillaError
from app.core.params import param
from app.core.rest import Ctx

GROUP_COLUMNS = "id, name, description, isbuggroup, userregexp, isactive, icon_url"
LIST_ORDER = "isbuggroup, name"
# Bugzilla::Object::UPDATE_COLUMNS order for Bugzilla::Group
UPDATE_COLUMNS = ("name", "description", "userregexp", "isactive", "icon_url")
# WebService parameter -> groups column (Bugzilla::Group::set_* methods)
SETTERS = {"name": "name", "description": "description", "user_regexp": "userregexp",
           "is_active": "isactive", "icon_url": "icon_url"}
MAPPED_RETURNS = {"userregexp": "user_regexp", "isactive": "is_active"}

GROUP_MEMBERSHIP, GROUP_BLESS, GROUP_VISIBLE = 0, 1, 2   # group_group_map.grant_type
GRANT_DIRECT, GRANT_REGEXP = 0, 2                        # user_group_map.grant_type
CONTROLMAPMANDATORY = 3

# Perl strips the credentials before the method sees the params; Python also adds _remote_ip.
NOT_FIELDS = {"ids", "names", "Bugzilla_login", "Bugzilla_password", "Bugzilla_token", "Bugzilla_api_key",
              "_remote_ip"}


# ------------------------------------------------------------------ helpers

def perl_true(value) -> bool:
    """Perl truthiness: undef, "", "0" and 0 are false; the string "false" is true."""
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value not in ("", "0")
    return bool(value)


def trim(value) -> str:
    return "" if value is None else re.sub(r"\s+$", "", re.sub(r"^\s+", "", str(value)))


def clean_text(value) -> str:
    return trim(re.sub(r"[\x00-\x1F\x7F]+", " ", str(value)))


def as_list(value):
    """Bugzilla::WebService::validate(): a scalar param becomes a one-element list."""
    if value is None or isinstance(value, list):
        return value
    return [value]


def in_sql(ids) -> str:
    return ",".join(str(int(i)) for i in ids)


def auth_failure(action: str) -> BugzillaError:
    # The template has no entry for object "group", hence the dangling " ." in the Perl message.
    verb = "add new" if action == "add" else "add, modify or delete"
    return BugzillaError("auth_failure", f"Sorry, you aren't a member of the 'creategroups' group, and so you are "
                                         f"not authorized to {verb} .")


def group_does_not_exist(value, by_id: bool) -> BugzillaError:
    what = f"with the id '{value}'" if by_id else f"named '{value}'"
    return BugzillaError("object_does_not_exist", f"There is no group {what}.")


def unknown_setter(key: str) -> BugzillaError:
    # Perl reports code -32601 as 100000 + code
    return BugzillaError("unknown_method", f"The requested method 'Bugzilla::Group::set_{key}' was not found.",
                         code=67399)


# ---------------------------------------------------------- Bugzilla::Group

def fetch_groups(ctx: Ctx, where: str, binds: dict | None = None) -> list[dict]:
    rows = ctx.db.execute(text(f"SELECT {GROUP_COLUMNS} FROM groups WHERE {where} ORDER BY {LIST_ORDER}"),
                          binds or {})
    return [dict(r._mapping) for r in rows]


def new_from_list(ctx: Ctx, ids: list) -> list[dict]:
    for i in ids:
        if not re.fullmatch(r"\d+", str(i)):
            raise BugzillaError("param_must_be_numeric", "Invalid parameter passed to "
                                "Bugzilla::Group::new_from_list: It must be numeric.")
    if not ids:
        return []
    return fetch_groups(ctx, f"id IN ({in_sql(ids)})")


def check_by_name(ctx: Ctx, name) -> dict:
    name = trim(name)
    if not name:
        raise BugzillaError("object_not_specified", "You must select/enter a group.")
    groups = fetch_groups(ctx, "name = :name", {"name": name})
    if not groups:
        raise group_does_not_exist(name, by_id=False)
    return groups[0]


def check_by_id(ctx: Ctx, gid) -> dict:
    groups = fetch_groups(ctx, "id = :id", {"id": int(gid)}) if re.fullmatch(r"\d+", str(gid)) else []
    if not groups:
        raise group_does_not_exist(gid, by_id=True)
    return groups[0]


def flatten_group_membership(ctx: Ctx, ids: list[int]) -> list[int]:
    """Bugzilla::Group::flatten_group_membership: the groups plus every group that is a member of them."""
    members: dict[int, set[int]] = {}
    for member, grantor in ctx.db.execute(text(
            "SELECT member_id, grantor_id FROM group_group_map WHERE grant_type = :t"), {"t": GROUP_MEMBERSHIP}):
        members.setdefault(grantor, set()).add(member)
    seen: list[int] = []
    todo = list(ids)
    while todo:
        g = todo.pop(0)
        if g in seen:
            continue
        seen.append(g)
        todo.extend(members.get(g, ()))
    return seen


# ----------------------------------------------------------- Bugzilla::User

def visible_groups_inherited(ctx: Ctx) -> list[int]:
    if not ctx.user.is_logged_in:
        return []
    if param("usevisibilitygroups"):
        if not ctx.user.group_ids:
            return []
        direct = [r[0] for r in ctx.db.execute(text(
            f"SELECT DISTINCT grantor_id FROM group_group_map WHERE member_id IN ({in_sql(ctx.user.group_ids)}) "
            f"AND grant_type = {GROUP_VISIBLE}"))]
    else:
        direct = [r[0] for r in ctx.db.execute(text("SELECT id FROM groups"))]
    return flatten_group_membership(ctx, direct)


def bless_groups(ctx: Ctx) -> list[dict]:
    user = ctx.user
    if not user.is_logged_in:
        return []
    if user.in_group("editusers"):
        return fetch_groups(ctx, "1=1")
    visible = None
    if param("usevisibilitygroups"):
        visible = visible_groups_inherited(ctx)
        if not visible:
            return []
    vis_sql = f" AND {{col}} IN ({in_sql(visible)})" if visible else ""
    query = "SELECT DISTINCT group_id FROM user_group_map WHERE user_id = :uid AND isbless = 1" + \
            vis_sql.format(col="group_id")
    if user.group_ids:
        query += f" UNION SELECT DISTINCT grantor_id FROM group_group_map WHERE grant_type = {GROUP_BLESS} " \
                 f"AND member_id IN ({in_sql(user.group_ids)})" + vis_sql.format(col="grantor_id")
    ids = [r[0] for r in ctx.db.execute(text(query), {"uid": user.id})]
    return fetch_groups(ctx, f"id IN ({in_sql(ids)})") if ids else []


def can_bless(ctx: Ctx, gid: int) -> bool:
    return any(g["id"] == gid for g in bless_groups(ctx))


# ------------------------------------------------------------ validators

def check_name(ctx: Ctx, name, current: dict | None = None) -> str:
    name = trim(name)
    if not name:
        raise BugzillaError("empty_group_name", "You must enter a name for the group.")
    renamed = current is None or current["name"].lower() != name.lower()
    if renamed and fetch_groups(ctx, "name = :name", {"name": name}):
        raise BugzillaError("group_exists", f"The group {name} already exists.")
    return name


def check_description(desc) -> str:
    desc = trim(desc)
    if not desc:
        raise BugzillaError("empty_group_description", "You must enter a description for the group.")
    return desc


def check_user_regexp(regexp) -> str:
    regexp = trim(regexp)
    if not perl_true(regexp):
        regexp = ""
    if regexp != "" and perl_regexp(regexp, []) is None:
        raise BugzillaError("invalid_regexp", "The regular expression you entered is invalid.")
    return regexp


PERL_REGEXP_SCRIPT = r"""
use strict; use warnings;
binmode STDIN; local $/;
my ($re, @logins) = split /\0/, <STDIN>, -1;
utf8::decode($re);
my $qr = eval { qr/$re/i };
if (!defined $qr) { print "INVALID\n"; exit 0 }
my @matched; my $i = 0;
for my $login (@logins) { utf8::decode($login); push @matched, $i if $login =~ $qr; $i++ }
print "OK @matched\n";
"""


def perl_regexp(regexp: str, logins: list[str]) -> list[int] | None:
    """Compile *regexp* with Perl itself — the stored pattern is consumed by the Perl app's qr// and
    m//i (Group::_check_user_regexp, Group::_rederive_regexp, User::derive_regexp_groups), and no
    Python/PCRE engine agrees with Perl's dialect in both directions. Returns the indexes of the
    matching logins, or None when Perl cannot compile the pattern. Only perl-base is needed
    (no modules), which the python:*-slim image ships."""
    payload = "\0".join([regexp, *logins]).encode()
    try:
        proc = subprocess.run(["perl", "-e", PERL_REGEXP_SCRIPT], input=payload, capture_output=True,
                              timeout=10, check=True)
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError):
        return None
    out = proc.stdout.decode().split()
    return [int(i) for i in out[1:]] if out and out[0] == "OK" else None


def check_icon_url(url):
    return clean_text(url) if perl_true(url) else None


# ------------------------------------------------------------ side effects

def audit_log(ctx: Ctx, gid: int, field: str, removed, added):
    ctx.db.execute(text("INSERT INTO audit_log (user_id, class, object_id, field, removed, added, at_time) "
                        "VALUES (:uid, 'Bugzilla::Group', :oid, :field, :removed, :added, NOW())"),
                   {"uid": ctx.user.id, "oid": gid, "field": field, "removed": removed, "added": added})


def rederive_regexp(ctx: Ctx, gid: int, regexp: str):
    """Bugzilla::Group::_rederive_regexp: sync the GRANT_REGEXP rows of user_group_map with the regexp."""
    rows = ctx.db.execute(text(
        "SELECT userid, login_name, group_id FROM profiles LEFT JOIN user_group_map "
        "ON user_group_map.user_id = profiles.userid AND group_id = :gid AND grant_type = :gt AND isbless = 0"),
        {"gid": gid, "gt": GRANT_REGEXP}).all()
    # Perl: ($regexp ne '') && ($login =~ m/$regexp/i)
    matched = perl_regexp(regexp, [login for _, login, _ in rows]) if regexp != "" else []
    if matched is None:
        raise BugzillaError("invalid_regexp", "The regular expression you entered is invalid.")
    matched = set(matched)
    for i, (uid, _login, present) in enumerate(rows):
        matches = i in matched
        if matches and present is None:
            ctx.db.execute(text("INSERT INTO user_group_map (user_id, group_id, grant_type, isbless) "
                                "VALUES (:uid, :gid, :gt, 0)"), {"uid": uid, "gid": gid, "gt": GRANT_REGEXP})
        elif not matches and present is not None:
            ctx.db.execute(text("DELETE FROM user_group_map WHERE user_id = :uid AND group_id = :gid "
                                "AND grant_type = :gt AND isbless = 0"), {"uid": uid, "gid": gid, "gt": GRANT_REGEXP})


def enforce_mandatory(ctx: Ctx, gid: int):
    """Bugzilla::Group::_enforce_mandatory: bugs in products where the group is Mandatory must carry it."""
    bug_ids = [r[0] for r in ctx.db.execute(text(
        "SELECT bugs.bug_id FROM bugs INNER JOIN group_control_map ON group_control_map.product_id = bugs.product_id "
        "LEFT JOIN bug_group_map ON bug_group_map.bug_id = bugs.bug_id "
        "AND bug_group_map.group_id = group_control_map.group_id "
        "WHERE group_control_map.group_id = :gid AND group_control_map.membercontrol = :mc "
        "AND bug_group_map.group_id IS NULL"), {"gid": gid, "mc": CONTROLMAPMANDATORY})]
    for bug_id in bug_ids:
        ctx.db.execute(text("INSERT INTO bug_group_map (bug_id, group_id) VALUES (:b, :g)"), {"b": bug_id, "g": gid})


# ------------------------------------------------------------------- Group.get

def get(ctx: Ctx) -> dict:
    ctx.user.require_login()
    user = ctx.user
    all_groups = user.in_group("editusers") or user.in_group("creategroups")
    if not all_groups and not bless_groups(ctx):
        raise BugzillaError("group_cannot_view", "In order to view groups, you need to be a member of the "
                            "'editusers' or 'creategroups' group, or have bless privileges to at least one group.")

    ids = as_list(ctx.params.get("ids"))
    names = as_list(ctx.params.get("names"))
    groups: list[dict] = []
    if ids is not None:
        groups = new_from_list(ctx, ids)
    if names is not None:
        for name in names:
            if any(g["name"] == name for g in groups):
                continue
            groups.append(check_by_name(ctx, name))
    if ids is None and names is None:
        groups = fetch_groups(ctx, "1=1") if all_groups else bless_groups(ctx)

    return {"groups": [group_to_hash(ctx, g) for g in groups]}


def group_to_hash(ctx: Ctx, group: dict) -> dict:
    data = {"id": int(group["id"]), "name": group["name"], "description": group["description"]}
    if ctx.user.in_group("creategroups"):
        data["is_active"] = bool(group["isactive"])
        data["is_bug_group"] = bool(group["isbuggroup"])
        data["user_regexp"] = group["userregexp"] or ""
    if perl_true(ctx.params.get("membership")):
        data["membership"] = group_membership(ctx, group)
    return data


def group_membership(ctx: Ctx, group: dict) -> list[dict]:
    user = ctx.user
    editusers = user.in_group("editusers")
    where = None
    if not editusers and param("usevisibilitygroups"):
        visible = visible_groups_inherited(ctx)
        if visible:
            where = f"ugm.isbless = 0 AND ugm.group_id IN ({in_sql(visible)})"
    elif editusers or can_bless(ctx, group["id"]) or user.in_group("creategroups"):
        where = "ugm.isbless = 0"
    if where is None:
        raise BugzillaError("group_not_visible", f"You are not allowed to see members of the {group['name']} group.")

    grouplist = flatten_group_membership(ctx, [group["id"]])
    rows = ctx.db.execute(text(
        "SELECT DISTINCT p.userid, p.login_name, p.realname, p.disabledtext, p.disable_mail, p.is_enabled "
        f"FROM profiles p, user_group_map ugm WHERE ugm.user_id = p.userid AND {where} "
        f"AND ugm.group_id IN ({in_sql(grouplist)}) ORDER BY p.login_name")).all()
    suffix = param("emailsuffix") or ""
    return [{
        "id": int(r.userid),
        "real_name": r.realname or "",
        "name": r.login_name,
        "email": r.login_name + suffix,
        "can_login": bool(r.is_enabled),
        "email_enabled": not bool(r.disable_mail),
        "login_denied_text": r.disabledtext or "",
    } for r in rows]


# ---------------------------------------------------------------- Group.create

def create(ctx: Ctx) -> dict:
    ctx.user.require_login()
    if not ctx.user.in_group("creategroups"):
        raise auth_failure("add")
    p = ctx.params
    # Bugzilla::Object::run_create_validators runs the validators in column order
    description = check_description(p.get("description"))
    icon_url = check_icon_url(p.get("icon_url"))
    isactive = 1 if perl_true(p.get("is_active")) else 0
    isbuggroup = 1
    name = check_name(ctx, p.get("name"))
    userregexp = check_user_regexp(p.get("user_regexp"))

    result = ctx.db.execute(text(
        "INSERT INTO groups (name, description, isbuggroup, userregexp, isactive, icon_url) "
        "VALUES (:name, :description, :isbuggroup, :userregexp, :isactive, :icon_url)"),
        {"name": name, "description": description, "isbuggroup": isbuggroup, "userregexp": userregexp,
         "isactive": isactive, "icon_url": icon_url})
    gid = int(result.lastrowid)
    audit_log(ctx, gid, "__create__", None, name)

    # the admin group is automatically a member of, can bless and can see every new group
    admin = ctx.db.execute(text("SELECT id FROM groups WHERE name = 'admin'")).first()
    if admin:
        for grant_type in (GROUP_MEMBERSHIP, GROUP_BLESS, GROUP_VISIBLE):
            ctx.db.execute(text("INSERT INTO group_group_map (member_id, grantor_id, grant_type) "
                                "VALUES (:m, :g, :t)"), {"m": admin.id, "g": gid, "t": grant_type})
    if userregexp:
        rederive_regexp(ctx, gid, userregexp)
    return {"id": gid}


# ---------------------------------------------------------------- Group.update

def update(ctx: Ctx) -> dict:
    ctx.user.require_login()
    if not ctx.user.in_group("creategroups"):
        raise auth_failure("edit")
    p = ctx.params
    ids, names = as_list(p.get("ids")), as_list(p.get("names"))
    if ids is None and names is None:
        raise BugzillaError("params_required", "The function Group.update requires that you set one of the "
                                               "following parameters: ids, names")

    # Bugzilla::WebService::Util::params_to_objects: names first, then ids, deduplicated by id
    groups: list[dict] = []
    for name in names or []:
        groups.append(check_by_name(ctx, name))
    for gid in ids or []:
        groups.append(check_by_id(ctx, gid))
    seen: set[int] = set()
    groups = [g for g in groups if not (g["id"] in seen or seen.add(g["id"]))]

    values = {k: v for k, v in p.items() if k not in NOT_FIELDS}
    new_values = []
    for group in groups:
        new_values.append(set_all(ctx, group, values))

    result = []
    for group, new in zip(groups, new_values):
        changes = apply_update(ctx, group, new)
        result.append({"id": int(group["id"]), "changes": {
            MAPPED_RETURNS.get(col, col): {"removed": old, "added": added} for col, (old, added) in changes.items()
        }})
    return {"groups": result}


def set_all(ctx: Ctx, group: dict, values: dict) -> dict:
    """Bugzilla::Object::set_all: call set_<key> for every key in sorted order."""
    new = dict(group)
    for key in sorted(values):
        if key not in SETTERS:
            raise unknown_setter(key)
        value = values[key]
        if key == "name":
            new["name"] = check_name(ctx, value, current=group)
        elif key == "description":
            new["description"] = check_description(value)
        elif key == "user_regexp":
            new["userregexp"] = check_user_regexp(value)
        elif key == "is_active":
            new["isactive"] = 1 if perl_true(value) else 0
        elif key == "icon_url":
            new["icon_url"] = check_icon_url(value)
    return new


def apply_update(ctx: Ctx, old: dict, new: dict) -> dict:
    """Bugzilla::Object::update + Bugzilla::Group::update side effects; returns {column: (old, new)}."""
    changes = {}
    for col in UPDATE_COLUMNS:
        o, n = old[col], new[col]
        o_s = None if o is None else str(o)
        n_s = None if n is None else str(n)
        if o_s == n_s:
            continue
        changes[col] = (o_s, n_s)
    if changes:
        sets = ", ".join(f"{col} = :{col}" for col in changes)
        ctx.db.execute(text(f"UPDATE groups SET {sets} WHERE id = :id"),
                       {**{col: new[col] for col in changes}, "id": old["id"]})
        for col, (o_s, n_s) in changes.items():
            audit_log(ctx, old["id"], col, o_s, n_s)
    if "isactive" in changes and changes["isactive"][1] == "1":
        enforce_mandatory(ctx, old["id"])
    if "userregexp" in changes:
        rederive_regexp(ctx, old["id"], new["userregexp"])
    return changes
