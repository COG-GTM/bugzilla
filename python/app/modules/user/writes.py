"""Write side of Bugzilla::WebService::User: create and update.

Ports Bugzilla::User->create / ->update with their validators (check_login_name, validate_password,
_check_realname, _check_disabledtext), group changes (_set_groups/_update_groups), audit_log,
profiles_activity, email_setting defaults, derive_regexp_groups and the forced logout.
"""
import base64
import hashlib
import re
import secrets
import string

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.errors import BugzillaError, param_required
from app.core.params import param
from app.core.rest import Ctx
from app.core.util import ALNUM
from app.modules.user.service import (
    GRANT_DIRECT,
    GRANT_REGEXP,
    GROUP_BLESS,
    GROUP_MEMBERSHIP,
    auth_failure,
    bless_group_ids,
    check_user_id,
    check_user_name,
    group_by_id,
    group_by_name,
    perl_true,
)

USER_PASSWORD_MIN_LENGTH = 6
# Bugzilla::BugMail::RELATIONSHIPS keys and the event constants (Bugzilla/Constants.pm)
RELATIONSHIPS = (0, 1, 2, 3, 5)  # assignee, qa, reporter, cc, global watcher
REL_REPORTER, REL_ANY = 2, 100
POS_EVENTS = tuple(range(12))
NEG_EVENTS = (50, 51)
EVT_CC, EVT_CHANGED_BY_ME = 8, 51
GLOBAL_EVENTS = (100, 101)
UPDATE_COLUMNS = ("login_name", "realname", "cryptpassword", "disabledtext", "disable_mail", "is_enabled", "extern_id")
# WebService field -> Bugzilla::User setter target column
API_TO_COLUMN = {"email": "login_name", "full_name": "realname", "login_denied_text": "disabledtext"}
COLUMN_TO_API = {v: k for k, v in API_TO_COLUMN.items()}
META_PARAMS = {"ids", "names", "include_fields", "exclude_fields", "_remote_ip", "callback", "token", "api_key",
               "Bugzilla_login", "Bugzilla_password", "Bugzilla_api_key", "Bugzilla_token", "Bugzilla_restrictlogin"}


# ---------------------------------------------------------------- helpers
def db_now(db: Session):
    return db.execute(text("SELECT LOCALTIMESTAMP(0)")).scalar()


def fieldid(db: Session, name: str) -> int:
    return db.execute(text("SELECT id FROM fielddefs WHERE name = :n"), {"n": name}).scalar()


def bz_crypt_hash(password: str) -> str:
    """Bugzilla::Util::bz_crypt without a salt: random 8-char salt, SHA-256 of password.salt, base64."""
    salt = "".join(secrets.choice(ALNUM) for _ in range(8))
    digest = base64.b64encode(hashlib.sha256((password + salt).encode("utf-8")).digest()).decode().rstrip("=")
    return f"{salt},{digest}{{SHA-256}}"


def check_email_syntax(addr: str):
    suffix = param("emailsuffix") or ""
    email = addr + suffix
    regexp = param("emailregexp") or ""
    local, _, domain = email.rpartition("@")
    valid = (re.search(regexp, addr) is not None and bool(local) and bool(domain) and email.isascii()
             and len(email) <= 127 and not re.search(r"[\s()<>\[\]\\,;:\"]", email) and "@" not in local)
    if not valid:
        raise BugzillaError("illegal_email_address",
                            f"The e-mail address you entered ({addr}) didn't pass our syntax checking for a legal "
                            f"email address. {param('emailregexpdesc') or ''} "
                            "It also must not contain any illegal characters.")


def validate_password(password: str):
    """Bugzilla::User::validate_password_check."""
    if len(password) < USER_PASSWORD_MIN_LENGTH:
        raise BugzillaError("password_too_short",
                            f"The password must be at least {USER_PASSWORD_MIN_LENGTH} characters long.")
    complexity = param("password_complexity") or "no_constraints"
    has = {"alpha": any(c.isalpha() for c in password), "lower": any(c.islower() for c in password),
           "upper": any(c.isupper() for c in password), "digit": re.search(r"\d", password) is not None,
           "punct": any(c in string.punctuation for c in password)}
    required = {"letters_numbers_specialchars": ("alpha", "digit", "punct"),
                "letters_numbers": ("lower", "upper", "digit"),
                "mixed_letters": ("lower", "upper")}.get(complexity, ())
    if not all(has[k] for k in required):
        raise BugzillaError("password_not_complex", "The password does not meet our security requirements.")


def is_available_username(db: Session, username: str, old_username: str | None = None) -> bool:
    """Bugzilla::User::is_available_username: not taken, and not reserved by a pending email change."""
    if db.execute(text("SELECT userid FROM profiles WHERE login_name = :n"), {"n": username}).first():
        return False
    row = db.execute(text(
        "SELECT tokentype, eventdata FROM tokens WHERE "
        "(tokentype = 'emailold' AND SUBSTRING(eventdata, 1, (POSITION(':' IN eventdata) - 1)) = :u) "
        "OR (tokentype = 'emailnew' AND SUBSTRING(eventdata, (POSITION(':' IN eventdata) + 1), LENGTH(eventdata)) = :u)"),
        {"u": username}).first()
    if not (row and row.eventdata):
        return True
    # the owner of the pending change may keep using both addresses
    return bool(old_username) and ((row.tokentype == "emailnew" and row.eventdata == f"{old_username}:{username}")
                                   or (row.tokentype == "emailold" and row.eventdata == f"{username}:{old_username}"))


def check_login_name(db: Session, login, current_login: str | None = None) -> str:
    """Bugzilla::User::check_login_name."""
    login = "" if login is None else str(login).strip()
    if not login:
        raise BugzillaError("user_login_required", "You must enter a login name for the new user.")
    check_email_syntax(login)
    if ((current_login is None or current_login.lower() != login.lower())
            and not is_available_username(db, login, current_login)):
        raise BugzillaError("account_exists", f"There is already an account with the login name {login}.")
    return login


def check_password(password) -> str:
    password = "" if password is None else str(password)
    if password == "*":
        return password
    validate_password(password)
    return bz_crypt_hash(password)


def sanitize_password(value) -> str | None:
    """Bugzilla::Object::_sanitize_audit_log: only the hash algorithm is ever exposed ('*' has none)."""
    if not value:
        return None
    algorithm = re.search(r"{([^}]+)}$", str(value))
    return f"hashed_with_{algorithm.group(1) if algorithm else 'unknown_algorithm'}"


def audit_log(db: Session, who: int, object_id: int, field: str, removed, added, when):
    if field == "cryptpassword":
        removed, added = None, sanitize_password(added)
    db.execute(text("INSERT INTO audit_log (user_id, class, object_id, field, removed, added, at_time) "
                    "VALUES (:u, 'Bugzilla::User', :o, :f, :r, :a, :t)"),
               {"u": who, "o": object_id, "f": field, "r": removed, "a": added, "t": when})


def derive_regexp_groups(db: Session, user_id: int, login: str):
    rows = db.execute(text("SELECT g.id, g.userregexp, ugm.group_id FROM groups g LEFT JOIN user_group_map ugm "
                           "ON g.id = ugm.group_id AND ugm.user_id = :u AND ugm.grant_type = :t"),
                      {"u": user_id, "t": GRANT_REGEXP}).all()
    for gid, regexp, present in rows:
        matches = False
        if regexp:
            try:
                matches = re.search(regexp, login, re.IGNORECASE) is not None
            except re.error:
                matches = False
        if matches and present is None:
            db.execute(text("INSERT INTO user_group_map (user_id, group_id, isbless, grant_type) VALUES (:u, :g, 0, :t)"),
                       {"u": user_id, "g": gid, "t": GRANT_REGEXP})
        elif not matches and present is not None:
            db.execute(text("DELETE FROM user_group_map WHERE user_id = :u AND group_id = :g AND isbless = 0 "
                            "AND grant_type = :t"), {"u": user_id, "g": gid, "t": GRANT_REGEXP})


def logout_user(db: Session, user_id: int):
    db.execute(text("DELETE FROM logincookies WHERE userid = :u"), {"u": user_id})


# ---------------------------------------------------------------- User.create
def create(ctx: Ctx) -> dict:
    db, me = ctx.db, ctx.user
    if not me.in_group("editusers"):
        raise auth_failure("editusers", "add", "users")
    email = ctx.params.get("email")
    email = "" if email is None else str(email).strip()
    if not email:
        raise param_required("email")
    realname = str(ctx.params.get("full_name") or "").strip()
    password = str(ctx.params.get("password") or "").strip() or "*"

    login = check_login_name(db, email)
    crypt = check_password(password)
    db.execute(text("INSERT INTO profiles (login_name, cryptpassword, realname, disabledtext, disable_mail, is_enabled) "
                    "VALUES (:l, :p, :r, '', 0, 1)"), {"l": login, "p": crypt, "r": realname})
    user_id = db.execute(text("SELECT LAST_INSERT_ID()")).scalar()
    now = db_now(db)
    audit_log(db, me.id, user_id, "__create__", None, login, now)

    for rel in RELATIONSHIPS:
        for event in POS_EVENTS + NEG_EVENTS:
            if event == EVT_CHANGED_BY_ME or (event == EVT_CC and rel != REL_REPORTER):
                continue
            db.execute(text("INSERT INTO email_setting (user_id, relationship, event) VALUES (:u, :r, :e)"),
                       {"u": user_id, "r": rel, "e": event})
    for event in GLOBAL_EVENTS:
        db.execute(text("INSERT INTO email_setting (user_id, relationship, event) VALUES (:u, :r, :e)"),
                   {"u": user_id, "r": REL_ANY, "e": event})

    derive_regexp_groups(db, user_id, login)
    db.execute(text("INSERT INTO profiles_activity (userid, who, profiles_when, fieldid, newvalue) "
                    "VALUES (:u, :w, :t, :f, :t)"),
               {"u": user_id, "w": me.id or user_id, "t": now, "f": fieldid(db, "creation_ts")})
    return {"id": user_id}


# ---------------------------------------------------------------- User.update
def _groups_to_objects(ctx: Ctx, changes: dict) -> dict:
    """Bugzilla::User::_set_groups_to_object."""
    if not isinstance(changes, dict):
        raise BugzillaError("param_invalid", f"{changes} is not a valid parameter for the groups function.")
    blessable = bless_group_ids(ctx.db, ctx.user)
    out = {}
    for key, values in changes.items():
        if not isinstance(values, list):
            raise BugzillaError("param_invalid", f"{values} is not a valid parameter for the {key} function.")
        groups = []
        for value in values:
            group = group_by_id(ctx.db, value) if re.fullmatch(r"\d+", str(value)) else group_by_name(ctx.db, value)
            if not group or group["id"] not in blessable:
                raise auth_failure(value, "edit", "users", reason="cant_bless")
            groups.append(group)
        out[key] = groups
    return out


def _set_groups(ctx: Ctx, user: dict, is_bless: int, changes: dict, pending: dict):
    """Bugzilla::User::_set_groups: compute (removed, added) group lists against GRANT_DIRECT rows."""
    changes = _groups_to_objects(ctx, changes)
    rows = ctx.db.execute(text("SELECT DISTINCT group_id FROM user_group_map WHERE user_id = :u AND isbless = :b "
                               "AND grant_type = :t"), {"u": user["id"], "b": is_bless, "t": GRANT_DIRECT})
    ids = [r[0] for r in rows]
    current = []
    if ids:
        current = [dict(r._mapping) for r in ctx.db.execute(text(
            f"SELECT id, name, description FROM groups WHERE id IN ({','.join(map(str, ids))}) ORDER BY isbuggroup, name"))]
    blessable = bless_group_ids(ctx.db, ctx.user)
    if "set" in changes:
        new = list(changes["set"])
        for g in current:
            if g["id"] not in blessable and all(n["id"] != g["id"] for n in new):
                new.append(g)
    else:
        new = list(current)
        for g in changes.get("remove") or []:
            new = [n for n in new if n["id"] != g["id"]]
        for g in changes.get("add") or []:
            if all(n["id"] != g["id"] for n in new):
                new.append(g)
    new_ids = {g["id"] for g in new}
    cur_ids = {g["id"] for g in current}
    removed = [g for g in current if g["id"] not in new_ids]
    added = [g for g in new if g["id"] not in cur_ids]
    if removed or added:
        pending[is_bless] = (removed, added)


def _update_groups(ctx: Ctx, user: dict, pending: dict, changes: dict, now):
    """Bugzilla::User::_update_groups."""
    for is_bless, (removed, added) in pending.items():
        for g in removed:
            ctx.db.execute(text("DELETE FROM user_group_map WHERE user_id = :u AND group_id = :g AND isbless = :b "
                                "AND grant_type = :t"), {"u": user["id"], "g": g["id"], "b": is_bless, "t": GRANT_DIRECT})
        for g in added:
            ctx.db.execute(text("INSERT INTO user_group_map (user_id, group_id, isbless, grant_type) "
                                "VALUES (:u, :g, :b, :t)"), {"u": user["id"], "g": g["id"], "b": is_bless, "t": GRANT_DIRECT})
        if not is_bless:
            ctx.db.execute(text("INSERT INTO profiles_activity (userid, who, profiles_when, fieldid, oldvalue, newvalue) "
                                "VALUES (:u, :w, :t, :f, :o, :n)"),
                           {"u": user["id"], "w": ctx.user.id, "t": now, "f": fieldid(ctx.db, "bug_group"),
                            "o": ", ".join(g["name"] for g in removed), "n": ", ".join(g["name"] for g in added)})
        changes["bless_groups" if is_bless else "groups"] = ([g["name"] for g in removed], [g["name"] for g in added])


def _update_one(ctx: Ctx, user: dict, fields: dict) -> dict:
    db = ctx.db
    new = dict(user)
    pending_groups: dict = {}
    for name, value in fields.items():
        if name == "email":
            new["login"] = check_login_name(db, value, current_login=user["login"])
        elif name == "full_name":
            new["realname"] = str(value or "").strip()
        elif name == "password":
            new["cryptpassword"] = check_password(value)
        elif name == "email_enabled":
            new["disable_mail"] = 0 if perl_true(value) else 1
        elif name == "login_denied_text":
            new["disabledtext"] = str(value or "").strip()
            new["is_enabled"] = 0 if perl_true(value) else 1
        elif name == "groups":
            _set_groups(ctx, user, GROUP_MEMBERSHIP, value, pending_groups)
        elif name == "bless_groups":
            if not ctx.user.in_group("editusers"):
                raise auth_failure("editusers", "edit", "users", reason="cant_bless")
            _set_groups(ctx, user, GROUP_BLESS, value, pending_groups)
        else:
            raise BugzillaError("unknown_method", f"The requested method 'Bugzilla::User::set_{name}' was not found.",
                                code=67399)

    col_of = {"login_name": "login", "realname": "realname", "cryptpassword": "cryptpassword",
              "disabledtext": "disabledtext", "disable_mail": "disable_mail", "is_enabled": "is_enabled",
              "extern_id": "extern_id"}
    changes: dict = {}
    for column in UPDATE_COLUMNS:
        old, cur = user[col_of[column]], new[col_of[column]]
        if (old if old is not None else "") != (cur if cur is not None else ""):
            changes[column] = (old, cur)

    now = db_now(db)
    if changes:
        sets = ", ".join(f"{c} = :{c}" for c in changes)
        db.execute(text(f"UPDATE profiles SET {sets} WHERE userid = :id"),
                   {**{c: v[1] for c, v in changes.items()}, "id": user["id"]})
        for column, (old, cur) in changes.items():
            audit_log(db, ctx.user.id, user["id"], column, old, cur, now)

    _update_groups(ctx, user, pending_groups, changes, now)

    if "login_name" in changes:
        db.execute(text("DELETE FROM tokens WHERE userid = :u"), {"u": user["id"]})
        derive_regexp_groups(db, user["id"], new["login"])
    if any(c in changes for c in ("login_name", "disabledtext", "cryptpassword")):
        logout_user(db, user["id"])

    out = {}
    for column, (old, cur) in changes.items():
        if isinstance(old, list):
            old, cur = ",".join(old), ",".join(cur)
        elif column == "cryptpassword":
            old, cur = "", sanitize_password(cur)
        out[COLUMN_TO_API.get(column, column)] = {"removed": "" if old is None else str(old),
                                                  "added": "" if cur is None else str(cur)}
    return {"id": user["id"], "changes": out}


def update(ctx: Ctx, ids=None, names=None) -> dict:
    db, me = ctx.db, ctx.user
    me.require_login()
    if not me.in_group("editusers"):
        raise auth_failure("editusers", "edit", "users")
    ids = ids if ids is not None else ctx.params.get("ids")
    names = names if names is not None else ctx.params.get("names")
    ids = [ids] if ids is not None and not isinstance(ids, list) else ids
    names = [names] if names is not None and not isinstance(names, list) else names
    users: list[dict] = []
    seen: set[int] = set()
    for i in ids or []:
        u = check_user_id(db, i)
        if u["id"] not in seen:
            seen.add(u["id"])
            users.append(u)
    for n in names or []:
        u = check_user_name(db, n)
        if u["id"] not in seen:
            seen.add(u["id"])
            users.append(u)
    if not users:
        raise BugzillaError("object_not_specified", "You must select/enter a user.")

    fields = {k: v for k, v in ctx.params.items() if k not in META_PARAMS}
    return {"users": [_update_one(ctx, u, fields) for u in users]}
