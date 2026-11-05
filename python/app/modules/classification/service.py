"""Classification.get (Bugzilla::WebService::Classification) on top of the shared core helpers.

Mirrors ``Bugzilla::WebService::Util::params_to_objects`` (names first, then ids, deduplicated by id),
``Bugzilla::Classification`` (``classifications``/``products`` tables) and the product visibility rules of
``Bugzilla::User::get_selectable_products`` / ``get_selectable_classifications``.
"""
import re

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.auth import User
from app.core.errors import BugzillaError, params_required
from app.core.params import param
from app.core.rest import Ctx

CLASS_NAME = "Bugzilla::Classification"
CONTROLMAPMANDATORY = 3
NATURAL = re.compile(r"^\d+$")


def _true(value) -> bool:
    """Perl truthiness for a params.json value ("0" and "" are false)."""
    return value not in (None, "", "0", 0, False)


def _as_list(value) -> list:
    """``validate(@_, 'names', 'ids')``: scalars become one-element lists; no csv splitting."""
    if value is None:
        return []
    return list(value) if isinstance(value, list) else [value]


def _merge(path_values: list, query_values) -> list:
    """Bugzilla::WebService::Server::REST: path values first, then the URL ones, ``uniq``'d as strings."""
    seen, merged = set(), []
    for v in [*path_values, *_as_list(query_values)]:
        s = "" if v is None else str(v)
        if s not in seen:
            seen.add(s)
            merged.append(s)
    return merged


def object_not_specified() -> BugzillaError:
    return BugzillaError("object_not_specified", "You must select/enter a classification.")


def object_does_not_exist(value: str, by_id: bool) -> BugzillaError:
    what = f"with the id '{value}'" if by_id else f"named '{value}'"
    return BugzillaError("object_does_not_exist", f"There is no classification {what}.")


def param_must_be_numeric() -> BugzillaError:
    return BugzillaError("param_must_be_numeric",
                         f"Invalid parameter passed to {CLASS_NAME}::_load_from_db: It must be numeric.")


def classification_not_enabled() -> BugzillaError:
    return BugzillaError("auth_classification_not_enabled", "Sorry, classification is not enabled.")


SELECT_CLASSIFICATION = "SELECT id, name, description, sortkey FROM classifications WHERE "


def _check_by_name(db: Session, raw) -> dict:
    name = ("" if raw is None else str(raw)).strip()
    if name == "":
        raise object_not_specified()
    row = db.execute(text(SELECT_CLASSIFICATION + "name = :name"), {"name": name}).mappings().first()
    if row is None:
        raise object_does_not_exist(name, by_id=False)
    return dict(row)


def _check_by_id(db: Session, raw) -> dict:
    value = ("" if raw is None else str(raw)).strip()
    if value == "":
        raise object_not_specified()
    if not NATURAL.match(value):
        raise param_must_be_numeric()
    row = db.execute(text(SELECT_CLASSIFICATION + "id = :id"), {"id": int(value)}).mappings().first()
    if row is None:
        raise object_does_not_exist(value, by_id=True)
    return dict(row)


def params_to_classifications(db: Session, names: list, ids: list) -> list[dict]:
    objects = [_check_by_name(db, n) for n in names] + [_check_by_id(db, i) for i in ids]
    seen, unique = set(), []
    for obj in objects:
        if obj["id"] not in seen:
            seen.add(obj["id"])
            unique.append(obj)
    return unique


PRODUCT_COLUMNS = "products.id AS id, products.name AS name, products.description AS description, " \
                  "products.classification_id AS classification_id"


def all_products(db: Session, classification_id: int) -> list[dict]:
    """``Bugzilla::Classification::products``: every product of the classification, by name."""
    rows = db.execute(text(f"SELECT {PRODUCT_COLUMNS} FROM products WHERE classification_id = :cid ORDER BY name"),
                      {"cid": classification_id}).mappings()
    return [dict(r) for r in rows]


def selectable_products(db: Session, user: User, classification_id: int | None = None) -> list[dict]:
    """``Bugzilla::User::get_selectable_products``: products without a MANDATORY group the user is not in."""
    groups = ",".join(map(str, sorted(user.group_ids))) or "-1"
    sql = f"SELECT {PRODUCT_COLUMNS} FROM products LEFT JOIN group_control_map " \
          f"ON group_control_map.product_id = products.id AND group_control_map.membercontrol = {CONTROLMAPMANDATORY}"
    if _true(param("or_groups")):
        sql += f" WHERE group_id IN ({groups}) OR group_id IS NULL"
    else:
        sql += f" AND group_id NOT IN ({groups}) WHERE group_id IS NULL"
    if classification_id is not None and _true(param("useclassification")):
        sql += " AND classification_id = :cid"
    sql += " ORDER BY products.name"
    return [dict(r) for r in db.execute(text(sql), {"cid": classification_id}).mappings()]


def _product_to_hash(ctx: Ctx, product: dict) -> dict:
    return ctx.filter.apply({
        "id": int(product["id"]), "name": product["name"], "description": product["description"] or "",
    }, prefix="products")


def _classification_to_hash(ctx: Ctx, classification: dict, can_edit: bool) -> dict:
    products = all_products(ctx.db, classification["id"]) if can_edit else \
        selectable_products(ctx.db, ctx.user, classification["id"])
    return ctx.filter.apply({
        "id": int(classification["id"]),
        "name": classification["name"],
        "description": classification["description"] or "",
        "sort_key": int(classification["sortkey"] or 0),
        "products": [_product_to_hash(ctx, p) for p in products],
    })


def get(ctx: Ctx, ids: list | None = None, names: list | None = None) -> dict:
    """``Classification.get``: ``ids``/``names`` from the path merged with the ones in the query string."""
    ids_in, names_in = ctx.params.get("ids"), ctx.params.get("names")
    if ids is None and names is None and ids_in is None and names_in is None:
        raise params_required("Classification.get", ["names", "ids"])
    all_ids = _merge(ids or [], ids_in)
    all_names = _merge(names or [], names_in)

    user = ctx.user
    can_edit = user.in_group("editclassifications")
    if not (_true(param("useclassification")) or can_edit):
        raise classification_not_enabled()

    classifications = params_to_classifications(ctx.db, all_names, all_ids)
    if not can_edit:
        # get_selectable_classifications: only classifications holding at least one selectable product
        selectable = {p["classification_id"] for p in selectable_products(ctx.db, user)}
        classifications = [c for c in classifications if c["id"] in selectable]

    return {"classifications": [_classification_to_hash(ctx, c, can_edit) for c in classifications]}
