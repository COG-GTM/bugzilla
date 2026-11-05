"""Port of Bugzilla::WebService::Component (create) and the Bugzilla::Component create path.

Perl flow reproduced here, in order:
  Bugzilla->login(LOGIN_REQUIRED)                                   -> 410
  in_group('editcomponents') || get_products_by_permission(...)     -> 304 auth_failure
  $user->check_can_admin_product($params->{product})               -> 50 / 106 / product_admin_denied
  translate($params, MAPPED_FIELDS); Bugzilla::Component->create:
    check_required_create_fields (name, product, initialowner, description default to undef)
    run_create_validators in Bugzilla::Object::_sort_by_dep order:
      create_series, description, initial_cc, initialowner, initialqacontact, isactive, product, then name
    insert_create_data: unknown keys -> 53 param_invalid, INSERT components, audit_log __create__
    _update_cc_list (component_cc), _create_series (series / series_categories)
"""
from urllib.parse import quote

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.auth import User
from app.core.errors import BugzillaError
from app.core.params import param
from app.core.rest import CREDENTIAL_RENAMES, Ctx
from app.modules.bug.service import object_does_not_exist, object_not_specified

MAPPED_FIELDS = {"default_assignee": "initialowner", "default_qa_contact": "initialqacontact",
                 "default_cc": "initial_cc", "is_open": "isactive"}
DB_COLUMNS = ("id", "name", "product_id", "initialowner", "initialqacontact", "description", "isactive")
REQUIRED_CREATE_FIELDS = ("description", "initialowner", "name", "product")
MAX_COMPONENT_SIZE = 64
CONTROLMAPMANDATORY = 3
# request-level keys Perl's REST/auth layers consume before Component.create sees the params
TRANSPORT_KEYS = set(CREDENTIAL_RENAMES.values()) | {"_remote_ip"}


class PerlDie(BugzillaError):
    """A Perl ``die`` inside a validator: Bugzilla::WebService::Server::REST has already picked the success
    status by then, so the error envelope (code 100500) goes out with HTTP 201."""

    def __init__(self, message: str, status: int):
        super().__init__("unknown_error", message, code=100500)
        self._status = status

    @property
    def http_status(self) -> int:
        return self._status


# ---------------------------------------------------------------- Perl helpers
def perl_true(value) -> bool:
    """Perl truthiness: undef, "", "0" and 0 are false; every other string (even "false") is true."""
    if value is None or value is False:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value not in ("", "0")
    return bool(value)


def trim(value):
    if value is None:
        return None
    return str(value).strip()


def url_quote(value: str) -> str:
    # Bugzilla::Util::url_quote: everything but [a-zA-Z0-9_.-] is %XX (upper-case hex)
    return quote(value, safe="_.-")


# ---------------------------------------------------------------- permissions (Bugzilla::User)
def _groups_sql(user: User) -> str:
    return ",".join(str(g) for g in sorted(user.group_ids)) or "0"


def selectable_product_ids(db: Session, user: User) -> set[int]:
    """Bugzilla::User::get_selectable_products: no MANDATORY group the user is not in (or_groups off)."""
    if perl_true(param("or_groups")):
        sql = (f"SELECT id FROM products LEFT JOIN group_control_map gcm ON gcm.product_id = products.id "
               f"AND gcm.membercontrol = {CONTROLMAPMANDATORY} WHERE gcm.group_id IN ({_groups_sql(user)}) "
               "OR gcm.group_id IS NULL")
    else:
        sql = (f"SELECT id FROM products LEFT JOIN group_control_map gcm ON gcm.product_id = products.id "
               f"AND gcm.membercontrol = {CONTROLMAPMANDATORY} AND gcm.group_id NOT IN ({_groups_sql(user)}) "
               "WHERE gcm.group_id IS NULL")
    return {r[0] for r in db.execute(text(sql))}


def enterable_product_ids(db: Session, user: User) -> set[int]:
    """Bugzilla::User::get_enterable_products."""
    if perl_true(param("or_groups")):
        sql = (f"SELECT products.id FROM products LEFT JOIN group_control_map gcm ON gcm.product_id = products.id "
               f"AND gcm.entry != 0 WHERE (gcm.group_id IN ({_groups_sql(user)}) OR gcm.group_id IS NULL) "
               "AND products.isactive = 1")
    else:
        sql = (f"SELECT products.id FROM products LEFT JOIN group_control_map gcm ON gcm.product_id = products.id "
               f"AND gcm.entry != 0 AND gcm.group_id NOT IN ({_groups_sql(user)}) WHERE gcm.group_id IS NULL "
               "AND products.isactive = 1")
    ids = {r[0] for r in db.execute(text(sql))}
    if not ids:
        return ids
    rows = db.execute(text(
        "SELECT DISTINCT products.id FROM products WHERE products.id IN :ids "
        "AND products.id IN (SELECT DISTINCT product_id FROM components WHERE isactive = 1) "
        "AND products.id IN (SELECT DISTINCT product_id FROM versions WHERE isactive = 1)"
    ).bindparams(ids=tuple(ids)))
    return {r[0] for r in rows}


def in_group_for_product(db: Session, user: User, group: str, product_id: int) -> bool:
    """Bugzilla::User::in_group($group, $product_id) for PER_PRODUCT_PRIVILEGES."""
    if user.in_group(group):
        return True
    if group not in ("editcomponents", "editbugs", "canconfirm"):
        return False
    row = db.execute(text(f"SELECT 1 FROM group_control_map WHERE product_id = :p AND {group} != 0 "
                          f"AND group_id IN ({_groups_sql(user)}) LIMIT 1"), {"p": product_id}).first()
    return row is not None


def products_by_permission(db: Session, user: User, group: str) -> set[int]:
    """Bugzilla::User::get_products_by_permission: products granting ``group`` through group_control_map,
    restricted to the ones the user can see."""
    if group not in ("editcomponents", "editbugs", "canconfirm"):
        return set()
    ids = {r[0] for r in db.execute(text(f"SELECT DISTINCT product_id FROM group_control_map WHERE {group} != 0 "
                                         f"AND group_id IN ({_groups_sql(user)})"))}
    return ids & selectable_product_ids(db, user) if ids else set()


def check_can_admin_product(ctx: Ctx, product_name) -> dict:
    """Bugzilla::User::check_can_admin_product: Bugzilla::Product->check + per-product editcomponents."""
    name = trim(product_name)
    if name is None or name == "":
        raise object_not_specified("product")
    row = ctx.db.execute(text("SELECT id, name FROM products WHERE name = :n"), {"n": name}).mappings().first()
    denied = BugzillaError("product_access_denied",
                           f"Either the product '{name}' does not exist or you don't have access to it.")
    if not row:
        raise denied
    accessible = selectable_product_ids(ctx.db, ctx.user) | enterable_product_ids(ctx.db, ctx.user)
    if row["id"] not in accessible:
        raise denied
    if not (in_group_for_product(ctx.db, ctx.user, "editcomponents", row["id"])
            and row["id"] in selectable_product_ids(ctx.db, ctx.user)):
        raise BugzillaError("product_admin_denied",
                            f"You are not allowed to edit properties of product '{row['name']}'.")
    return dict(row)


# ---------------------------------------------------------------- validators (Bugzilla::Component)
def _user_check(db: Session, login) -> int:
    """Bugzilla::User->check($login): object_not_specified / object_does_not_exist (class Bugzilla::User)."""
    login = trim(login)
    if login is None or login == "":
        raise object_not_specified("user")
    row = db.execute(text("SELECT userid, login_name FROM profiles WHERE login_name = :l"), {"l": login}).first()
    if not row or row.login_name.lower() != login.lower():
        raise object_does_not_exist("user", login)
    return row.userid


def _login_to_id(db: Session, login) -> int:
    """Bugzilla::User::login_to_id($login, THROW_ERROR)."""
    login = "" if login is None else str(login)
    row = db.execute(text("SELECT userid FROM profiles WHERE LOWER(login_name) = LOWER(:l)"), {"l": login}).first()
    if not row:
        raise BugzillaError("invalid_username", f"The name {login} is not a valid username. Either you misspelled "
                            "it, or the person has not registered for a Bugzilla account.")
    return row.userid


def _check_description(value) -> str:
    value = trim(value)
    if not perl_true(value):
        raise BugzillaError("component_blank_description",
                            "You must enter a non-blank description for this component.")
    return value


def _check_cc_list(db: Session, cc_list) -> list[int]:
    if not isinstance(cc_list, list):
        raise PerlDie(f'Can\'t use string ("{cc_list}") as an ARRAY ref while "strict refs" in use at '
                      "/var/www/html/Bugzilla/Component.pm line 237.\n", 201)
    ids: dict[int, bool] = {}
    for cc in cc_list:
        ids[_login_to_id(db, cc)] = True
    return list(ids)


def _check_initialowner(db: Session, owner) -> int:
    if not perl_true(owner):
        raise BugzillaError("component_need_initialowner", "A default assignee is required for this component.")
    return _user_check(db, owner)


def _check_initialqacontact(db: Session, qa_contact) -> int | None:
    if perl_true(param("useqacontact")) and perl_true(qa_contact):
        return _user_check(db, qa_contact)
    return None


def _check_name(db: Session, name, product: dict) -> str:
    name = trim(name)
    if not perl_true(name):
        raise BugzillaError("component_blank_name", "You must enter a name for this new component.")
    if len(name) > MAX_COMPONENT_SIZE:
        raise BugzillaError("component_name_too_long", f"The name of a component is limited to {MAX_COMPONENT_SIZE} "
                            f"characters. '{name}' is too long ({len(name)} characters).")
    existing = db.execute(text("SELECT name FROM components WHERE product_id = :p AND name = :n"),
                          {"p": product["id"], "n": name}).first()
    if existing:
        raise BugzillaError("component_already_exists", f"The {product['name']} product already has a component "
                            f"named {existing.name}.")
    return name


# ---------------------------------------------------------------- side effects
def _update_cc_list(db: Session, component_id: int, cc_ids: list[int]):
    db.execute(text("DELETE FROM component_cc WHERE component_id = :c"), {"c": component_id})
    for uid in cc_ids:
        db.execute(text("INSERT INTO component_cc (user_id, component_id) VALUES (:u, :c)"),
                   {"u": uid, "c": component_id})


def _category_id(db: Session, name: str) -> int:
    """Bugzilla::Series::getCategoryID (insert on first use)."""
    while True:
        row = db.execute(text("SELECT id FROM series_categories WHERE name = :n"), {"n": name}).first()
        if row:
            return row[0]
        db.execute(text("INSERT INTO series_categories (name) VALUES (:n)"), {"n": name})


def _create_series(db: Session, user: User, product_name: str, component_name: str):
    """Bugzilla::Component::_create_series: the 'All Open' / 'All Closed' charting series of the component."""
    prodcomp = f"&product={url_quote(product_name)}&component={url_quote(component_name)}"
    series = [("All Open", "field0-0-0=resolution&type0-0-0=notregexp&value0-0-0=." + prodcomp),
              ("All Closed", "field0-0-0=resolution&type0-0-0=regexp&value0-0-0=." + prodcomp)]
    category = _category_id(db, product_name)
    subcategory = _category_id(db, component_name)
    for name, query in series:
        existing = db.execute(text("SELECT series_id FROM series WHERE category = :c AND subcategory = :s "
                                   "AND name = :n"), {"c": category, "s": subcategory, "n": name}).first()
        if existing:
            db.execute(text("UPDATE series SET category = :c, subcategory = :s, name = :n, frequency = 1, "
                            "is_public = 1 WHERE series_id = :id"),
                       {"c": category, "s": subcategory, "n": name, "id": existing[0]})
        else:
            db.execute(text("INSERT INTO series (creator, category, subcategory, name, frequency, query, is_public) "
                            "VALUES (:u, :c, :s, :n, 1, :q, 1)"),
                       {"u": user.id, "c": category, "s": subcategory, "n": name, "q": query})


def _audit_create(db: Session, user: User, component_id: int, name: str):
    db.execute(text("INSERT INTO audit_log (user_id, class, object_id, field, removed, added, at_time) "
                    "VALUES (:u, 'Bugzilla::Component', :id, '__create__', NULL, :name, LOCALTIMESTAMP(0))"),
               {"u": user.id or None, "id": component_id, "name": name})


# ---------------------------------------------------------------- Component.create
def create(ctx: Ctx) -> dict:
    db, user = ctx.db, ctx.user
    user.require_login()

    if not (user.in_group("editcomponents") or products_by_permission(db, user, "editcomponents")):
        raise BugzillaError("auth_failure", "Sorry, you aren't a member of the 'editcomponents' group, and so you "
                            "are not authorized to add, modify or delete components.")

    params = {k: v for k, v in ctx.params.items() if k not in TRANSPORT_KEYS}
    product = check_can_admin_product(ctx, params.get("product"))

    # translate($params, MAPPED_FIELDS)
    values = {MAPPED_FIELDS.get(k, k): v for k, v in params.items()}
    values["product"] = product

    # check_required_create_fields
    for field in REQUIRED_CREATE_FIELDS:
        values.setdefault(field, None)

    # run_create_validators, in _sort_by_dep order (no-dependency fields alphabetically, then name)
    create_series = perl_true(values.pop("create_series")) if "create_series" in values else False
    values["description"] = _check_description(values["description"])
    cc_ids = _check_cc_list(db, values.pop("initial_cc")) if "initial_cc" in values else None
    values["initialowner"] = _check_initialowner(db, values["initialowner"])
    if "initialqacontact" in values:
        values["initialqacontact"] = _check_initialqacontact(db, values["initialqacontact"])
    if "isactive" in values:
        values["isactive"] = 1 if perl_true(values["isactive"]) else 0
    check_can_admin_product(ctx, product["name"])  # _check_product
    values["name"] = _check_name(db, values["name"], product)

    # insert_create_data: every remaining key must be a components column
    values.pop("product")
    values["product_id"] = product["id"]
    for key in values:
        if key not in DB_COLUMNS:
            raise BugzillaError("param_invalid",
                                f"{key} is not a valid parameter for the Bugzilla::Component::create function.")
    columns = list(values)
    db.execute(text(f"INSERT INTO components ({', '.join(columns)}) VALUES ({', '.join(':' + c for c in columns)})"),
               values)
    component_id = db.execute(text("SELECT LAST_INSERT_ID()")).scalar()
    _audit_create(db, user, component_id, values["name"])

    if cc_ids:
        _update_cc_list(db, component_id, cc_ids)
    if create_series:
        _create_series(db, user, product["name"], values["name"])
    return {"id": int(component_id)}
