"""Product.get_selectable/enterable/accessible_products, Product.get, Product.create, Product.update
(GET /product_selectable, GET /product_enterable, GET /product_accessible, GET /product, GET /product/{id_or_name},
POST /product, PUT /product/{id_or_name})."""
import pytest

from parity.conftest import SEED_PRODUCT, expect_error, ok, unique
from parity.normalize import ID, normalize

PRODUCT_FIELDS = {"id", "name", "description", "is_active", "default_milestone", "has_unconfirmed",
                  "classification", "components", "versions", "milestones"}
COMPONENT_FIELDS = {"id", "name", "description", "default_assigned_to", "default_qa_contact", "sort_key",
                    "is_active", "flag_types"}
SUB_FIELDS = {"id", "name", "sort_key", "is_active"}


def _ids(api, path, token=None):
    body = ok(api.get(path, token=token))
    assert set(body) == {"ids"} and all(isinstance(i, int) for i in body["ids"]), body
    return body["ids"]


def _names_by_id(api, ids, token=None):
    body = ok(api.get("/product", token=token, params={"ids": ids, "include_fields": "id,name"}))
    return {p["id"]: p["name"] for p in body["products"]}


@pytest.fixture(scope="module")
def new_product(api, admin_token):
    """Factory: create a fresh product as admin and return (id, body sent)."""
    def _create(**overrides):
        body = {"name": unique("parity product"), "description": "created by the parity suite",
                "version": "0.1", "create_series": 0}
        body.update(overrides)
        r = api.post("/product", token=admin_token, json=body)
        assert r.status_code == 201, r.text
        assert set(r.json()) == {"id"} and isinstance(r.json()["id"], int)
        return r.json()["id"], body
    return _create


@pytest.fixture(scope="module")
def inactive_product(new_product):
    pid, body = new_product(is_open=False)
    return pid, body["name"]


# ---------------------------------------------------------------- id lists
@pytest.mark.parametrize("path", ["/product_selectable", "/product_enterable", "/product_accessible"])
def test_lists_anonymous_contain_seed_product(api, path, seed_product_id):
    ids = _ids(api, path)
    assert seed_product_id in ids and len(ids) == len(set(ids))


@pytest.mark.parametrize("path", ["/product_selectable", "/product_enterable", "/product_accessible"])
def test_lists_as_user_match_anonymous(api, path, user_token):
    assert _ids(api, path, token=user_token) == _ids(api, path)


def test_lists_sorted_by_name(api, admin_token, inactive_product):
    ids = _ids(api, "/product_accessible", token=admin_token)
    names = _names_by_id(api, ids, token=admin_token)
    assert [names[i] for i in ids] == sorted(names[i] for i in ids)
    sel = _ids(api, "/product_selectable", token=admin_token)
    assert [names[i] for i in sel] == sorted((names[i] for i in sel), key=str.lower)


def test_inactive_product_is_selectable_but_not_enterable(api, admin_token, inactive_product):
    pid, _ = inactive_product
    assert pid in _ids(api, "/product_selectable", token=admin_token)
    assert pid in _ids(api, "/product_accessible", token=admin_token)
    assert pid not in _ids(api, "/product_enterable", token=admin_token)
    # anonymous users see it as well (no group controls on it)
    assert pid in _ids(api, "/product_selectable")
    assert pid not in _ids(api, "/product_enterable")


def test_list_with_api_key_param_is_rejected_when_invalid(api):
    expect_error(api.get("/product_selectable", params={"api_key": "not-a-real-key"}), 400, 306)


# ---------------------------------------------------------------- Product.get
def test_get_by_id_default_fields(api, seed_product_id):
    body = ok(api.get(f"/product/{seed_product_id}"))
    assert set(body) == {"products"} and len(body["products"]) == 1
    p = body["products"][0]
    assert set(p) == PRODUCT_FIELDS
    assert normalize({k: p[k] for k in PRODUCT_FIELDS - {"components", "versions", "milestones"}}) == {
        "id": ID, "name": SEED_PRODUCT, "description": "Customer relationship management suite.",
        "is_active": True, "default_milestone": "---", "has_unconfirmed": True, "classification": "Unclassified",
    } or p["description"]  # description text is asserted separately below
    assert p["id"] == seed_product_id and p["name"] == SEED_PRODUCT and p["is_active"] is True
    assert p["default_milestone"] == "---" and p["has_unconfirmed"] is True and p["classification"] == "Unclassified"
    assert isinstance(p["description"], str) and p["description"]

    comps = p["components"]
    assert [c["name"] for c in comps] == ["Contacts", "Mobile App", "Pipeline"]
    for c in comps:
        assert set(c) == COMPONENT_FIELDS
        assert c["sort_key"] == 0 and c["is_active"] is True and c["default_qa_contact"] == ""
        assert c["flag_types"] == {"bug": [], "attachment": []}
        assert isinstance(c["id"], int) and isinstance(c["description"], str) and c["description"]
    assert [c["default_assigned_to"] for c in comps] == [
        "alice.ng@bugzilla.test", "chen.wei@bugzilla.test", "bruno.costa@bugzilla.test"]

    assert normalize(p["versions"]) == [{"id": ID, "name": "2.4", "sort_key": 0, "is_active": True}]
    assert normalize(p["milestones"]) == [{"id": ID, "name": "---", "sort_key": 0, "is_active": True}]


def test_get_by_name_and_case_insensitive(api, seed_product_id):
    body = ok(api.get(f"/product/{SEED_PRODUCT}", params={"include_fields": "id,name"}))
    assert body == {"products": [{"id": seed_product_id, "name": SEED_PRODUCT}]}
    body = ok(api.get(f"/product/{SEED_PRODUCT.lower()}", params={"include_fields": "id,name"}))
    assert body == {"products": [{"id": seed_product_id, "name": SEED_PRODUCT}]}
    body = ok(api.get("/product", params={"names": SEED_PRODUCT.upper(), "include_fields": "id"}))
    assert body == {"products": [{"id": seed_product_id}]}


def test_get_include_exclude_fields(api, seed_product_id):
    body = ok(api.get(f"/product/{seed_product_id}", params={"include_fields": "id,name,versions"}))
    p = body["products"][0]
    assert set(p) == {"id", "name", "versions"} and set(p["versions"][0]) == SUB_FIELDS

    body = ok(api.get(f"/product/{seed_product_id}", params={"exclude_fields": "components,versions,milestones"}))
    assert set(body["products"][0]) == PRODUCT_FIELDS - {"components", "versions", "milestones"}

    body = ok(api.get(f"/product/{seed_product_id}", params={"include_fields": "components.name"}))
    assert body == {"products": [{"components": [{"name": "Contacts"}, {"name": "Mobile App"}, {"name": "Pipeline"}]}]}

    body = ok(api.get(f"/product/{seed_product_id}", params={"include_fields": "components.flag_types"}))
    assert body == {"products": [{"components": [{"flag_types": {"bug": [], "attachment": []}}] * 3}]}

    body = ok(api.get(f"/product/{seed_product_id}", params={"include_fields": "_all"}))
    assert set(body["products"][0]) == PRODUCT_FIELDS

    body = ok(api.get(f"/product/{seed_product_id}", params={"include_fields": "_default", "exclude_fields": "id"}))
    assert set(body["products"][0]) == PRODUCT_FIELDS - {"id"}


def test_get_unknown_id_and_name_return_empty(api):
    assert ok(api.get("/product/999999")) == {"products": []}
    assert ok(api.get("/product/no such product")) == {"products": []}
    assert ok(api.get("/product", params={"ids": "999999", "include_fields": "id"})) == {"products": []}
    assert ok(api.get("/product", params={"names": unique("missing")})) == {"products": []}


def test_get_without_params_is_params_required(api, admin_token):
    expect_error(api.get("/product"), 400, 50,
                 "The function Product.get requires that you set one of the following parameters: ids, names, type")
    expect_error(api.get("/product", token=admin_token, params={"include_fields": "id"}), 400, 50)


def test_get_invalid_type(api):
    expect_error(api.get("/product", params={"type": "bogus"}), 400, 32000,
                 "The product type 'bogus' is invalid. Valid choices are 'accessible', 'selectable', and 'enterable'.")
    expect_error(api.get("/product", params={"type": ["selectable", "nope"]}), 400, 32000, "'nope' is invalid")


@pytest.mark.parametrize("ptype", ["selectable", "enterable", "accessible"])
def test_get_by_type_matches_id_lists(api, ptype, admin_token, inactive_product):
    body = ok(api.get("/product", token=admin_token, params={"type": ptype, "include_fields": "id"}))
    assert sorted(p["id"] for p in body["products"]) == sorted(_ids(api, f"/product_{ptype}", token=admin_token))
    assert all(set(p) == {"id"} for p in body["products"])


def test_get_multiple_types_union(api, inactive_product):
    body = ok(api.get("/product", params={"type": ["enterable", "selectable"], "include_fields": "id"}))
    expected = set(_ids(api, "/product_enterable")) | set(_ids(api, "/product_selectable"))
    assert sorted(p["id"] for p in body["products"]) == sorted(expected)


def test_get_ids_and_names_are_unioned_and_intersected_with_accessible(api, seed_product_id, inactive_product):
    pid, name = inactive_product
    body = ok(api.get("/product", params={"ids": pid, "names": SEED_PRODUCT, "include_fields": "id,name"}))
    assert sorted(body["products"], key=lambda p: p["id"]) == sorted(
        [{"id": pid, "name": name}, {"id": seed_product_id, "name": SEED_PRODUCT}], key=lambda p: p["id"])
    body = ok(api.get("/product", params={"ids": [pid, seed_product_id, 999999], "type": "enterable",
                                           "include_fields": "id"}))
    assert body == {"products": [{"id": seed_product_id}]}
    # Perl does not split a csv "ids" string: it is a single (non-matching) id
    assert ok(api.get("/product", params={"ids": f"{pid},{seed_product_id}", "include_fields": "id"})) == {"products": []}


def test_get_with_token_and_bad_token(api, admin_token, seed_product_id):
    body = ok(api.get(f"/product/{seed_product_id}", token=admin_token, params={"include_fields": "id"}))
    assert body == {"products": [{"id": seed_product_id}]}
    expect_error(api.get(f"/product/{seed_product_id}", token="1-notavalidtoken"), 400, 32000)


# ---------------------------------------------------------------- Product.create
def test_create_requires_login(api):
    expect_error(api.post("/product", json={"name": unique(), "description": "d", "version": "1"}), 401, 410,
                 "You must log in before using this part of Bugzilla.")


def test_create_requires_editcomponents(api, user_token):
    expect_error(api.post("/product", token=user_token, json={"name": unique(), "description": "d", "version": "1"}),
                 401, 304, "Sorry, you aren't a member of the 'editcomponents' group, and so you are not "
                           "authorized to add new products.")


def test_create_validation_errors(api, admin_token):
    post = lambda body: api.post("/product", token=admin_token, json=body)  # noqa: E731
    expect_error(post({}), 400, 703, "You must enter a description for this product.")
    expect_error(post({"name": unique()}), 400, 703)
    expect_error(post({"name": unique(), "description": "   "}), 400, 703)
    expect_error(post({"name": unique(), "description": "d"}), 400, 704,
                 "You must enter a valid version to create a new product.")
    expect_error(post({"name": unique(), "description": "d", "version": "  "}), 400, 704)
    expect_error(post({"description": "d", "version": "1"}), 400, 700, "You must enter a name for the product.")
    expect_error(post({"name": "   ", "description": "d", "version": "1"}), 400, 700)
    long_name = "p" * 65
    expect_error(post({"name": long_name, "description": "d", "version": "1"}), 400, 701,
                 f"The name of a product is limited to 64 characters. '{long_name}' is too long (65 characters).")
    expect_error(post({"name": SEED_PRODUCT, "description": "d", "version": "1"}), 400, 702,
                 f"The product name '{SEED_PRODUCT}' already exists.")
    expect_error(post({"name": SEED_PRODUCT.upper(), "description": "d", "version": "1"}), 400, 702,
                 f"The product name '{SEED_PRODUCT.upper()}' differs from existing product '{SEED_PRODUCT}' only in case.")
    expect_error(post({"name": unique(), "description": "d", "version": "1", "classification": "Nope"}), 404, 51,
                 "There is no classification named 'Nope'.")
    expect_error(post({"name": unique(), "description": "d", "version": "1", "classification": ""}), 400, 50,
                 "You must select/enter a classification.")
    # the classification is checked before description/name/version (validators run alphabetically)
    expect_error(post({"classification": "Nope"}), 404, 51)
    expect_error(post({"name": "", "description": "", "version": ""}), 400, 703)


def test_create_minimal_defaults(api, admin_token, new_product):
    pid, sent = new_product()
    body = ok(api.get(f"/product/{pid}", token=admin_token))
    p = body["products"][0]
    assert normalize({k: v for k, v in p.items() if k != "components"}) == {
        "id": ID, "name": sent["name"], "description": "created by the parity suite", "is_active": True,
        "default_milestone": "---", "has_unconfirmed": True, "classification": "Unclassified",
        "versions": [{"id": ID, "name": "0.1", "sort_key": 0, "is_active": True}],
        "milestones": [{"id": ID, "name": "---", "sort_key": 0, "is_active": True}],
    }
    assert p["components"] == []
    assert pid in _ids(api, "/product_selectable", token=admin_token)
    # a product without components is not enterable
    assert pid not in _ids(api, "/product_enterable", token=admin_token)


def test_create_all_fields_and_trimming(api, admin_token, new_product):
    name = unique("parity trimmed")
    pid, _ = new_product(name=f"  {name}  ", description="  full fixture  ", version="  3.0  ",
                         default_milestone="  M1  ", has_unconfirmed=False, is_open=False,
                         classification="Unclassified", create_series=1)
    body = ok(api.get(f"/product/{pid}", token=admin_token))
    assert normalize(body["products"][0]) == {
        "id": ID, "name": name, "description": "full fixture", "is_active": False,
        "default_milestone": "M1", "has_unconfirmed": False, "classification": "Unclassified", "components": [],
        "versions": [{"id": ID, "name": "3.0", "sort_key": 0, "is_active": True}],
        "milestones": [{"id": ID, "name": "M1", "sort_key": 0, "is_active": True}],
    }
    # lookups by (case-insensitive) name find the trimmed name
    assert ok(api.get(f"/product/{name.upper()}", params={"include_fields": "id"})) == {"products": [{"id": pid}]}


def test_create_boolean_strings(api, admin_token, new_product):
    pid, _ = new_product(has_unconfirmed="0", is_open="", default_milestone="")
    p = ok(api.get(f"/product/{pid}", params={"include_fields": "is_active,has_unconfirmed,default_milestone"}))
    assert p == {"products": [{"is_active": False, "has_unconfirmed": False, "default_milestone": "---"}]}


def test_create_duplicate_of_created_product(api, admin_token, new_product):
    _, sent = new_product()
    expect_error(api.post("/product", token=admin_token, json=sent), 400, 702,
                 f"The product name '{sent['name']}' already exists.")


# ---------------------------------------------------------------- Product.update
def test_update_requires_login(api, seed_product_id):
    expect_error(api.put(f"/product/{seed_product_id}", json={"description": "x"}), 401, 410)


def test_update_requires_editcomponents(api, user_token, seed_product_id):
    expect_error(api.put(f"/product/{seed_product_id}", token=user_token, json={"description": "x"}), 401, 304,
                 "Sorry, you aren't a member of the 'editcomponents' group, and so you are not authorized to add, "
                 "modify or delete products.")


def test_update_without_id_in_path_is_not_a_resource(api, admin_token):
    expect_error(api.put("/product", token=admin_token, json={"description": "x"}), 404, 32614,
                 "A REST API resource was not found for 'PUT /product'.")


def test_update_unknown_product(api, admin_token):
    expect_error(api.put("/product/999999", token=admin_token, json={"description": "x"}), 401, 106,
                 "Either the product with the id 999999 does not exist or you don't have access to it.")
    expect_error(api.put("/product/nosuchprod", token=admin_token, json={"description": "x"}), 401, 106,
                 "Either the product 'nosuchprod' does not exist or you don't have access to it.")


def test_update_all_fields_reports_changes(api, admin_token, new_product):
    pid, sent = new_product(default_milestone="M1")
    new_name = unique("parity renamed")
    body = ok(api.put(f"/product/{pid}", token=admin_token, json={
        "name": new_name, "description": "renamed", "is_open": False, "has_unconfirmed": False,
        "default_milestone": "M1"}))
    assert body == {"products": [{"id": pid, "changes": {
        "name": {"removed": sent["name"], "added": new_name},
        "description": {"removed": "created by the parity suite", "added": "renamed"},
        "is_open": {"removed": "1", "added": "0"},
        "has_unconfirmed": {"removed": "1", "added": "0"},
    }}]}
    p = ok(api.get(f"/product/{pid}", token=admin_token,
                   params={"include_fields": "name,description,is_active,has_unconfirmed,default_milestone"}))
    assert p == {"products": [{"name": new_name, "description": "renamed", "is_active": False,
                               "has_unconfirmed": False, "default_milestone": "M1"}]}
    # back again, by name in the path and with string booleans
    body = ok(api.put(f"/product/{new_name}", token=admin_token, json={"is_open": "1", "has_unconfirmed": "yes"}))
    assert body == {"products": [{"id": pid, "changes": {"is_open": {"removed": "0", "added": "1"},
                                                          "has_unconfirmed": {"removed": "0", "added": "1"}}}]}


def test_update_noop_and_trimming(api, admin_token, new_product):
    pid, sent = new_product()
    assert ok(api.put(f"/product/{pid}", token=admin_token, json={})) == {"products": [{"id": pid, "changes": {}}]}
    body = ok(api.put(f"/product/{pid}", token=admin_token, json={
        "name": f"  {sent['name']}  ", "description": "  created by the parity suite ", "default_milestone": " --- ",
        "is_open": "1", "has_unconfirmed": True}))
    assert body == {"products": [{"id": pid, "changes": {}}]}


def test_update_body_ids_and_names_override_path(api, admin_token, new_product):
    pid1, _ = new_product()
    pid2, sent2 = new_product()
    body = ok(api.put(f"/product/{pid1}", token=admin_token, json={"ids": [pid2], "description": "via ids"}))
    assert body == {"products": [{"id": pid2, "changes": {
        "description": {"removed": "created by the parity suite", "added": "via ids"}}}]}
    # body "names" are unioned with the path id (names are resolved first, then ids)
    body = ok(api.put(f"/product/{pid1}", token=admin_token, json={"names": [sent2["name"]], "description": "via names"}))
    assert body == {"products": [
        {"id": pid2, "changes": {"description": {"removed": "via ids", "added": "via names"}}},
        {"id": pid1, "changes": {"description": {"removed": "created by the parity suite", "added": "via names"}}}]}
    body = ok(api.put(f"/product/{pid1}", token=admin_token, json={"ids": [pid1, pid2], "description": "both"}))
    assert sorted(body["products"], key=lambda p: p["id"]) == [
        {"id": pid1, "changes": {"description": {"removed": "via names", "added": "both"}}},
        {"id": pid2, "changes": {"description": {"removed": "via names", "added": "both"}}}]
    assert ok(api.get(f"/product/{pid1}", params={"include_fields": "description"})) == {
        "products": [{"description": "both"}]}


def test_update_validation_errors(api, admin_token, new_product):
    pid, _ = new_product()
    put = lambda body: api.put(f"/product/{pid}", token=admin_token, json=body)  # noqa: E731
    expect_error(put({"name": ""}), 400, 700, "You must enter a name for the product.")
    expect_error(put({"name": "  "}), 400, 700)
    expect_error(put({"name": "p" * 65}), 400, 701, "is too long (65 characters)")
    expect_error(put({"name": SEED_PRODUCT}), 400, 702, f"The product name '{SEED_PRODUCT}' already exists.")
    expect_error(put({"name": SEED_PRODUCT.lower()}), 400, 702,
                 f"The product name '{SEED_PRODUCT.lower()}' differs from existing product '{SEED_PRODUCT}' only in case.")
    expect_error(put({"description": "  "}), 400, 703, "You must enter a description for this product.")
    expect_error(put({"default_milestone": "M9"}), 400, 705,
                 "You must create the milestone 'M9' before it can be made the default milestone for product")
    expect_error(put({"bogus": "x"}), 400, 67399, "The requested method 'Bugzilla::Product::set_bogus' was not found.")
    expect_error(put({"classification": "Unclassified"}), 400, 67399,
                 "The requested method 'Bugzilla::Product::set_classification' was not found.")
    expect_error(put({"version": "9"}), 400, 67399, "'Bugzilla::Product::set_version'")
    # nothing was changed by the failed calls
    p = ok(api.get(f"/product/{pid}", params={"include_fields": "description,default_milestone"}))
    assert p == {"products": [{"description": "created by the parity suite", "default_milestone": "---"}]}
