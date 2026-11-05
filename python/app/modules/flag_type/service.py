"""Port of Bugzilla::WebService::FlagType + the parts of Bugzilla::FlagType it drives.

Perl is the contract: every quirk below (string truthiness, alphabetical validator order,
hash-shaped inclusions being dropped, ...) was observed against the running Perl app.
"""
import re

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.auth import User
from app.core.errors import BugzillaError, param_required, params_required
from app.core.rest import Ctx
from app.modules.bug.service import object_does_not_exist, object_not_specified

MAX_SMALLINT = 32767
MAX_INT_32 = 2147483647
CLASS = "Bugzilla::FlagType"
UPDATE_COLUMNS = ["name", "description", "cc_list", "sortkey", "is_active", "is_requestable", "is_requesteeble",
                  "is_multiplicable", "grant_group_id", "request_group_id"]
# WebService param -> DB column (Bugzilla::FlagType::set_* methods)
SETTERS = {"name": "name", "description": "description", "cc_list": "cc_list", "sortkey": "sortkey",
           "is_active": "is_active", "is_requestable": "is_requestable",
           "is_specifically_requestable": "is_requesteeble", "is_multiplicable": "is_multiplicable",
           "grant_group": "grant_group_id", "request_group": "request_group_id"}
EMAIL_RE = re.compile(r"^[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+(\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*"
                      r"@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*$")


# ---------------------------------------------------------------- perl-isms
def perl_str(value) -> str:
    if value is None:
        return ""
    if value is True:
        return "1"
    if value is False:
        return "0"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, list):
        return "ARRAY"
    if isinstance(value, dict):
        return "HASH"
    return str(value)


def perl_true(value) -> bool:
    if isinstance(value, (list, dict)):
        return True
    return perl_str(value) not in ("", "0")


def trim(value) -> str:
    return re.sub(r"\s+$", "", re.sub(r"^\s+", "", perl_str(value)))


def squash(message: str) -> str:
    """Template whitespace collapses in WebService error messages (e.g. 'The name  must' -> 'The name must')."""
    return re.sub(r"\s+", " ", message).strip()


def user_error(name: str, message: str, code: int | None = None) -> BugzillaError:
    return BugzillaError(name, squash(message), code)


def auth_failure(action: str) -> BugzillaError:
    what = "add new flag types" if action == "add" else "add, modify or delete flag types"
    return BugzillaError("auth_failure", "Sorry, you aren't a member of the 'editcomponents' group, and so you "
                         f"are not authorized to {what}.")


def product_access_denied(name) -> BugzillaError:
    return BugzillaError("product_access_denied",
                         f"Either the product '{perl_str(name)}' does not exist or you don't have access to it.")


# ---------------------------------------------------------------- lookups
def products_by_permission(db: Session, user: User) -> list[int]:
    """Bugzilla::User::get_products_by_permission('editcomponents') restricted to selectable products."""
    if not user.group_ids:
        return []
    gids = ",".join(str(g) for g in user.group_ids)
    ids = [r[0] for r in db.execute(text(
        f"SELECT DISTINCT product_id FROM group_control_map WHERE editcomponents != 0 AND group_id IN ({gids})"))]
    if not ids:
        return []
    selectable = {r[0] for r in db.execute(text(
        "SELECT products.id FROM products LEFT JOIN group_control_map ON group_control_map.product_id = products.id "
        f"AND group_control_map.membercontrol = 3 AND group_id NOT IN ({gids}) WHERE group_id IS NULL"))}
    return [i for i in ids if i in selectable]


def can_admin_flagtypes(ctx: Ctx) -> bool:
    return ctx.user.in_group("editcomponents") or bool(products_by_permission(ctx.db, ctx.user))


def can_access_product(ctx: Ctx, product_id: int) -> bool:
    """Bugzilla::User::can_access_product = can_see_product || can_enter_product."""
    rows = ctx.db.execute(text("SELECT group_id, membercontrol, entry FROM group_control_map WHERE product_id = :p"),
                          {"p": product_id}).all()
    see = {r[0] for r in rows if r[1] == 3}
    enter = {r[0] for r in rows if r[2]}
    return see <= ctx.user.group_ids or enter <= ctx.user.group_ids


def check_product(ctx: Ctx, name) -> dict:
    name = trim(name)
    row = ctx.db.execute(text("SELECT id, name FROM products WHERE name = :n"), {"n": name}).mappings().first()
    if not row or not can_access_product(ctx, row["id"]):
        raise product_access_denied(name)
    return row


def check_component(db: Session, product: dict, name) -> dict:
    name = trim(name)
    if name == "":
        raise object_not_specified("component")
    row = db.execute(text("SELECT id, name FROM components WHERE product_id = :p AND name = :n"),
                     {"p": product["id"], "n": name}).mappings().first()
    if not row:
        raise object_does_not_exist("component", name, product=product["name"])
    return row


def check_group_id(db: Session, name) -> int | None:
    if perl_str(name) == "":
        return None
    name = trim(name)
    if name == "":
        raise object_not_specified("group")
    row = db.execute(text("SELECT id FROM groups WHERE name = :n"), {"n": name}).first()
    if not row:
        raise object_does_not_exist("group", name)
    return row[0]


def check_flagtype(db: Session, ft_id) -> dict:
    """Bugzilla::FlagType->check({id => ...})."""
    raw = trim(ft_id)
    if raw == "":
        raise object_not_specified("flagtype")
    if not re.fullmatch(r"\d+", raw):
        raise BugzillaError("param_must_be_numeric", "Invalid parameter passed to Bugzilla::FlagType::_load_from_db: "
                            "It must be numeric.")
    row = None
    if int(raw) <= MAX_INT_32:
        row = db.execute(text("SELECT * FROM flagtypes WHERE id = :id"), {"id": int(raw)}).mappings().first()
    if not row:
        raise object_does_not_exist("flagtype", raw, by_id=True)
    return dict(row)


# ---------------------------------------------------------------- validators (Bugzilla::FlagType::_check_*)
def check_name(value) -> str:
    name = trim(value)
    if not name or re.search(r"[\s,]", name) or len(name) > 50:
        raise user_error("flag_type_name_invalid", f"The name {name} must be 1-50 characters long and must not "
                         "contain any spaces or commas.")
    return name


def check_description(value) -> str:
    desc = trim(value)
    if not desc:
        raise BugzillaError("flag_type_description_invalid", "You must enter a description for this flag type.")
    return desc


def check_cc_list(value) -> str:
    cc_list = perl_str(value)
    if len(cc_list) > 200:
        raise user_error("flag_type_cc_list_invalid", f"The CC list {cc_list} must be less than 200 characters long.")
    addresses = re.split(r"[,\s]+", cc_list)
    while addresses and addresses[-1] == "":  # perl split drops trailing empty fields
        addresses.pop()
    for address in addresses:
        if not address.isascii() or not EMAIL_RE.match(address):
            raise BugzillaError("illegal_email_address", f"The e-mail address you entered ({address}) didn't pass "
                                "our syntax checking for a legal email address. A legal address must contain exactly "
                                "one '@', and at least one '.' after the @. It also must not contain any illegal "
                                "characters.")
    return cc_list


def check_target_type(value) -> str:
    target_type = perl_str(value)
    if target_type not in ("bug", "attachment"):
        raise BugzillaError("flag_type_target_type_invalid",
                            f"The target type was neither bug nor attachment but rather {target_type}.", code=68000)
    return target_type


def check_sortkey(value) -> int:
    raw = perl_str(value)
    natural = re.fullmatch(r"\d+", raw)
    if not natural or int(raw) > MAX_SMALLINT:
        shown = raw if natural else ""
        raise user_error("flag_type_sortkey_invalid",
                         f"The sort key {shown} must be an integer between 0 and 32767.")
    return int(raw)


def check_boolean(value) -> int:
    return 1 if perl_true(value) else 0


def process_lists(ctx: Ctx, items) -> list[tuple[int, int]]:
    """Bugzilla::WebService::FlagType::_process_lists -> [(product_id, component_id)].

    Observed on the Perl app: hash-shaped ``{product: [components]}`` items are dropped silently.
    """
    allowed = None if ctx.user.in_group("editcomponents") else set(products_by_permission(ctx.db, ctx.user))
    out = []
    for item in items:
        if isinstance(item, dict):
            continue
        if isinstance(item, list):
            raise BugzillaError("param_invalid", f"ARRAY is not a valid parameter for the function.")
        product = check_product(ctx, item)
        if allowed is not None and product["id"] not in allowed:
            raise product_access_denied(item)
        out.append((product["id"], 0))
    return out


# ---------------------------------------------------------------- domain helpers
def clusions(db: Session, ft_id: int, kind: str) -> dict[str, tuple[int, int]]:
    """Bugzilla::FlagType::get_clusions: {'Product:Component': (product_id, component_id)}."""
    rows = db.execute(text(
        f"SELECT products.id, products.name, components.id, components.name FROM flagtypes "
        f"INNER JOIN flag{kind}clusions ON flag{kind}clusions.type_id = flagtypes.id "
        f"LEFT JOIN products ON flag{kind}clusions.product_id = products.id "
        f"LEFT JOIN components ON flag{kind}clusions.component_id = components.id WHERE flagtypes.id = :id"),
        {"id": ft_id}).all()
    result = {}
    for pid, pname, cid, cname in rows:
        result[f"{pname or '__Any__'}:{cname or '__Any__'}"] = (pid or 0, cid or 0)
    return result


def write_clusions(db: Session, ft_id: int, kind: str, pairs: list[tuple[int, int]]):
    db.execute(text(f"DELETE FROM flag{kind}clusions WHERE type_id = :id"), {"id": ft_id})
    seen = set()
    for pid, cid in pairs:
        if (pid, cid) in seen:
            continue
        seen.add((pid, cid))
        db.execute(text(f"INSERT INTO flag{kind}clusions (type_id, product_id, component_id) VALUES (:t, :p, :c)"),
                   {"t": ft_id, "p": pid or None, "c": cid or None})


def audit(db: Session, user_id: int, ft_id: int, field: str, removed, added):
    db.execute(text("INSERT INTO audit_log (user_id, class, object_id, field, removed, added, at_time) "
                    "VALUES (:u, :c, :o, :f, :r, :a, NOW())"),
               {"u": user_id, "c": CLASS, "o": ft_id, "f": field, "r": removed, "a": added})


def after_update(db: Session, row: dict):
    """Tail of Bugzilla::FlagType::update: drop flags that no longer fit, clear stale requestees."""
    stale = [r[0] for r in db.execute(text(
        "SELECT DISTINCT flags.id FROM flags INNER JOIN bugs ON flags.bug_id = bugs.bug_id "
        "LEFT JOIN flaginclusions AS i ON (flags.type_id = i.type_id "
        "AND (bugs.product_id = i.product_id OR i.product_id IS NULL) "
        "AND (bugs.component_id = i.component_id OR i.component_id IS NULL)) "
        "WHERE flags.type_id = :t AND i.type_id IS NULL"), {"t": row["id"]})]
    stale += [r[0] for r in db.execute(text(
        "SELECT DISTINCT flags.id FROM flags INNER JOIN bugs ON flags.bug_id = bugs.bug_id "
        "INNER JOIN flagexclusions AS e ON flags.type_id = e.type_id WHERE flags.type_id = :t "
        "AND (bugs.product_id = e.product_id OR e.product_id IS NULL) "
        "AND (bugs.component_id = e.component_id OR e.component_id IS NULL)"), {"t": row["id"]})]
    for flag_id in set(stale):
        retarget_flag(db, flag_id, row)
    if not row["is_requesteeble"]:
        db.execute(text("UPDATE flags SET requestee_id = NULL WHERE type_id = :t AND requestee_id IS NOT NULL"),
                   {"t": row["id"]})


def retarget_flag(db: Session, flag_id: int, row: dict):
    """Bugzilla::Flag::force_retarget: move the flag to a same-named type valid for its bug, else delete it."""
    flag = db.execute(text("SELECT flags.id, flags.bug_id, bugs.product_id, bugs.component_id FROM flags "
                           "INNER JOIN bugs ON bugs.bug_id = flags.bug_id WHERE flags.id = :f"), {"f": flag_id}).first()
    if not flag:
        return
    target = db.execute(text(
        "SELECT flagtypes.id FROM flagtypes INNER JOIN flaginclusions AS i ON flagtypes.id = i.type_id "
        "AND (i.product_id = :p OR i.product_id IS NULL) AND (i.component_id = :c OR i.component_id IS NULL) "
        "LEFT JOIN flagexclusions AS e ON (flagtypes.id = e.type_id AND (e.component_id = :c OR e.component_id IS NULL) "
        "AND ((e.product_id = :p) OR e.product_id IS NULL)) "
        "WHERE flagtypes.name = :n AND flagtypes.target_type = :tt AND flagtypes.is_active = 1 AND e.type_id IS NULL "
        "AND flagtypes.id != :self ORDER BY flagtypes.sortkey, flagtypes.name LIMIT 1"),
        {"p": flag.product_id, "c": flag.component_id, "n": row["name"], "tt": row["target_type"],
         "self": row["id"]}).first()
    if target:
        db.execute(text("UPDATE flags SET type_id = :t WHERE id = :f"), {"t": target[0], "f": flag_id})
    else:
        db.execute(text("DELETE FROM flags WHERE id = :f"), {"f": flag_id})


# ---------------------------------------------------------------- FlagType.get
def can_set_flag(user: User, row) -> bool:
    return not row["grant_group_id"] or row["grant_group_id"] in user.group_ids


def can_request_flag(user: User, row) -> bool:
    return can_set_flag(user, row) or not row["request_group_id"] or row["request_group_id"] in user.group_ids


def clusion_names(db: Session, pairs: dict, product_id: int) -> list[str]:
    result = []
    for pid, cid in pairs.values():
        if pid == 0 or pid == product_id:
            if not cid:
                return [""]
            name = db.execute(text("SELECT name FROM components WHERE id = :c"), {"c": cid}).scalar()
            result.append(name)
    return result


def flagtype_hash(ctx: Ctx, row, product: dict | None) -> dict:
    values = ["X"]
    if row["is_requestable"] and can_request_flag(ctx.user, row):
        values.append("?")
    if can_set_flag(ctx.user, row):
        values += ["+", "-"]
    item = {"id": row["id"], "name": row["name"], "description": row["description"],
            "type": "bug" if row["target_type"] == "b" else "attachment", "values": values,
            "is_active": bool(row["is_active"]), "is_requesteeble": bool(row["is_requesteeble"]),
            "is_multiplicable": bool(row["is_multiplicable"])}
    if product:
        inclusions = clusion_names(ctx.db, clusions(ctx.db, row["id"], "in"), product["id"])
        exclusions = clusion_names(ctx.db, clusions(ctx.db, row["id"], "ex"), product["id"])
        if inclusions and exclusions:
            exclusions = []
        if inclusions and inclusions[0] != "":
            item["inclusions"] = inclusions
        if exclusions and exclusions[0] != "":
            item["exclusions"] = exclusions
    return item


def get(ctx: Ctx) -> dict:
    if ctx.params.get("product") is None:
        raise param_required("product", "Bug.flag_types")
    product = check_product(ctx, ctx.params["product"])
    component = None
    if perl_true(ctx.params.get("component")):
        component = check_component(ctx.db, product, ctx.params["component"])

    binds = {"p": product["id"]}
    inc = "(i.product_id = :p OR i.product_id IS NULL)"
    exc = "flagtypes.id = e.type_id "
    if component:
        binds["c"] = component["id"]
        inc += " AND (i.component_id = :c OR i.component_id IS NULL)"
        exc += "AND (e.component_id = :c OR e.component_id IS NULL) AND ((e.product_id = :p) OR e.product_id IS NULL)"
    else:
        # verbatim from Bugzilla::FlagType::sqlify_criteria, operator precedence included
        exc += "AND ((e.product_id = :p AND e.component_id IS NULL OR (i.component_id = e.component_id)) " \
               "OR e.product_id IS NULL)"
    rows = ctx.db.execute(text(
        "SELECT * FROM flagtypes WHERE id IN (SELECT flagtypes.id FROM flagtypes "
        f"INNER JOIN flaginclusions AS i ON flagtypes.id = i.type_id LEFT JOIN flagexclusions AS e ON ({exc}) "
        f"WHERE 1=1 AND {inc} AND e.type_id IS NULL) ORDER BY sortkey, name"), binds).mappings().all()

    result = {"bug": [], "attachment": []}
    for row in rows:
        result["bug" if row["target_type"] == "b" else "attachment"].append(flagtype_hash(ctx, row, product))
    return result


# ---------------------------------------------------------------- FlagType.create
def validate(db: Session, field: str, value):
    if field == "name":
        return check_name(value)
    if field == "description":
        return check_description(value)
    if field == "cc_list":
        return check_cc_list(value)
    if field == "sortkey":
        return check_sortkey(value)
    if field == "target_type":
        return check_target_type(value)
    if field in ("grant_group", "request_group"):
        return check_group_id(db, value)
    if field.startswith("is_"):
        return check_boolean(value)
    return value


def create(ctx: Ctx) -> dict:
    ctx.user.require_login()
    if not can_admin_flagtypes(ctx):
        raise auth_failure("add")
    params = ctx.params
    if not perl_true(params.get("name")):
        raise param_required("name")
    if not perl_true(params.get("description")):
        raise param_required("description")

    args = {"sortkey": 1, "name": None, "inclusions": [(0, 0)], "cc_list": "", "description": None,
            "is_requestable": "on", "exclusions": [], "is_multiplicable": "on", "request_group": "",
            "is_active": "on", "is_specifically_requestable": "on", "target_type": "bug", "grant_group": ""}
    for key in list(args):
        if params.get(key) is not None:
            args[key] = params[key]
    args["name"] = trim(params["name"])
    args["description"] = trim(params["description"])
    args["is_requesteeble"] = args.pop("is_specifically_requestable")
    for key in ("is_requestable", "is_multiplicable", "is_active", "is_requesteeble"):
        if args[key] is not None and perl_str(args[key]) == "0":
            args[key] = None
    if params.get("inclusions") is not None:
        args["inclusions"] = process_lists(ctx, params["inclusions"])
    if params.get("exclusions") is not None:
        args["exclusions"] = process_lists(ctx, params["exclusions"])

    values = {}
    for field in sorted(args):  # Bugzilla::Object::run_create_validators sorts the fields
        values[field] = validate(ctx.db, field, args[field])

    db = ctx.db
    result = db.execute(text(
        "INSERT INTO flagtypes (name, description, cc_list, target_type, sortkey, is_active, is_requestable, "
        "is_requesteeble, is_multiplicable, grant_group_id, request_group_id) VALUES (:name, :description, :cc_list, "
        ":target_type, :sortkey, :is_active, :is_requestable, :is_requesteeble, :is_multiplicable, :grant_group, "
        ":request_group)"), {**{k: v for k, v in values.items() if k not in ("inclusions", "exclusions")},
                             "target_type": values["target_type"][0]})
    ft_id = result.lastrowid
    audit(db, ctx.user.id, ft_id, "__create__", None, values["name"])
    write_clusions(db, ft_id, "in", values["inclusions"])
    write_clusions(db, ft_id, "ex", values["exclusions"])
    row = db.execute(text("SELECT * FROM flagtypes WHERE id = :id"), {"id": ft_id}).mappings().first()
    after_update(db, dict(row))
    return {"id": ft_id}


# ---------------------------------------------------------------- FlagType.update
def update(ctx: Ctx) -> dict:
    ctx.user.require_login()
    if not can_admin_flagtypes(ctx):
        raise auth_failure("edit")
    params = dict(ctx.params)
    if params.get("names") is None and params.get("ids") is None:
        raise params_required("FlagType.update", ["ids", "names"])

    ids = list(params.pop("ids", None) or [])
    names = params.pop("names", None)
    if names is not None:
        names = names if isinstance(names, list) else [names]
        if names:
            binds = {f"n{i}": n for i, n in enumerate(names)}
            ids += [r[0] for r in ctx.db.execute(text(
                "SELECT id FROM flagtypes WHERE 1=1 AND name IN (" + ",".join(f":{k}" for k in binds) + ")"), binds)]
    seen = set()
    ids = [i for i in ids if not (perl_str(i) in seen or seen.add(perl_str(i)))]

    inclusions = exclusions = None
    if params.get("inclusions") is not None:
        inclusions = process_lists(ctx, params.pop("inclusions"))
    if params.get("exclusions") is not None:
        exclusions = process_lists(ctx, params.pop("exclusions"))
    fields = {k: v for k, v in params.items()
              if k not in ("inclusions", "exclusions") and not k.startswith("Bugzilla_") and k != "_remote_ip"}

    db = ctx.db
    result = []
    for ft_id in ids:
        old = check_flagtype(db, ft_id)
        new = dict(old)
        for key in sorted(fields):  # Bugzilla::Object::set_all sorts the fields
            if key not in SETTERS:
                raise BugzillaError("unknown_method", f"The requested method '{CLASS}::set_{key}' was not found.",
                                    code=67399)
            new[SETTERS[key]] = validate(db, key if key.endswith("_group") else SETTERS[key], fields[key])

        changes = {}
        for column in UPDATE_COLUMNS:
            o, n = old[column], new[column]
            if o is None and n is None:
                continue
            if o is not None and n is not None and perl_str(o) == perl_str(n):
                continue
            changes[column] = (None if o is None else perl_str(o), None if n is None else perl_str(n))
        if changes:
            sets = ", ".join(f"{c} = :{c}" for c in changes)
            db.execute(text(f"UPDATE flagtypes SET {sets} WHERE id = :id"),
                       {**{c: new[c] for c in changes}, "id": old["id"]})
            for column, (removed, added) in changes.items():
                audit(db, ctx.user.id, old["id"], column, removed, added)
        if inclusions is not None:
            write_clusions(db, old["id"], "in", inclusions)
            changes["inclusions"] = ("0", "1")
        if exclusions is not None:
            write_clusions(db, old["id"], "ex", exclusions)
            changes["exclusions"] = ("0", "1")
        after_update(db, new)
        result.append({"id": old["id"], "name": new["name"],
                       "changes": {f: {"removed": r, "added": a} for f, (r, a) in changes.items()}})
    return {"flagtypes": result}
