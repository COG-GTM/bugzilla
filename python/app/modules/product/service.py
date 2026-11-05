"""Product.get_selectable/enterable/accessible_products, Product.get, Product.create and Product.update
(Bugzilla/WebService/Product.pm + the domain bits of Bugzilla/Product.pm, Version.pm, Milestone.pm,
Component.pm, Classification.pm, Series.pm that those methods touch)."""
from sqlalchemy import text

from app.core.errors import BugzillaError
from app.core.params import param
from app.core.rest import Ctx
from app.core.util import now_utc

MAX_PRODUCT_SIZE = 64
CONTROLMAPNA = 0
CONTROLMAPDEFAULT = 2
CONTROLMAPMANDATORY = 3
TYPES = ("accessible", "enterable", "selectable")
# Product.create / Product.update API field -> products column (Bugzilla::WebService::Product FIELD_MAP)
FIELD_MAP = {"has_unconfirmed": "allows_unconfirmed", "is_open": "isactive", "default_milestone": "defaultmilestone",
             "name": "name", "description": "description"}
# products column -> field name used in Product.update's `changes` (MAPPED_RETURNS)
RETURN_MAP = {"allows_unconfirmed": "has_unconfirmed", "defaultmilestone": "default_milestone", "isactive": "is_open",
              "name": "name", "description": "description"}
# Bugzilla::Product set_* methods reachable from Product.update, keyed by the (translated) field name
SETTERS = {"name": "name", "description": "description", "default_milestone": "defaultmilestone",
           "is_active": "isactive", "allows_unconfirmed": "allows_unconfirmed"}


# ---------------------------------------------------------------- helpers
def path_param(value: str) -> dict:
    """REST: a numeric path segment is an id, anything else a name (Resources/Product.pm)."""
    return {"ids": [value]} if value.isdigit() else {"names": [value]}


def _as_list(value) -> list:
    """Perl receives `ids`/`names`/`type` as given: a scalar or an array, csv strings are not split."""
    return value if isinstance(value, list) else [value]


def _truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return value != 0
    s = str(value)
    return s not in ("", "0")


def _trim(value) -> str:
    return "" if value is None else str(value).strip()


def _groups_sql(ctx: Ctx) -> str:
    gids = sorted(ctx.user.group_ids)
    return ",".join(str(g) for g in gids) if gids else "-1"


def _group_clause(ctx: Ctx) -> str:
    """Tail of the group_control_map join in User::get_selectable/enterable_products.
    or_groups: the user is in at least one of the controlling groups, or there are none.
    Otherwise: there must be no controlling group the user is not in."""
    if param("or_groups"):
        return f"WHERE (gcm.group_id IN ({_groups_sql(ctx)}) OR gcm.group_id IS NULL)"
    return f"AND gcm.group_id NOT IN ({_groups_sql(ctx)}) WHERE gcm.group_id IS NULL"


def _auth_failure(action: str) -> BugzillaError:
    verb = "add new" if action == "add" else "add, modify or delete"
    return BugzillaError("auth_failure", "Sorry, you aren't a member of the 'editcomponents' group, and so you "
                                         f"are not authorized to {verb} products.")


def _audit(ctx: Ctx, cls: str, object_id: int, field: str, removed, added):
    ctx.db.execute(text("INSERT INTO audit_log (user_id, class, object_id, field, removed, added, at_time) "
                        "VALUES (:u, :c, :o, :f, :r, :a, :t)"),
                   {"u": ctx.user.id or None, "c": cls, "o": object_id, "f": field, "r": removed, "a": added,
                    "t": now_utc().replace(tzinfo=None)})


# ---------------------------------------------------------------- visibility (Bugzilla::User)
def selectable_ids(ctx: Ctx) -> list[int]:
    """Products with no MANDATORY group the user is not in, ordered by name (Product LIST_ORDER)."""
    sql = f"""SELECT id FROM products
              LEFT JOIN group_control_map gcm ON gcm.product_id = products.id
                   AND gcm.membercontrol = {CONTROLMAPMANDATORY} {_group_clause(ctx)} ORDER BY products.name"""
    return [r[0] for r in ctx.db.execute(text(sql))]


def enterable_ids(ctx: Ctx) -> list[int]:
    """Active products with Entry access for the user and at least one active component and version."""
    sql = f"""SELECT products.id FROM products
              LEFT JOIN group_control_map gcm ON gcm.product_id = products.id
                   AND gcm.entry != 0 {_group_clause(ctx)} AND products.isactive = 1
                AND products.id IN (SELECT DISTINCT product_id FROM components WHERE isactive = 1)
                AND products.id IN (SELECT DISTINCT product_id FROM versions WHERE isactive = 1)
              ORDER BY products.name"""
    return [r[0] for r in ctx.db.execute(text(sql))]


def accessible_ids(ctx: Ctx) -> list[int]:
    """Union of selectable and enterable, sorted by name with Perl's `cmp` (case-sensitive)."""
    ids = set(selectable_ids(ctx)) | set(enterable_ids(ctx))
    if not ids:
        return []
    rows = ctx.db.execute(text("SELECT id, name FROM products WHERE id IN :ids").bindparams(ids=tuple(ids))).all()
    return [r.id for r in sorted(rows, key=lambda r: r.name.encode("utf-8"))]


def id_list(ctx: Ctx, kind: str) -> dict:
    return {"ids": {"accessible": accessible_ids, "enterable": enterable_ids, "selectable": selectable_ids}[kind](ctx)}


# ---------------------------------------------------------------- Product.get
def get(ctx: Ctx, ids=None, names=None) -> dict:
    p = ctx.params
    ids = p.get("ids", ids)
    names = p.get("names", names)
    types = p.get("type")
    if ids is None and names is None and types is None:
        raise BugzillaError("params_required", "The function Product.get requires that you set one of the following "
                                               "parameters: ids, names, type")
    if types is not None:
        pool: list[int] = []
        for t in _as_list(types):
            if t not in TYPES:
                raise BugzillaError("get_products_invalid_type", f"The product type '{t}' is invalid. Valid choices "
                                    "are 'accessible', 'selectable', and 'enterable'.")
            pool.extend(i for i in id_list(ctx, t)["ids"] if i not in pool)
    else:
        pool = accessible_ids(ctx)
    products = _load_products(ctx, pool)

    wanted: list[dict] = []
    if ids is not None:
        want = {str(i) for i in _as_list(ids)}
        wanted.extend(pr for pr in products if str(pr["id"]) in want)
    if names is not None:
        want = {str(n).lower() for n in _as_list(names)}
        seen = {pr["id"] for pr in wanted}
        wanted.extend(pr for pr in products if pr["name"].lower() in want and pr["id"] not in seen)
    if ids is None and names is None:
        wanted = products
    return {"products": [_product_to_hash(ctx, pr) for pr in wanted]}


def _load_products(ctx: Ctx, ids: list[int]) -> list[dict]:
    if not ids:
        return []
    rows = ctx.db.execute(text(
        "SELECT p.id, p.name, p.description, p.isactive, p.defaultmilestone, p.allows_unconfirmed, "
        "c.name AS classification FROM products p JOIN classifications c ON c.id = p.classification_id "
        "WHERE p.id IN :ids").bindparams(ids=tuple(ids))).mappings().all()
    by_id = {r["id"]: dict(r) for r in rows}
    return [by_id[i] for i in ids if i in by_id]


def _product_to_hash(ctx: Ctx, pr: dict) -> dict:
    f = ctx.filter
    item = {"id": pr["id"], "name": pr["name"], "description": pr["description"], "is_active": bool(pr["isactive"]),
            "default_milestone": pr["defaultmilestone"], "has_unconfirmed": bool(pr["allows_unconfirmed"]),
            "classification": pr["classification"]}
    out = f.apply(item)
    if f.wants("components"):
        out["components"] = [_component_to_hash(ctx, c) for c in _components(ctx, pr["id"])]
    if f.wants("versions"):
        out["versions"] = [f.apply({"id": v.id, "name": v.value, "sort_key": 0, "is_active": bool(v.isactive)},
                                   prefix="versions") for v in _versions(ctx, pr["id"])]
    if f.wants("milestones"):
        out["milestones"] = [f.apply({"id": m.id, "name": m.value, "sort_key": int(m.sortkey),
                                      "is_active": bool(m.isactive)}, prefix="milestones")
                             for m in _milestones(ctx, pr["id"])]
    return out


def _components(ctx: Ctx, product_id: int):
    return ctx.db.execute(text(
        "SELECT c.id, c.name, c.description, c.isactive, c.product_id, o.login_name AS owner, q.login_name AS qa "
        "FROM components c JOIN profiles o ON o.userid = c.initialowner "
        "LEFT JOIN profiles q ON q.userid = c.initialqacontact WHERE c.product_id = :p ORDER BY c.name"),
        {"p": product_id}).all()


def _versions(ctx: Ctx, product_id: int):
    return ctx.db.execute(text("SELECT id, value, isactive FROM versions WHERE product_id = :p ORDER BY id"),
                          {"p": product_id}).all()


def _milestones(ctx: Ctx, product_id: int):
    return ctx.db.execute(text("SELECT id, value, sortkey, isactive FROM milestones WHERE product_id = :p "
                               "ORDER BY sortkey, value"), {"p": product_id}).all()


def _component_to_hash(ctx: Ctx, c) -> dict:
    f = ctx.filter
    out = f.apply({"id": c.id, "name": c.name, "description": c.description, "default_assigned_to": c.owner,
                   "default_qa_contact": c.qa or "", "sort_key": 0, "is_active": bool(c.isactive)},
                  prefix="components")
    if f.wants("flag_types", None, "components"):
        types = _flag_types(ctx, c.product_id, c.id)
        out["flag_types"] = {"bug": [_flag_type_to_hash(t) for t in types if t.target_type == "b"],
                             "attachment": [_flag_type_to_hash(t) for t in types if t.target_type == "a"]}
    return out


def _flag_types(ctx: Ctx, product_id: int, component_id: int):
    """Bugzilla::FlagType::match({product_id, component_id}): inclusions minus exclusions."""
    return ctx.db.execute(text(f"""
        SELECT DISTINCT flagtypes.* FROM flagtypes
        INNER JOIN flaginclusions AS i ON flagtypes.id = i.type_id
        LEFT JOIN flagexclusions AS e ON (flagtypes.id = e.type_id
             AND (e.component_id = {int(component_id)} OR e.component_id IS NULL)
             AND ((e.product_id = {int(product_id)}) OR e.product_id IS NULL))
        WHERE (i.product_id = {int(product_id)} OR i.product_id IS NULL)
          AND (i.component_id = {int(component_id)} OR i.component_id IS NULL)
          AND e.type_id IS NULL ORDER BY flagtypes.sortkey, flagtypes.name""")).all()


def _flag_type_to_hash(t) -> dict:
    """Perl's _flag_type_to_hash is called without $params, so include/exclude_fields never trims it."""
    return {"id": t.id, "name": t.name, "description": t.description, "cc_list": t.cc_list or "",
            "sort_key": int(t.sortkey), "is_active": bool(t.is_active), "is_requestable": bool(t.is_requestable),
            "is_requesteeble": bool(t.is_requesteeble), "is_multiplicable": bool(t.is_multiplicable),
            "grant_group": t.grant_group_id, "request_group": t.request_group_id}


# ---------------------------------------------------------------- validators (Bugzilla::Product)
def _check_name(ctx: Ctx, name, self_id: int | None = None) -> str:
    name = _trim(name)
    if not name:
        raise BugzillaError("product_blank_name", "You must enter a name for the product.")
    if len(name) > MAX_PRODUCT_SIZE:
        raise BugzillaError("product_name_too_long", f"The name of a product is limited to {MAX_PRODUCT_SIZE} "
                            f"characters. '{name}' is too long ({len(name)} characters).")
    row = ctx.db.execute(text("SELECT id, name FROM products WHERE name = :n"), {"n": name}).first()
    if row and row.id != self_id:
        if row.name == name:
            raise BugzillaError("product_name_already_in_use", f"The product name '{row.name}' already exists.")
        raise BugzillaError("product_name_diff_in_case", f"The product name '{name}' differs from existing "
                            f"product '{row.name}' only in case.")
    return name


def _check_description(description) -> str:
    description = _trim(description)
    if not description:
        raise BugzillaError("product_must_have_description", "You must enter a description for this product.")
    return description


def _check_version(version) -> str:
    version = _trim(version)
    if not version:
        raise BugzillaError("product_must_have_version", "You must enter a valid version to create a new product.")
    return version


def _check_classification(ctx: Ctx, name) -> int:
    if not param("useclassification"):
        return 1
    if name is None:
        return 1
    name = _trim(name)
    if not name:
        raise BugzillaError("object_not_specified", "You must select/enter a classification.")
    row = ctx.db.execute(text("SELECT id FROM classifications WHERE name = :n"), {"n": name}).first()
    if not row:
        raise BugzillaError("object_does_not_exist", f"There is no classification named '{name}'.")
    return row.id


def _check_default_milestone(ctx: Ctx, milestone, product: dict | None = None) -> str:
    if not param("usetargetmilestone"):
        return product["defaultmilestone"] if product else "---"
    milestone = _trim(milestone)
    if product is not None:
        row = ctx.db.execute(text("SELECT id FROM milestones WHERE product_id = :p AND value = :v"),
                             {"p": product["id"], "v": milestone}).first()
        if not row:
            raise BugzillaError("product_must_define_defaultmilestone", f"You must create the milestone "
                                f"'{milestone}' before it can be made the default milestone for product "
                                f"'{product['name']}'.")
        return milestone
    return milestone or "---"


# ---------------------------------------------------------------- Product.create
def create(ctx: Ctx) -> dict:
    ctx.user.require_login()
    if not ctx.user.in_group("editcomponents"):
        raise _auth_failure("add")
    p = ctx.params
    # Bugzilla::Object runs the validators in alphabetical order of the field names
    allows_unconfirmed = int(_truthy(p.get("has_unconfirmed"))) if p.get("has_unconfirmed") is not None else 1
    classification_id = _check_classification(ctx, p.get("classification"))
    create_series = _truthy(p.get("create_series")) if p.get("create_series") is not None else True
    defaultmilestone = _check_default_milestone(ctx, p.get("default_milestone"))
    description = _check_description(p.get("description"))
    isactive = int(_truthy(p.get("is_open"))) if p.get("is_open") is not None else 1
    name = _check_name(ctx, p.get("name"))
    version = _check_version(p.get("version"))

    db = ctx.db
    db.execute(text("INSERT INTO products (name, description, isactive, defaultmilestone, allows_unconfirmed, "
                    "classification_id) VALUES (:n, :d, :a, :m, :u, :c)"),
               {"n": name, "d": description, "a": isactive, "m": defaultmilestone, "u": allows_unconfirmed,
                "c": classification_id})
    product_id = db.execute(text("SELECT LAST_INSERT_ID()")).scalar()
    _audit(ctx, "Bugzilla::Product", product_id, "__create__", None, name)

    db.execute(text("INSERT INTO versions (value, product_id, isactive) VALUES (:v, :p, 1)"),
               {"v": version, "p": product_id})
    _audit(ctx, "Bugzilla::Version", db.execute(text("SELECT LAST_INSERT_ID()")).scalar(), "__create__", None, version)
    db.execute(text("INSERT INTO milestones (value, product_id, sortkey, isactive) VALUES (:v, :p, 0, 1)"),
               {"v": defaultmilestone, "p": product_id})
    _audit(ctx, "Bugzilla::Milestone", db.execute(text("SELECT LAST_INSERT_ID()")).scalar(), "__create__", None,
           defaultmilestone)

    if param("makeproductgroups"):
        _create_bug_group(ctx, product_id, name)
    if create_series:
        _create_series(ctx, name)
    return {"id": int(product_id)}


def _url_quote(value: str) -> str:
    """Bugzilla::Util::url_quote: everything but [A-Za-z0-9_.-] becomes %XX (upper-case hex)."""
    return "".join(ch if ch.isascii() and (ch.isalnum() or ch in "_-.") else
                   "".join(f"%{b:02X}" for b in ch.encode("utf-8")) for ch in value)


def _series_category_id(ctx: Ctx, name: str) -> int:
    row = ctx.db.execute(text("SELECT id FROM series_categories WHERE name = :n"), {"n": name}).first()
    if row:
        return row.id
    ctx.db.execute(text("INSERT INTO series_categories (name) VALUES (:n)"), {"n": name})
    return ctx.db.execute(text("SELECT LAST_INSERT_ID()")).scalar()


def _create_series(ctx: Ctx, product_name: str):
    """Bugzilla::Product::_create_series: one series per status, per resolution, plus 'All Open'."""
    db = ctx.db
    series = []
    statuses = db.execute(text("SELECT value, is_open FROM bug_status WHERE isactive = 1 ORDER BY sortkey, value")).all()
    for s in statuses:
        series.append((s.value, "bug_status=" + _url_quote(s.value)))
    for r in db.execute(text("SELECT value FROM resolution WHERE isactive = 1 ORDER BY sortkey, value")):
        if r.value:
            series.append((r.value, "resolution=" + _url_quote(r.value)))
    series.append(("All Open", "&".join("bug_status=" + _url_quote(s.value) for s in statuses if s.is_open)))
    category = _series_category_id(ctx, product_name)
    subcategory = _series_category_id(ctx, "-All-")
    for name, query in series:
        db.execute(text("INSERT INTO series (creator, category, subcategory, name, frequency, query, is_public) "
                        "VALUES (:c, :cat, :sub, :n, 1, :q, 1)"),
                   {"c": ctx.user.id, "cat": category, "sub": subcategory, "n": name,
                    "q": f"{query}&product={_url_quote(product_name)}"})


def _create_bug_group(ctx: Ctx, product_id: int, name: str):
    """Bugzilla::Product::_create_bug_group (only when the `makeproductgroups` param is on)."""
    db = ctx.db
    group_name = name
    n = 1
    while db.execute(text("SELECT 1 FROM groups WHERE name = :n"), {"n": group_name}).first():
        group_name = f"{name}_{n}"
        n += 1
    db.execute(text("INSERT INTO groups (name, description, isbuggroup, isactive) VALUES (:n, :d, 1, 1)"),
               {"n": group_name, "d": f"Access to bugs in the {name} product"})
    gid = db.execute(text("SELECT LAST_INSERT_ID()")).scalar()
    _audit(ctx, "Bugzilla::Group", gid, "__create__", None, group_name)
    db.execute(text("INSERT INTO group_control_map (group_id, product_id, membercontrol, othercontrol) "
                    "VALUES (:g, :p, :m, :o)"),
               {"g": gid, "p": product_id, "m": CONTROLMAPDEFAULT, "o": CONTROLMAPNA})
    db.execute(text("INSERT INTO group_group_map (member_id, grantor_id, grant_type) VALUES (:a, :g, 0)"),
               {"a": db.execute(text("SELECT id FROM groups WHERE name = 'admin'")).scalar(), "g": gid})


# ---------------------------------------------------------------- Product.update
def update(ctx: Ctx, ids=None, names=None) -> dict:
    ctx.user.require_login()
    if not ctx.user.in_group("editcomponents"):
        raise _auth_failure("edit")
    p = ctx.params
    ids = p.get("ids", ids)
    names = p.get("names", names)
    if ids is None and names is None:
        raise BugzillaError("params_required", "The function Product.update requires that you set one of the "
                                               "following parameters: ids, names")

    products: list[dict] = []
    for name in _as_list(names) if names is not None else []:
        _add_product(ctx, products, _check_product(ctx, name=name))
    for pid in _as_list(ids) if ids is not None else []:
        _add_product(ctx, products, _check_product(ctx, pid=pid))

    fields = {k: v for k, v in p.items() if k not in ("ids", "names", "_remote_ip") and not k.startswith("Bugzilla_")}
    # Bugzilla::WebService::Util::translate(has_unconfirmed => allows_unconfirmed, is_open => is_active)
    translated = {{"has_unconfirmed": "allows_unconfirmed", "is_open": "is_active"}.get(k, k): v
                  for k, v in fields.items()}
    staged = [(pr, _set_all(ctx, pr, translated)) for pr in products]
    result = []
    for pr, new_values in staged:
        changes = _write_changes(ctx, pr, new_values)
        result.append({"id": pr["id"], "changes": {RETURN_MAP[col]: {"removed": str(old), "added": str(new)}
                                                    for col, (old, new) in changes.items()}})
    return {"products": result}


def _add_product(ctx: Ctx, products: list[dict], pr: dict):
    if all(x["id"] != pr["id"] for x in products):
        products.append(pr)


def _check_product(ctx: Ctx, name=None, pid=None) -> dict:
    """Bugzilla::Product->check: unknown or inaccessible products both raise product_access_denied."""
    if pid is not None:
        row = ctx.db.execute(text("SELECT id FROM products WHERE id = :i"), {"i": str(pid)}).first() \
            if str(pid).strip().isdigit() else None
        what = f"the product with the id {pid}"
    else:
        row = ctx.db.execute(text("SELECT id FROM products WHERE name = :n"), {"n": str(name)}).first()
        what = f"the product '{name}'"
    if not row or row.id not in set(accessible_ids(ctx)):
        raise BugzillaError("product_access_denied", f"Either {what} does not exist or you don't have access to it.")
    return _load_products(ctx, [row.id])[0]


def _set_all(ctx: Ctx, pr: dict, fields: dict) -> dict:
    """Validate every field against the product (no writes), like Bugzilla::Object::set_all."""
    new_values: dict[str, object] = {}
    for key, value in fields.items():
        if key not in SETTERS:
            raise BugzillaError("unknown_method", f"The requested method 'Bugzilla::Product::set_{key}' was not found.",
                                code=67399)
        col = SETTERS[key]
        if col == "name":
            new_values[col] = _check_name(ctx, value, self_id=pr["id"])
        elif col == "description":
            new_values[col] = _check_description(value)
        elif col == "defaultmilestone":
            new_values[col] = _check_default_milestone(ctx, value, product=pr)
        else:
            new_values[col] = int(_truthy(value))
    return new_values


def _write_changes(ctx: Ctx, pr: dict, new_values: dict) -> dict:
    """Bugzilla::Object::update: persist what differs and return {column: (old, new)}."""
    changes = {}
    for col, new in new_values.items():
        old = pr[col]
        if str(old) != str(new):
            changes[col] = (old, new)
    if changes:
        sets = ", ".join(f"{col} = :{col}" for col in changes)
        ctx.db.execute(text(f"UPDATE products SET {sets} WHERE id = :id"),
                       {**{col: new for col, (_, new) in changes.items()}, "id": pr["id"]})
        for col, (old, new) in changes.items():
            _audit(ctx, "Bugzilla::Product", pr["id"], col, str(old), str(new))
    return changes
